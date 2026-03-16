import time
import re
import asyncio
from typing import TypedDict, List, Optional, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage

from config.settings import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME
from config.prompt import CHARACTER_PROMPT

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage  
)

class AgentState(TypedDict):
    input: str
    history: List[dict]
    messages: Annotated[List[BaseMessage], operator.add]
    context: str
    user_id: str
    chat_id: str
    intent: str
    tool_calls: List[dict]
    reply: Optional[str]


class HerbGraph:
    def __init__(self, vector_manager, mcp_manager, skill_manager, all_tools):
        self.vm = vector_manager
        self.mcp = mcp_manager
        self.sm = skill_manager

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

    def _build_graph(self):
        builder = StateGraph(AgentState)


        async def analyze(state: AgentState):
            print(f"[Graph] 决策分析中，当前对话轮数: {len(state['messages'])}")
            current_time = time.strftime('%Y-%m-%d %H:%M:%S')

            system_prompt = (
                f"{CHARACTER_PROMPT}\n"
                f"当前时间:{current_time}\n"
                "指令：请根据对话历史和工具返回的结果判断：\n"
                "1. 如果需要访问网页/点击，请继续调用工具。\n"
                "2. 如果已经获取到足够信息或找到答案，请停止调用工具，直接总结回答。"
                "3. 如果是调用微博热搜功能，请先返回完整的热搜榜单，再总结回答。"
            )


            full_messages = [SystemMessage(content=system_prompt)] + state["messages"]

            res = await self.decision_llm.ainvoke(full_messages)

            intent = "chat"
            if res.tool_calls:
                intent = "tool"
                print(f"[Graph] 识别到新工具调用: {[t['name'] for t in res.tool_calls]}")
            else:
                rag_keywords = ["保研", "综测", "绩点", "饭卡", "宿舍", "食堂"]
                if any(k in state["input"] for k in rag_keywords) or len(state["input"]) > 10:
                    # 只有在第一轮且没有工具调用时才可能触发 RAG
                    if len(state["messages"]) <= 1:
                        intent = "rag"

            return {
                "intent": intent,
                "messages": [res],
                "tool_calls": res.tool_calls
            }

        async def execute_tool(state: AgentState):
            last_message = state["messages"][-1]
            tool_messages = []
            outputs = []

            for call in last_message.tool_calls:
                name = call["name"]
                args = call["args"]
                try:
                    if name == "set_reminder" and "minutes" in args:
                        args["duration_str"] = f"{args.pop('minutes')}分钟"

                    if name.startswith("puppeteer_"):
                        result = await self.mcp.call_tool(name, args)
                    else:
                        result = self.sm.execute(name, args)

                    outputs.append(str(result))
                    tool_messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
                except Exception as e:
                    error_msg = f"工具错误:{e}"
                    outputs.append(error_msg)
                    tool_messages.append(ToolMessage(content=error_msg, tool_call_id=call["id"]))

            return {
                "messages": tool_messages,
                "context": "\n".join(outputs)
            }

        # ---------------------------
        # 3. RAG 检索 (保持不变)
        # ---------------------------
        async def retrieve(state: AgentState):
            print("[Graph] 正在检索知识库...")
            knowledge = await asyncio.to_thread(
                self.vm.query,
                state["input"],
                state["user_id"],
                state["chat_id"],
                k=10
            )
            return {"context": knowledge}

        # ---------------------------
        # 4. 回复生成 (保持标签逻辑)
        # ---------------------------
        def generate(state: AgentState):
            knowledge = state.get("context", "").strip()

            # 1. 提取计时器标签逻辑
            raw_tag = ""
            if "[SEC:" in knowledge:
                match = re.search(r"\[SEC:\d+\]【.*?】", knowledge)
                if match: raw_tag = match.group(0)

            # 2. 判断内容是否包含微博热搜
            is_weibo = "【Herb 实时播报：微博热搜】" in knowledge

            # 3. 动态构建指令
            # 如果是微博热搜，使用绝对禁止总结的语气
            instruction = ""
            if is_weibo:
                instruction = (
                    "### 绝对指令 ###\n"
                    "检测到参考资料中有微博热搜榜单。你必须：\n"
                    "1. 完整地、逐字逐句地展示从第 1 条到最后一条的热搜内容，严禁进行任何概括或省略！\n"
                    "2. 先输出完整的榜单，然后再用你 Herb 的身份进行评价。\n"
                    "3. 不要说'根据资料显示'这种废话，直接甩出榜单。"
                )
            else:
                instruction = "请结合参考资料，以 Herb 的身份直接回答用户。"

            system_prompt = (
                f"{CHARACTER_PROMPT}\n"
                f"--- 参考资料 ---\n"
                f"{knowledge if knowledge else '没有找到直接相关的参考资料。'}\n\n"
                f"{instruction}"
            )

            # 4. 生成回复 (注意：这里 state['messages'] 建议改为 history，确保上下文连贯)
            response = self.gen_llm.invoke([
                {"role": "system", "content": system_prompt},
                *state.get("messages", [])
            ])

            final_reply = response.content

            # 5. 补偿逻辑：如果 AI 还是没听话（比如 LLM 抽风），
            # 在代码层面强制将列表补在回复前面 (可选，但最保险)
            if is_weibo and "1." not in final_reply:
                final_reply = f"{knowledge}\n\n{final_reply}"

            if raw_tag and "[SEC:" not in final_reply:
                final_reply += f"\n\n{raw_tag}"

            return {"reply": final_reply}
        # ---------------------------
        # 5. 循环路由逻辑
        # ---------------------------
        def router(state: AgentState):
            if state["intent"] == "tool":
                return "execute_tool"
            if state["intent"] == "rag":
                return "retrieve"
            return "generate"

        builder.add_node("analyze", analyze)
        builder.add_node("execute_tool", execute_tool)
        builder.add_node("retrieve", retrieve)
        builder.add_node("generate", generate)

        builder.add_edge(START, "analyze")
        builder.add_conditional_edges("analyze", router)

        # 关键修改：execute_tool 结束后回到 analyze 形成闭环循环
        builder.add_edge("execute_tool", "analyze")
        builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", END)

        return builder.compile()

    async def run(self, user_input, history, user_id, chat_id=None):
        # 1. 构造初始消息列表（历史 + 当前问题）
        initial_messages = []
        for h in history:
            if h["role"] == "user":
                initial_messages.append(HumanMessage(content=h["content"]))
            else:
                initial_messages.append(AIMessage(content=h["content"]))

        # 将当前用户输入作为最后一条 HumanMessage
        initial_messages.append(HumanMessage(content=user_input))

        state = {
            "input": user_input,
            "messages": initial_messages,  # 这里的 messages 会在 Graph 中流转并累加
            "history": history,
            "context": "",
            "user_id": str(user_id),
            "chat_id": str(chat_id) if chat_id else "private",
            "intent": "chat",
            "tool_calls": []
        }

        # 设置递归上限，防止极端情况下的死循环（如 10 轮内必须出结果）
        config = {"recursion_limit": 30}
        final_state = await self.app.ainvoke(state, config=config)

        return final_state.get("reply", "抱歉，我未能找到相关信息。")
