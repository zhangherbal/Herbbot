import time
import re
import asyncio
import json
import os
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

        # Layer 3: Save transcript (保存原始快照用于故障复原)
        ts = int(time.time())
        path = self.TRANSCRIPT_DIR / f"transcript_{ts}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps({"type": m.type, "content": m.content}, ensure_ascii=False) + "\n")

        # Layer 2: LLM Summarization (将长历史压缩为核心记忆)
        print(f"[*] 对话历史过长，触发 Auto-Compact 机制...")

        history_to_summarize = ""
        for m in messages:
            prefix = "User" if isinstance(m, HumanMessage) else "AI"
            history_to_summarize += f"{prefix}: {m.content[:500]}\n"

        summary_prompt = (
            "你是一个记忆管理专家。请总结以下对话的核心上下文、用户偏好和待办事项。\n"
            "要求：极其简练，只保留关键信息，作为后续对话的背景资料。\n\n"
            f"--- 对话片段 ---\n{history_to_summarize}"
        )

        response = await self.gen_llm.ainvoke(summary_prompt)

        return [
            SystemMessage(content=f"[System Memory Archive]\n先前对话摘要：{response.content}"),
            AIMessage(content="我已整理好之前的对话记忆，请继续。")
        ]

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

        builder.add_node("analyze", self.analyze)
        builder.add_node("execute_tool", self.execute_tool)
        builder.add_node("retrieve", self.retrieve)
        builder.add_node("generate", self.generate)
        builder.add_node("critique", self.critique)

        builder.add_edge(START, "analyze")

        builder.add_conditional_edges(
            "analyze",
            self._get_router_logic()
        )

        builder.add_edge("execute_tool", "analyze")  # 执行完工具回analyze检查结果
        builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", "critique")

        builder.add_conditional_edges(
            "critique",
            lambda state: END if "[PASS]" in state.get("critique_feedback", "") or state.get("retry_count",
                                                                                             0) >= 2 else "generate"
        )

        return builder.compile()

    async def analyze(self, state: AgentState):

        messages = state["messages"]
        user_input = state["input"].lower()
        current_time = time.strftime('%Y-%m-%d %H:%M:%S')

        has_tool_result = any(isinstance(m, ToolMessage) for m in messages[-3:])
        if (messages and isinstance(messages[-1], ToolMessage)) or has_tool_result:
            print("[Graph] ✅ 探测到工具执行结果，准备生成最终回复。")
            return {
                "intent": "chat",
                "tool_calls": [],
                "critique_feedback": ""
            }

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
                "tool_calls": [tool_call]
            }

        if any(kw in user_input for kw in ["热搜", "微博", "瓜"]):
            print("[Graph] ⚠️ 命中硬编码逻辑：强制触发微博热搜")
            tool_call = {
                "name": "get_weibo_hot_search",
                "args": {},
                "id": f"force_weibo_{int(time.time())}"
            }
            return {
                "intent": "tool",
                "messages": [AIMessage(content="正在为你打探微博热搜...", tool_calls=[tool_call])],
                "tool_calls": [tool_call]
            }

        weather_trigger = any(kw in user_input for kw in ["天气", "气温", "下雨", "多少度", "冷不冷"])
        weather_instruction = ""
        if weather_trigger:
            print(f"[Graph] 🌤 探测到天气需求，准备引导 LLM 提取城市...")
            weather_instruction = (
                "\n【紧急任务：天气查询】\n"
                "1. 你必须调用 'get_weather' 工具。\n"
                "2. 必须从输入中提取纯净的城市名（如 '日照'、'北京'），严禁包含‘查询’、‘今天’等废词。\n"
                "3. 如果用户没说城市，请根据上下文推断，严禁直接说查不到。"
            )

        system_prompt = (
            f"{CHARACTER_PROMPT}\n"
            f"当前时间: {current_time}\n"
            "【意图判断协议】\n"
            f"1. 实时信息（天气/提醒）：使用 tool。{weather_instruction}\n"
            "2. 校园知识（保研/绩点/食堂）：使用 rag。\n"
            "3. 普通闲聊：使用 chat。\n"
            "注意：如果涉及时间或天气，必须优先调用工具，不要只用文字回复。"
        )

        full_messages = [SystemMessage(content=system_prompt)] + messages[-6:]

        try:
            res = await self.decision_llm.ainvoke(full_messages)

            if res.tool_calls:
                for call in res.tool_calls:
                    if call["name"] == "get_weather":
                        raw_city = call["args"].get("city", "")
                        clean_city = re.sub(r"(查询|天气|今天|明天|现在的|山东|省|市)", "", str(raw_city)).strip()
                        call["args"]["city"] = clean_city if clean_city else "北京"

                print(f"[Graph] LLM 决策成功: {res.tool_calls[0]['name']} -> {res.tool_calls[0]['args']}")
                return {"intent": "tool", "messages": [res], "tool_calls": res.tool_calls}

            if weather_trigger:
                print("[Graph] ⚠️ LLM 漏掉工具调用，执行暴力补齐")
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
                    "messages": [AIMessage(content="这就去查查天气。", tool_calls=[tool_call])]
                }


            rag_keywords = ["保研", "综测", "绩点", "饭卡", "宿舍", "食堂", "挂科", "学分"]
            intent = "rag" if any(k in user_input for k in rag_keywords) else "chat"

            return {"intent": intent, "messages": [res], "tool_calls": []}

        except Exception as e:
            print(f"❌ Analyze 节点异常: {e}")
            return {"intent": "chat", "messages": [], "tool_calls": []}
    async def execute_tool(self, state: AgentState):
        last_message = state["messages"][-1]
        tool_messages = []
        outputs = []

        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            print("⚠️ 警告：进入 execute_tool 但未检测到工具调用请求")
            return {"messages": [], "context": "", "intent": "chat"}

        for call in last_message.tool_calls:
            name = call["name"]
            args = call["args"]
            print(f"正在执行工具: {name} 参数: {args}")
            try:
                if name == "set_reminder":
                    # 确保 duration_str 存在
                    if "minutes" in args:
                        args["duration_str"] = f"{args.pop('minutes')}分钟"
                    elif "duration" in args:
                        args["duration_str"] = str(args.pop("duration"))

                if name.startswith("puppeteer_"):
                    result = await self.mcp.call_tool(name, args)
                else:
                    result = await asyncio.to_thread(self.sm.execute, name, args)

                outputs.append(str(result))
                tool_messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
            except Exception as e:
                error_msg = f"工具执行错误: {e}"
                print(f"❌ 工具内部错误: {e}")
                outputs.append(error_msg)
                tool_messages.append(ToolMessage(content=error_msg, tool_call_id=call["id"]))

        return {
            "messages": tool_messages,
            "context": "\n".join(outputs),
            "intent": "chat"  # 执行完工具后，意图转为聊天
        }
    async def retrieve(self,state: AgentState):
        print("[Graph] 正在检索知识库...")
        docs = await asyncio.to_thread(self.vm.query, state["input"], state["user_id"], state["chat_id"], k=5)

        context_parts, source_list = [], []
        for i, d in enumerate(docs):
            source_name = d.metadata.get("source", "未知文档")
            source_list.append(f"[{i + 1}] {source_name}")
            context_parts.append(f"--- 资料 [{i + 1}] ---\n{d.page_content}")

        return {
            "context": "\n\n".join(context_parts),
            "sources": source_list
        }

    def generate(self, state: AgentState):
        feedback = state.get("critique_feedback", "")
        knowledge = state.get("context", "").strip()
        sources = state.get("sources", [])

        tool_content = ""
        timer_protocol = ""
        is_weather_data = False

        for msg in reversed(state["messages"][-10:]):
            if isinstance(msg, ToolMessage) and msg.content:
                content = msg.content
                t_match = re.search(r"\[SEC:\d+\].*?】", content)
                if t_match and not timer_protocol:
                    timer_protocol = t_match.group(0)

                if any(k in content for k in ["气温", "温度", "天气", "Condition", "Temp", "度"]):
                    is_weather_data = True
                    tool_content = content  # 记录天气原文

                if "【Herb 实时播报：微博热搜】" in content:
                    tool_content = content

            if (is_weather_data or "微博热搜" in tool_content) and timer_protocol:
                break

        if timer_protocol:
            instruction = (
                f"定时提醒已设好。你【必须】在回复末尾原封不动带上协议字符串：{timer_protocol}\n"
                "语气：Herb 电竞风，告诉用户这波计时稳如老狗。"
            )
        elif is_weather_data:
            instruction = (
                f"你已经拿到了实时天气数据：{tool_content}\n"
                "### 任务 ###\n"
                "1. 以 Herb 电竞解说的身份播报天气和气温。\n"
                "2. 给出骚气的'出装建议'（穿衣/带伞）。\n"
                "3. 绝对不要说'查不到'或'当成指令'，数据就在实时背景里！"
            )
        elif "微博热搜" in tool_content:
            instruction = f"微博热搜来啦：{tool_content}。请展示完整的微博热搜榜单并骚气点评。"
        elif feedback and "[PASS]" not in feedback:
            instruction = f"注意：之前的回答被退回，意见：{feedback}。请修正。"
        elif knowledge:
            instruction = "结合参考资料，以 Herb 的身份回答用户问题。"
        else:
            instruction = "闲聊模式，展现 Herb 的个性和电竞态度。"

        safe_messages = []

        final_system_prompt = (
            f"{CHARACTER_PROMPT}\n\n"
            f"--- 实时背景/工具执行结果 ---\n{tool_content if tool_content else '无'}\n\n"
            f"--- 补充参考资料 ---\n{knowledge if knowledge else '无'}\n\n"
            f"当前任务指令：{instruction}"
        )
        safe_messages.append(SystemMessage(content=final_system_prompt))

        for msg in state["messages"][-6:]:
            if isinstance(msg, (HumanMessage, SystemMessage)):
                safe_messages.append(msg)
            elif isinstance(msg, AIMessage) and not msg.tool_calls:
                safe_messages.append(msg)

        try:
            print(f"[Graph] 正在生成回复。天气状态: {is_weather_data}, 定时器状态: {bool(timer_protocol)}")
            response = self.gen_llm.invoke(safe_messages)
            final_reply = response.content

            if timer_protocol and timer_protocol not in final_reply:
                final_reply += f"\n\n{timer_protocol}"

        except Exception as e:
            print(f"❌ Generate 异常: {e}")
            if is_weather_data:
                return {"reply": f"兄弟们，日照这波气象数据我直接贴这了：{tool_content[:50]}... 信号有点波动，下次再详聊！"}
            final_reply = "Herb 掉帧了，这波操作没打出来。"

        if sources and any(f"[{i + 1}]" in final_reply for i in range(len(sources))):
            final_reply += "\n\n📚 参考来源：\n" + "\n".join(sources)

        return {"reply": final_reply}
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
