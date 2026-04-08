import time
import re
import json
import operator
import asyncio
import uuid
from typing import TypedDict, List, Annotated, Optional, Union

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
    ToolMessage
)
from langgraph.graph import StateGraph, START, END

from config.settings import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME
from config.prompt import EXPERT_PROMPT, PERSONA_PROMPT


# =========================
# 1. 状态定义
# =========================
class AgentState(TypedDict):
    input: str
    user_id: str
    chat_id: str
    # Annotated 确保消息会自动累加而不是覆盖
    messages: Annotated[List[BaseMessage], operator.add]
    context_data: str
    raw_response: str
    reply: str
    sources: List[str]
    plan: Optional[dict]
    step_count: int
    last_tool: str


# =========================
# 2. 核心图逻辑
# =========================
class HerbGraph:
    def __init__(self, vector_manager, redis_memory, mcp_manager=None, skill_manager=None):
        self.vm = vector_manager
        self.redis_mem = redis_memory
        self.mcp = mcp_manager
        self.sm = skill_manager

        # 低温模型用于逻辑决策
        common_conf = {
            "model": MODEL_NAME,
            "openai_api_key": OPENAI_API_KEY,
            "openai_api_base": OPENAI_BASE_URL,
            "temperature": 0
        }

        self.planner_llm = ChatOpenAI(**common_conf)
        self.critic_llm = ChatOpenAI(**common_conf)

        # 高温模型用于人格化表达
        self.gen_llm = ChatOpenAI(
            model=MODEL_NAME,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            temperature=0.7
        )

        self.app = self._build_graph()

    # --- 节点：预处理 ---
    async def pre_process(self, state: AgentState):
        mem_data = await self.redis_mem.get_user_summary(state["user_id"])
        profile = ", ".join([f"{k}:{v}" for k, v in mem_data.get("profile", {}).items()])
        facts = " | ".join(mem_data.get("facts", []))

        context = f"[记忆]\n用户画像: {profile}\n事实: {facts}\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"

        return {
            "context_data": context,
            "sources": [],
            "step_count": 0,
            "last_tool": ""
        }

    # --- 节点：规划器 (Planner) ---
    async def planner(self, state: AgentState):
        # 1. 获取本地工具 (SkillManager)
        print(f"DEBUG: 正在进入 Planner 节点, self.mcp 是否存在: {self.mcp is not None}")

        mcp_tools_desc = "无远程 MCP 工具"
        if self.mcp:
            try:
                # 显式等待，防止僵死
                mcp_schemas = await asyncio.wait_for(self.mcp.get_tool_schemas(), timeout=5.0)
                if mcp_schemas:
                    mcp_tools_desc = json.dumps(mcp_schemas, ensure_ascii=False)
                    print(f"DEBUG: 成功加载 MCP 工具 -> {[t['function']['name'] for t in mcp_schemas]}")
                else:
                    print("DEBUG: MCP 已连接但工具清单为空")
            except Exception as e:
                print(f"DEBUG: [Planner] 获取 MCP Schema 失败: {e}")

        local_tools_desc = self.sm.get_tools_instruction() if self.sm else "无本地可用工具"
        system_prompt = f"""你是一个名为 Herb 的决策核心。你必须分析上下文、记忆和可用工具，并输出 JSON 格式的决策。

### 可用工具资源：
1. **本地 Skill 工具**（用于提醒、计算等）：
{local_tools_desc}

2. **远程 MCP 工具**（用于实时联网搜索、网页抓取等）：
{mcp_tools_desc}

### 输出 JSON 格式：
{{
  "thought": "简短的思考过程",
  "action": "tool | rag | mcp | final",
  "tool_name": "具体调用的工具函数名",
  "tool_args": {{ "参数名": "参数值" }}
}}

### 决策规则：
1. **action: rag** -> 涉及校园私有知识（老师简介、办公电话、规章制度、校园新闻等）。
2. **action: tool** -> 使用上述【本地 Skill 工具】列表中的功能（如设置闹钟 set_reminder）。
3. **action: mcp** -> 需要联网搜索实时信息、抓取特定网页内容或使用【远程 MCP 工具】。
4. **action: final** -> 任务已完成、简单的日常闲聊或直接回答已知问题。

当前系统时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
"""

        # 3. 调用 LLM
        res = await self.planner_llm.ainvoke([
            SystemMessage(content=system_prompt),
            *state["messages"][-10:]  # 保持对话上下文
        ])

        # 4. 解析 JSON
        try:
            # 兼容模型可能输出的 Markdown 代码块
            clean_json = re.sub(r'```json|```', '', res.content).strip()
            plan = json.loads(clean_json)
        except Exception as e:
            print(f"[Planner Error] 解析 JSON 失败: {e}")
            plan = {"action": "final", "thought": "解析异常，转为直接回答", "reply": res.content}

        # 5. 核心逻辑：构造符合协议的消息体
        action = plan.get("action")

        # 处理需要调用工具的情况 (本地 tool 或 远程 mcp)
        if action in ["tool", "mcp"]:
            tool_name = plan.get("tool_name")
            tool_args = plan.get("tool_args", {})

            # 生成唯一的 Call ID 供后续 ToolMessage 绑定
            call_id = f"call_{action}_{uuid.uuid4().hex[:10]}"
            plan["pending_tool_call_id"] = call_id

            # 构造符合 OpenAI 规范的 AIMessage
            # 必须包含 tool_calls 字段，否则 LangChain 后续节点会报错
            ai_message = AIMessage(
                content=res.content,
                tool_calls=[{
                    "id": call_id,
                    "name": tool_name,
                    "args": tool_args
                }]
            )
            return {"plan": plan, "messages": [ai_message]}

        # 处理 RAG 或 直接回答的情况
        return {"plan": plan, "messages": [res]}

    # --- 节点：工具执行 ---
    async def tool_node(self, state: AgentState):
        plan = state["plan"]
        name = plan.get("tool_name")
        args = plan.get("tool_args", {})
        call_id = plan.get("pending_tool_call_id", f"fake_{uuid.uuid4().hex[:8]}")

        handler = self.sm.get_handler(name) if self.sm else None
        try:
            if handler:
                result = await handler(**args) if asyncio.iscoroutinefunction(handler) else handler(**args)
                content = json.dumps(result, ensure_ascii=False)
                wrapped = f"\n<DATA_BLOCK type='{name}'>\n{content}\n</DATA_BLOCK>\n"
            else:
                wrapped = f"⚠️ 错误：未找到工具 {name}"
        except Exception as e:
            wrapped = f"❌ 工具执行异常：{str(e)}"

        return {
            "messages": [ToolMessage(content=wrapped, tool_call_id=call_id)],
            "last_tool": name,
            "step_count": state["step_count"] + 1
        }

    # --- 节点：RAG 专家 ---
    async def campus_expert(self, state: AgentState):
        print(f"\n[RAG 诊断] 原始输入: {state['input']}")

        # 1. 关键词提炼 (让 AI 把 "你知道艾勇老师吗" 变成 "艾勇")
        extract_prompt = f"请从以下用户问题中提取最适合搜索的 1-2 个关键词（人名、机构名或核心概念），只输出关键词，不要解释：\n问题：{state['input']}"
        search_query_res = await self.gen_llm.ainvoke(extract_prompt)
        search_query = search_query_res.content.strip().replace(" ", "")

        print(f"[RAG 诊断] 提炼后的检索词: {search_query}")

        # 2. 执行检索
        # 注意：确保你的 vm.query 内部没有写死 user_id 的过滤逻辑（如果1.pdf是公共文档）
        docs = await self.vm.query(search_query, state["user_id"])

        if not docs:
            print(f"⚠️ [RAG 诊断] 向量库返回为空！检索词: {search_query}")
            return {"raw_response": "抱歉，档案库里没翻到相关内容。", "sources": []}

        print(f"✅ [RAG 诊断] 检索成功，匹配到 {len(docs)} 条相关片段")

        knowledge = "\n".join([f"[{i + 1}] {d.page_content}" for i, d in enumerate(docs)])
        wrapped = f"\n<DATA_BLOCK type='knowledge'>\n{knowledge}\n</DATA_BLOCK>\n"
        sources = list(set([d.metadata.get("source", "Herb知识库") for d in docs]))

        return {"raw_response": wrapped, "sources": sources}

    async def mcp_node(self, state: AgentState):
        plan = state["plan"]
        tool_name = plan.get("tool_name")
        tool_args = plan.get("tool_args", {})
        call_id = plan.get("pending_tool_call_id", f"mcp_{uuid.uuid4().hex[:8]}")

        print(f"  > [MCP 执行] 正在调用: {tool_name}")

        try:
            if not self.mcp:
                content = "❌ 错误：MCP 管理器未初始化，无法调用工具。"
            else:
                # 实际调用
                result_text = await self.mcp.call_tool(tool_name, tool_args)
                content = f"工具 {tool_name} 返回结果：\n{result_text}"
        except Exception as e:
            content = f"❌ MCP 执行异常：{str(e)}"

        wrapped = f"\n<DATA_BLOCK type='mcp_tool' name='{tool_name}'>\n{content}\n</DATA_BLOCK>\n"

        # 必须回传消息，否则 Critic 和 Planner 会失去上下文
        return {
            "messages": [ToolMessage(content=content, tool_call_id=call_id)],
            "raw_response": wrapped,
            "last_tool": tool_name,
            "step_count": state["step_count"] + 1
        }
    # --- 节点：审查者 (Critic) ---
    async def critic(self, state: AgentState):
        if state["step_count"] > 6:
            return {"plan": {"action": "final"}}

        # 获取最后一条工具返回的消息
        last_msg = state["messages"][-1].content
        prompt = f"基于以下最新获取的信息，判断是否足以回答用户：'{state['input']}'\n信息：{last_msg}\n只需回答：'continue' 或 'final'"

        res = await self.critic_llm.ainvoke(prompt)
        decision = "tool" if "continue" in res.content.lower() else "final"
        return {"plan": {"action": decision}}

    # --- 节点：人格化染色 (Persona) ---
    async def herb_persona(self, state: AgentState):
        # 确定原始素材
        raw = state.get("raw_response") or (state["messages"][-1].content if state["messages"] else "")

        persona_prompt = f"""
        ### 任务背景
        用户当前问题："{state['input']}"
        上下文记忆：{state['context_data']}
        原始素材池：{raw}

        ### 执行准则（表现层控制）
        你现在是 Herb，一个高智商、说话带刺但极其严谨的 AI。请根据“原始素材池”构造回复，并严格遵守以下过滤规则：

        1. **逻辑锚定与实体隔离（最高优先级）**：
           - **严格匹配**：在素材中精准定位用户询问的姓名（如“吴超”）。
           - **后向抓取原则**：关于该人物的属性（如邮箱、电话、职称），【必须】在该人名出现之后、下一个数字序号（如 45.）出现之前提取。
           - **禁区校验**：绝对禁止向上抓取！如果某个邮箱出现在人名“吴超”的上方（属于王新年），哪怕距离再近也视为干扰项，严禁调用。
           - **精准剪枝**：素材中所有非 "{state['input']}" 相关的人物资料必须物理删除，不得在回复中露出一丁点儿。

        2. **数据提取规范**：
           - **原样搬运**：提取相关数据时，保持文字原始性（如邮箱地址 wind0101880@126.com 必须逐字核对，禁止脑补）。
           - **静默输出**：禁止显示任何 `<DATA_BLOCK>` 标签。
           -如定时器等任务一定要保留原任务名称

        3. **响应构造结构**：
           - [开场白]：Herb 风格的骚气引言（如：老板，你要的“情报”我给你从档案库里拽出来了...）。
           - [数据展示]：清晰列出与 "{state['input']}" 相关的纯净数据。
           - [吐槽位]：针对数据内容进行 1-2 句辛辣、符合人设的评论（如：研究激光的，这邮箱名字听着就很“快”）。

        4. **防御与修正机制**：
           - 如果上下文显示用户在纠正你（如“我只问了XX”），说明你之前的检索产生了污染。这一轮必须表现得极度克制，取消所有废话，只输出最精准的目标数据。

        排版要求：各部分之间仅保留一个空行。
        """
        res = await self.gen_llm.ainvoke(persona_prompt)

        # 清洗可能残留的标签
        reply = re.sub(r'</?DATA_BLOCK[^>]*>', '', res.content).strip()

        # 只有当确实有来源且回复不为空时才添加
        if state.get("sources") and len(reply) > 5:
            reply += "\n\n📚 来源：" + " | ".join(state["sources"])

        return {"reply": reply}

    async def memorize(self, state: AgentState):
        # 异步存储用户行为
        user_id = state["user_id"]
        # 1. 存入本次行为（碎片）
        await self.redis_mem.store_fact(user_id, f"询问了: {state['input'][:15]}", importance="low")

        # 2. 尝试触发归纳 (这里用 gen_llm 这种比较便宜的模型)
        # 建议异步跑，别卡住主流程
        asyncio.create_task(self.redis_mem.consolidate_if_needed(user_id, self.gen_llm))

        return state

    # =========================
    # 3. 路由与构建
    # =========================
        # 1. 修改路由逻辑
    def _route(self, state: AgentState):
        action = state["plan"].get("action", "final")
        # 如果 planner 输出的是 mcp，确保路由能识别
        return action

    # 2. 修改图构建逻辑
    def _build_graph(self):
        builder = StateGraph(AgentState)

        builder.add_node("pre", self.pre_process)
        builder.add_node("planner", self.planner)
        builder.add_node("tool", self.tool_node)
        builder.add_node("rag", self.campus_expert)
        builder.add_node("mcp", self.mcp_node)  # <-- 新增节点
        builder.add_node("critic", self.critic)
        builder.add_node("persona", self.herb_persona)
        builder.add_node("memory", self.memorize)

        builder.add_edge(START, "pre")
        builder.add_edge("pre", "planner")

        # 修改条件路由
        builder.add_conditional_edges(
            "planner",
            self._route,
            {
                "tool": "tool",
                "rag": "rag",
                "mcp": "mcp",  # <-- 新增路由：去网页抓取
                "final": "persona"
            }
        )

        # MCP 抓完后去哪里？建议去 critic 让它看看抓到的内容够不够
        builder.add_edge("mcp", "critic")

        # 保持其他的连线不变...
        builder.add_edge("tool", "critic")
        builder.add_conditional_edges(
            "critic",
            self._route,
            {
                "tool": "planner",
                "mcp": "mcp",  # <-- 如果 Critic 觉得抓得不够，可以回去再抓
                "final": "persona"
            }
        )

        builder.add_edge("rag", "persona")
        builder.add_edge("persona", "memory")
        builder.add_edge("memory", END)

        return builder.compile()

    async def run(self, user_input: str, user_id: str, chat_id="private"):
        state = {
            "input": user_input,
            "user_id": user_id,
            "chat_id": chat_id,
            "messages": [HumanMessage(content=user_input)],
            "step_count": 0,
            "sources": []
        }

        print(f"\n{'=' * 20} 🚀 Herb Agent 开始执行 {'=' * 20}")
        print(f"用户输入: {user_input}")

        final_state = state
        try:
            # 遍历图执行过程中的每一个事件
            async for event in self.app.astream(state, config={"recursion_limit": 35}):
                for node_name, node_output in event.items():
                    print(f"\n[进入节点]: 🧠 {node_name}")

                    # 1. 如果是 Planner，打印它的思考过程和决策
                    if node_name == "planner":
                        plan = node_output.get("plan", {})
                        print(f"  > 思考: {plan.get('thought')}")
                        print(f"  > 决策: {plan.get('action')} | 工具: {plan.get('tool_name')}")

                    # 2. 如果是 Tool，打印工具执行结果
                    elif node_name == "tool":
                        last_msg = node_output["messages"][-1].content
                        print(f"  > 工具结果: {last_msg[:200]}...")  # 只打印前200字防止刷屏

                    # 3. 如果是 Critic，打印审查结论
                    elif node_name == "critic":
                        decision = node_output.get("plan", {}).get("action")
                        print(f"  > 审查结论: {decision}")

                    # 4. 如果是 Persona，打印最终润色前的状态
                    elif node_name == "persona":
                        print(f"  > 正在进行 Herb 人格化染色...")

                    # 更新最终状态快照
                    final_state.update(node_output)

            print(f"\n{'=' * 20} ✅ 执行完成 {'=' * 20}\n")

        except Exception as e:
            import traceback
            print(f"\n❌ [执行异常]:")
            traceback.print_exc()
            return f"❌ Herb 核心引擎故障: {str(e)}"

        return final_state.get("reply", "Herb 思考得太深，断线了。")
