import time
import re
import asyncio
import json
import os
from core.memory import RedisMemory
from pathlib import Path
from typing import TypedDict, List, Optional, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage
)

from config.settings import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME
from config.prompt import CHARACTER_PROMPT


class AgentState(TypedDict):
    input: str
    history: List[dict]
    messages: Annotated[List[BaseMessage], operator.add]
    context: str
    sources: List[str]  # 新增：用于存储检索来源
    user_id: str
    chat_id: str
    intent: str
    tool_calls: List[dict]
    reply: Optional[str]
    retry_count: int
    critique_feedback: str


class HerbGraph:
    def __init__(self, vector_manager, mcp_manager, skill_manager, all_tools):
        self.vm = vector_manager
        self.mcp = mcp_manager
        self.sm = skill_manager

        # --- 记忆管理配置 (进阶逻辑) ---
        self.TRANSCRIPT_DIR = Path("./data/transcripts")
        self.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        self.KEEP_RECENT_TOOLS = 3  # 保留最近3个工具执行原文
        self.TOKEN_THRESHOLD = 8000  # 字符数触发总结的阈值
        self.KEEP_RECENT = 6
        self.redis_mem = RedisMemory(host='localhost', port=6379,password="123456")

        self.decision_llm = ChatOpenAI(
            model=MODEL_NAME,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            temperature=0
        ).bind_tools(all_tools)

        self.gen_llm = ChatOpenAI(
            model=MODEL_NAME,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            temperature=0.7
        )

        self.app = self._build_graph()

    # 记忆管理核心方法

    def _micro_compact(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        Layer 1: 占位符替换。将旧的 ToolMessage 内容替换，只保留结构。
        """
        tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]

        if len(tool_indices) <= self.KEEP_RECENT_TOOLS:
            return messages

        for idx in tool_indices[:-self.KEEP_RECENT_TOOLS]:
            msg = messages[idx]
            if len(msg.content) > 100:
                # 提取工具ID作为标识，替换为占位符
                t_id = getattr(msg, "tool_call_id", "unknown")
                msg.content = f"[Previous: Tool result for ID {t_id[:8]} has been archived to save memory.]"

        return messages

    async def critique(self,state: AgentState):
        """
        自省节点：扮演审核员，检查 generate 节点的输出
        """
        last_reply = state["reply"]
        user_input = state["input"]
        context = state.get("context", "无参考资料")

        critique_prompt = (
            "你是一个AI 回答审核员。请评价以下回答：\n\n"
            f"用户问题：{user_input}\n"
            f"参考资料：{context}\n"
            f"AI 的回答：{last_reply}\n\n"
            "检查标准：\n"
            "1. 是否符合价值观？\n"
            "如果合格，请只输出 [PASS]。\n"
            "如果不合格，请详细说明改进意见，不要输出 [PASS]。"
        )

        res = await self.gen_llm.ainvoke(critique_prompt)
        feedback = res.content.strip()

        return {
            "critique_feedback": feedback,
            "retry_count": state.get("retry_count", 0) + 1
        }

    def critique_router(self,state: AgentState):
        """
        自省路由：判断是直接结束还是回去重写
        """
        feedback = state.get("critique_feedback", "")
        retry = state.get("retry_count", 0)

        # 强制出口：如果重试了 2 次还没过，或者审核员说了 [PASS]
        if "[PASS]" in feedback or retry >= 2:
            return END
        return "generate"

    async def _auto_compact(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        # 修复变量名错误：使用 self.KEEP_RECENT
        if len(messages) <= self.KEEP_RECENT:
            return messages

        to_summarize = messages[:-self.KEEP_RECENT]
        keep_intact = messages[-self.KEEP_RECENT:]

        # 这里的打印很有帮助，保留它
        print(f"[*] 触发压缩：保留最近{len(keep_intact)}条...")

        history_text = ""
        for m in to_summarize:
            # 增加对 ToolMessage 的简单处理，防止摘要全是 ID
            role = "User" if isinstance(m, HumanMessage) else "AI"
            content = m.content[:200] if not isinstance(m, ToolMessage) else "[工具执行结果]"
            history_text += f"{role}: {content}\n"

        summary_prompt = f"请简练总结以下对话背景：\n{history_text}"

        try:
            # 注意这里：用 gen_llm 是对的，因为它不需要 bind_tools
            response = await self.gen_llm.ainvoke(summary_prompt)
            return [
                SystemMessage(content=f"[历史背景摘要]: {response.content}"),
                *keep_intact
            ]
        except Exception as e:
            print(f"压缩失败: {e}")
            return messages

    def _get_router_logic(self):

        def router_func(state: AgentState):
            # 获取在 analyze 节点中设置的 intent
            intent = state.get("intent", "chat")
            print(f"[Router] 决策路径: {intent}")

            if intent == "tool":
                return "execute_tool"
            if intent == "rag":
                return "retrieve"
            return "generate"

        return router_func

    def _build_graph(self):
        builder = StateGraph(AgentState)

        # 1. 注册所有节点
        builder.add_node("analyze", self.analyze)
        builder.add_node("execute_tool", self.execute_tool)
        builder.add_node("retrieve", self.retrieve)
        builder.add_node("generate", self.generate)
        builder.add_node("critique", self.critique)
        builder.add_node("memorize", self.memorize)  # 记忆沉淀节点

        # 2. 起点
        builder.add_edge(START, "analyze")

        # 3. 意图分流
        builder.add_conditional_edges(
            "analyze",
            self._get_router_logic()
        )

        # 4. 汇聚到生成
        builder.add_edge("execute_tool", "generate")
        builder.add_edge("retrieve", "generate")

        # 5. 生成后进行反思审核
        builder.add_edge("generate", "critique")

        # 6. 【关键逻辑重构】：反思路由
        builder.add_conditional_edges(
            "critique",
            lambda state: (
                "memorize"  # 如果通过或达到重试上限，先去记笔记
                if "[PASS]" in state.get("critique_feedback", "") or state.get("retry_count", 0) >= 2
                else "generate"  # 没通过则回去重写
            )
        )

        # 7. 记完笔记后正式结束
        builder.add_edge("memorize", END)

        return builder.compile()
    async def analyze(self, state: AgentState):
        user_id = state["user_id"]
        chat_id = state["chat_id"]
        user_input = state["input"].lower()
        current_time = time.strftime('%Y-%m-%d %H:%M:%S')

        # =========================================================
        # 0. 核心增强：从 Redis 唤醒长期记忆 (Level 3 Memory)
        # =========================================================
        # 假设你已经将 RedisMemory 实例挂载在 self.redis_mem 上
        memory_data = await self.redis_mem.get_user_summary(user_id)
        profile = memory_data.get("profile", {})
        facts = memory_data.get("facts", [])

        # 将画像和事实格式化为字符串，用于注入 Prompt
        profile_str = ", ".join([f"{k}:{v}" for k, v in profile.items()]) if profile else "未知"
        facts_str = " | ".join(facts) if facts else "尚无记录"

        # 构造长期记忆背景
        long_term_memory_context = (
            f"\n[长期记忆唤醒]\n"
            f"👤 用户画像: {profile_str}\n"
            f"📌 关键事实: {facts_str}\n"
        )

        time_keywords = ["分钟", "秒", "小时", "点", "钟", "半"]
        action_keywords = ["提醒", "闹钟", "叫我", "记得", "定时"]
        if any(tk in user_input for tk in time_keywords) and any(ak in user_input for ak in action_keywords):
            print("[Graph] ⏰ 命中硬编码逻辑：强制触发 set_reminder")
            time_num_match = re.search(r'(\d+|一|二|两|三|五|十)', user_input)
            time_val = time_num_match.group(1) if time_num_match else "1"
            unit = "秒" if "秒" in user_input else "小时" if "小时" in user_input else "分钟"
            mapping = {"一": "1", "二": "2", "两": "2", "三": "3", "五": "5", "十": "10"}
            time_val = mapping.get(time_val, time_val)

            tool_call = {
                "name": "set_reminder",
                "args": {"duration_str": f"{time_val}{unit}", "task": user_input},
                "id": f"timer_{int(time.time())}"
            }
            return {
                "intent": "tool",
                "messages": [AIMessage(content=f"没问题，这就定个 {time_val}{unit} 的闹钟。", tool_calls=[tool_call])],
                "tool_calls": [tool_call],
                "memory_context": long_term_memory_context
            }

        if any(kw in user_input for kw in ["热搜", "微博", "瓜"]):
            print("[Graph] ⚠️ 命中硬拦截：强制触发微博热搜")
            tool_call = {
                "name": "get_weibo_hot_search",
                "args": {},
                "id": f"force_weibo_{int(time.time())}"
            }
            return {
                "intent": "tool",
                "messages": [AIMessage(content="正在为你打探微博热搜...", tool_calls=[tool_call])],
                "tool_calls": [tool_call],
                "memory_context": long_term_memory_context
            }

        # ---------------------------------------------------------
        # 3. 意图决策：注入长期记忆
        # ---------------------------------------------------------
        weather_trigger = any(kw in user_input for kw in ["天气", "气温", "下雨", "多少度", "冷不冷"])
        weather_instruction = ""
        if weather_trigger:
            weather_instruction = (
                "\n【紧急任务：天气查询】你必须调用 'get_weather' 工具，提取纯净城市名。"
            )

        # 将记忆注入 System Prompt，让决策 LLM 知道它在和谁对话
        system_prompt = (
            f"{CHARACTER_PROMPT}\n"
            f"当前时间: {current_time}\n"
            f"{long_term_memory_context}\n"  # <--- 记忆注入点
            f"任务：根据对话和记忆，判断意图：tool(实时/工具)、rag(校园知识)、chat(闲聊/情感)。"
        )

        # 只取最近6条进行推理，节省Token
        full_messages = [SystemMessage(content=system_prompt)] + state["messages"][-6:]

        try:
            res = await self.decision_llm.ainvoke(full_messages)

            # 情况 A：LLM 决定调用工具
            if res.tool_calls:
                for call in res.tool_calls:
                    if call["name"] == "get_weather":
                        raw_city = call["args"].get("city", "")
                        clean_city = re.sub(r"(查询|天气|今天|明天|现在的|山东|省|市)", "", str(raw_city)).strip()
                        call["args"]["city"] = clean_city if clean_city else "北京"

                print(f"[Graph] LLM 决策调用工具: {res.tool_calls[0]['name']}")
                return {"intent": "tool", "messages": [res], "tool_calls": res.tool_calls,"memory_context": long_term_memory_context}

            # 情况 B：LLM 漏掉工具但触发了天气硬补齐
            if weather_trigger:
                print("[Graph] ⚠️ LLM 漏掉工具，执行暴力补齐")
                fallback_city = user_input.replace("查询", "").replace("天气", "").strip()[:2]
                fallback_city = fallback_city if fallback_city else "北京"
                tool_call = {
                    "name": "get_weather",
                    "args": {"city": fallback_city},
                    "id": f"fix_weather_{int(time.time())}"
                }
                return {
                    "intent": "tool",
                    "tool_calls": [tool_call],
                    "messages": [AIMessage(content="这就去查查天气。", tool_calls=[tool_call])],
                    "memory_context": long_term_memory_context
                }

            # 情况 C：闲聊或 RAG
            rag_keywords = ["保研", "综测", "绩点", "饭卡", "宿舍", "食堂", "挂科", "学分"]
            # 如果问题里提到了记忆里的关键词，也优先走 RAG 或深度对话
            intent = "rag" if any(k in user_input for k in rag_keywords) else "chat"

            print(f"[Graph] 决策路径: {intent} (携带长期记忆)")
            return {
                "intent": intent,
                "tool_calls": [],
                "memory_context": long_term_memory_context  # 核心：将记忆传给 generate 节点
            }

        except Exception as e:
            print(f"❌ Analyze 异常: {e}")
            return {"intent": "chat", "memory_context": long_term_memory_context}
    async def execute_tool(self, state: AgentState):
        last_message = state["messages"][-1]
        if not (isinstance(last_message, AIMessage) and last_message.tool_calls):
            return {"intent": "chat"}

        tool_messages = []
        outputs = []

        # 标识是否触发了主动压缩
        triggered_compact = False

        for call in last_message.tool_calls:
            try:
                # 1. 统一执行入口
                if call["name"].startswith("puppeteer_"):
                    result = await self.mcp.call_tool(call["name"], call["args"])
                else:
                    result = await asyncio.to_thread(self.sm.execute, call["name"], call["args"])

                # 2. Layer 3 信号检测
                if result == "MEM_COMPACT_SIGNAL":
                    triggered_compact = True
                    continue  # 信号本身不作为 ToolMessage 展示给用户

                # 3. 正常结果收集
                outputs.append(str(result))
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

            except Exception as e:
                error_text = f"工具 {call['name']} 执行失败: {e}"
                outputs.append(error_text)
                tool_messages.append(ToolMessage(content=error_text, tool_call_id=call["id"]))

        # 4. 如果触发了压缩，执行特殊的返回逻辑
        if triggered_compact:
            print("[Graph] Herb 觉得脑子太乱了，触发主动记忆压缩...")
            # 注意：这里需要传入当前已有的 tool_messages，确保压缩包含本次调用的意图
            current_full_messages = state["messages"] + tool_messages
            compacted_messages = await self._auto_compact(current_full_messages)

            return {
                "messages": compacted_messages,
                "context": "Herb 刚刚整理了一下思绪（执行了深度记忆压缩），现在大脑非常清爽。",
                "intent": "chat",
                "reply": "呼... 刚才聊得太嗨，脑子有点乱，我刚把记忆整理了一下。咱们继续，刚才说到哪了？"
            }

        return {
            "messages": tool_messages,
            "context": "\n".join(outputs),
            "intent": "chat"
        }

    async def retrieve(self, state: AgentState):
        print(f"[Graph] 🔍 进入检索流程，当前输入: {state['input']}")

        # --- 1. 查询改写 (Query Rewrite) ---
        history_messages = state.get("messages", [])[-6:]  # 取最近几条消息
        history_text = ""
        for m in history_messages:
            role = "用户" if isinstance(m, HumanMessage) else "AI"
            history_text += f"{role}: {m.content[:50]}\n"

        rewrite_prompt = (
            "你是一个搜索关键词优化专家。请根据对话历史和当前问题，提取出 1-2 个最适合在知识库中搜索的专业关键词。"
            "要求：只需输出关键词，用空格隔开，不要有任何解释文字。\n\n"
            f"--- 对话历史 ---\n{history_text}"
            f"--- 当前问题 ---\n{state['input']}"
        )
        try:
            # 使用 gen_llm 进行快速改写
            rewrite_res = await self.gen_llm.ainvoke(rewrite_prompt)
            search_query = rewrite_res.content.strip().replace("\"", "").replace("'", "")
            print(f"[Graph] 🔄 查询改写成功: '{state['input']}' -> '{search_query}'")
        except Exception as e:
            print(f"⚠️ 查询改写失败，使用原句兜底: {e}")
            search_query = state["input"]
        # --- 2. 调用向量库 (已经是异步混合检索 + RRF + Rerank) ---
        docs = await self.vm.query(
            text=search_query,
            user_id=state["user_id"],
            chat_id=state["chat_id"],
            k=6  # 初始召回设定，内部会自动处理 RRF 和 Rerank
        )

        if not docs:
            print("[Graph] 知识库未命中任何相关信息")
            return {
                "context": "未在校园知识库中找到相关正式记录，建议按一般经验回答或引导用户咨询相关部门。",
                "sources": []
            }

        # --- 3. 格式化输出 ---
        context_parts, source_list = [], []
        for i, d in enumerate(docs):
            source_name = d.metadata.get("source", "未知来源")
            source_list.append(f"[{i + 1}] {source_name}")
            # 加上文档标题或来源，方便 LLM 引用
            context_parts.append(f"【参考资料 {i + 1} | 来源: {source_name}】\n{d.page_content}")

        print(f"[Graph] ✅ 检索完成，共获取 {len(docs)} 条深度上下文")

        return {
            "context": "\n\n".join(context_parts),
            "sources": source_list
        }

    async def generate(self, state: AgentState):
        """
        Herb 最终生成节点：集成长期记忆、RAG知识、工具结果与硬协议
        """
        feedback = state.get("critique_feedback", "")
        sources = state.get("sources", [])
        memory_context = state.get("memory_context", "尚无记录")

        # 1. 处理 RAG 知识库内容
        context_docs = state.get("context", [])
        if isinstance(context_docs, list):
            knowledge = "\n".join([f"资料[{i + 1}]: {d.page_content}" for i, d in enumerate(context_docs)])
        else:
            knowledge = str(context_docs).strip()

        tool_content = ""
        timer_protocol = ""
        is_weather_data = False

        # 2. 逆序扫描消息流，提取工具执行结果与定时器协议
        # 扫描最近 10 条，确保拿到最新的工具反馈
        for msg in reversed(state["messages"][-10:]):
            if isinstance(msg, ToolMessage) and msg.content:
                content = msg.content
                # 提取定时器协议字符串 [SEC:XXX]...】
                t_match = re.search(r"\[SEC:\d+\].*?】", content)
                if t_match and not timer_protocol:
                    timer_protocol = t_match.group(0)

                # 识别天气数据
                if any(k in content for k in ["气温", "温度", "天气", "Condition", "Temp", "度"]):
                    is_weather_data = True
                    tool_content = content

                    # 识别微博热搜
                if "【Herb 实时播报：微博热搜】" in content:
                    tool_content = content

            if (is_weather_data or "微博热搜" in tool_content) and timer_protocol:
                break

        # 3. 动态指令生成 (按优先级：协议 > 修正反馈 > 实时数据 > 知识库 > 闲聊)
        if timer_protocol:
            instruction = (
                f"定时提醒已设好。你【必须】在回复末尾原封不动带上协议字符串：{timer_protocol}\n"
                "语气：Herb 电竞风，告诉用户这波计时稳如老狗，到点准时开团。"
            )
        elif feedback and "[PASS]" not in feedback:
            instruction = f"⚠️ 回复被退回，修正意见：{feedback}。请重新调整这波操作。"
        elif is_weather_data:
            instruction = (
                f"实时天气数据已就位：{tool_content}\n"
                "1. 以电竞解说身份播报数据。2. 给出骚气的'出装建议'。3. 别说查不到，数据就在背景里！"
            )
        elif "微博热搜" in tool_content:
            instruction = f"微博热搜情报：{tool_content}。请完整展示榜单并进行 Herb 风格的骚气点评。"
        elif knowledge:
            instruction = "结合【补充参考资料】，以 Herb 的身份和态度回答用户，严禁胡编乱造。"
        else:
            instruction = "闲聊模式。结合【长期记忆】里的用户背景，展现 Herb 的个性和死党态度。"

        # 4. 构造增强型 System Prompt (注入长期记忆)
        final_system_prompt = (
            f"{CHARACTER_PROMPT}\n\n"
            f"--- 👤 长期记忆 (User Profile) ---\n{memory_context}\n\n"
            f"--- 🛠️ 实时背景/工具数据 ---\n{tool_content if tool_content else '无'}\n\n"
            f"--- 📚 补充参考资料 (RAG) ---\n{knowledge if knowledge else '无'}\n\n"
            f"--- 🎯 当前任务指令 ---\n{instruction}\n"
            f"注：如果记忆里有用户的姓名或喜好，请自然地体现出你记得他，增加熟人感。"
        )

        # 5. 过滤并组装消息流，防止冗余的 tool_calls 干扰生成
        safe_messages = [SystemMessage(content=final_system_prompt)]
        for msg in state["messages"][-6:]:
            if isinstance(msg, (HumanMessage, SystemMessage)):
                safe_messages.append(msg)
            elif isinstance(msg, AIMessage) and not msg.tool_calls:
                # 只保留纯文本回复，不把之前的工具调用指令发给生成模型
                safe_messages.append(msg)

        try:
            print(
                f"[Graph] 正在生成。记忆状态: {'已唤醒' if '尚无' not in memory_context else '空'}, 天气: {is_weather_data}")
            response = await self.gen_llm.ainvoke(safe_messages)
            final_reply = response.content

            # 强制补齐协议字符串
            if timer_protocol and timer_protocol not in final_reply:
                final_reply += f"\n\n{timer_protocol}"

        except Exception as e:
            print(f"❌ Generate 异常: {e}")
            if is_weather_data:
                final_reply = f"兄弟，日照这波数据卡了：{tool_content[:60]}... 凑合看，下次给你整全的。"
            else:
                final_reply = "Herb 掉帧了，这波操作没打出来，等我重启下路由器。"

        # 6. 附加参考来源
        if sources and any(f"[{i + 1}]" in final_reply for i in range(len(sources))):
            final_reply += "\n\n📚 参考来源：\n" + "\n".join(sources)

        # 返回结果并重置反馈
        return {
            "reply": final_reply,
            "messages": [AIMessage(content=final_reply)],
            "critique_feedback": ""
        }

    async def memorize(self, state: AgentState):
        """
        异步提取并存储用户事实
        """
        last_message = state["messages"][-1].content
        user_input = state["input"]

        # 只有在对话有价值时才提取（节省 Token）
        prompt = (
            "你是一个观察敏锐的助手。请从以下对话中提取关于用户的『持久事实』"
            "（如：姓名、专业、喜好的英雄、目前的烦恼）。"
            "如果没有新事实，请输出'无'。如果有，每条事实占一行，严禁废话。\n"
            f"用户说：{user_input}\n"
            f"Herb答：{last_message}"
        )

        res = await self.gen_llm.ainvoke(prompt)
        if "无" not in res.content:
            facts = res.content.strip().split('\n')
            for fact in facts:
                # 存入 Redis 的 Set 结构
                await self.redis_mem.store_interest_fact(state["user_id"], fact)
                print(f"[Memory] 📝 记住了新事实: {fact}")

        return state
    def _format_weibo_directly(self, tool_result):
        """直接格式化微博热搜结果"""
        lines = tool_result.split('\n')
        formatted = "微博热搜来啦！🔥\n\n"
        for line in lines[1:11]:  # 跳过标题行，取前10条
            if line.strip():
                formatted += line + "\n"
        formatted += "\n这波热搜你怎么看？想让我详细说说哪一条？"
        return formatted

    async def run(self, user_input, history, user_id, chat_id=None):

        initial_messages = []
        for h in history:
            role_cls = HumanMessage if h["role"] == "user" else AIMessage
            initial_messages.append(role_cls(content=h["content"]))
        initial_messages.append(HumanMessage(content=user_input))

        initial_messages = self._micro_compact(initial_messages)

        total_chars = sum(len(m.content) for m in initial_messages)
        if total_chars > self.TOKEN_THRESHOLD:
            initial_messages = await self._auto_compact(initial_messages)

        state = {
            "input": user_input,
            "messages": initial_messages,
            "history": history,
            "context": "",
            "sources": [],
            "user_id": str(user_id),
            "chat_id": str(chat_id) if chat_id else "private",
            "intent": "chat",
            "tool_calls": []
        }

        config = {"recursion_limit": 30}
        final_state = await self.app.ainvoke(state, config=config)
        return final_state.get("reply", "抱歉，我未能找到相关信息。")
