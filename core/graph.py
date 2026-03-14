import time
import re
from typing import TypedDict, List, Optional

from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI

from config.settings import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME
from config.prompt import CHARACTER_PROMPT


class AgentState(TypedDict):
    input: str
    history: List[dict]
    context: str
    reply: str
    user_id: str
    chat_id: Optional[str]
    intent: str
    tool_calls: List[dict]


class HerbGraph:

    def __init__(self, vector_manager, mcp_manager, skill_manager):

        self.vm = vector_manager
        self.mcp = mcp_manager
        self.sm = skill_manager

        self.decision_llm = ChatOpenAI(
            model=MODEL_NAME,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            temperature=0
        ).bind_tools(self.sm.get_schemas())

        self.gen_llm = ChatOpenAI(
            model=MODEL_NAME,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            temperature=0.7
        )

        self.app = self._build_graph()

    def _build_graph(self):

        builder = StateGraph(AgentState)

        # ---------------------------
        # 1 意图分析
        # ---------------------------

        def analyze(state: AgentState):

            print("[Graph] 分析:", state["input"])

            current_time = time.strftime('%Y-%m-%d %H:%M:%S')

            messages = [
                {"role": "system",
                 "content": f"{CHARACTER_PROMPT}\n当前时间:{current_time}"},
                {"role": "user", "content": state["input"]}
            ]

            res = self.decision_llm.invoke(messages)

            if res.tool_calls:
                return {
                    "intent": "tool",
                    "tool_calls": res.tool_calls
                }

            keywords = ["提醒", "天气", "热搜", "几点", "开箱"]

            if any(k in state["input"] for k in keywords):
                return {
                    "intent": "tool",
                    "tool_calls": []
                }

            return {"intent": "chat"}

        # ---------------------------
        # 2 执行工具
        # ---------------------------

        async def execute_tool(state: AgentState):

            outputs = []

            calls = state.get("tool_calls", [])

            if not calls:
                res = await self.decision_llm.ainvoke([
                    {"role": "user", "content": f"必须使用工具回答:{state['input']}"}
                ])

                calls = res.tool_calls if res.tool_calls else []

            for call in calls:

                name = call["name"]
                args = call["args"]

                try:

                    if name == "set_reminder" and "minutes" in args:
                        args["duration_str"] = f"{args.pop('minutes')}分钟"

                    result = self.sm.execute(name, args)

                    outputs.append(str(result))

                except Exception as e:

                    outputs.append(f"工具错误:{e}")

            context = "\n".join(outputs)

            return {"context": context}

        # ---------------------------
        # 3 RAG
        # ---------------------------

        def retrieve(state: AgentState):

            knowledge = self.vm.query(
                state["input"],
                state["user_id"],
                state["chat_id"]
            )

            return {"context": knowledge}

        # ---------------------------
        # 4 回复生成
        # ---------------------------

        def generate(state: AgentState):

            knowledge = state.get("context", "").strip()

            raw_tag = ""

            if "[SEC:" in knowledge:

                match = re.search(r"\[SEC:\d+\]【.*?】", knowledge)

                if match:
                    raw_tag = match.group(0)

            timer_instruction = ""

            if raw_tag:

                timer_instruction = (
                    f"\n【系统指令】必须在回复末尾保留标签:{raw_tag}"
                )

            system_prompt = (
                f"{CHARACTER_PROMPT}\n"
                f"参考信息:\n{knowledge}\n"
                f"{timer_instruction}"
            )

            response = self.gen_llm.invoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": state["input"]}
            ])

            final_reply = response.content

            if raw_tag and "[SEC:" not in final_reply:

                final_reply += f"\n\n{raw_tag}"

            return {"reply": final_reply}

        # ---------------------------
        # Graph
        # ---------------------------

        builder.add_node("analyze", analyze)
        builder.add_node("execute_tool", execute_tool)
        builder.add_node("retrieve", retrieve)
        builder.add_node("generate", generate)

        builder.add_edge(START, "analyze")

        def router(state: AgentState):

            if state["intent"] == "tool":
                return "execute_tool"

            if state["intent"] == "rag":
                return "retrieve"

            return "generate"

        builder.add_conditional_edges("analyze", router)

        builder.add_edge("execute_tool", "generate")
        builder.add_edge("retrieve", "generate")

        builder.add_edge("generate", END)

        return builder.compile()

    async def run(self, user_input, history, user_id, chat_id=None):

        state = {
            "input": user_input,
            "history": history,
            "context": "",
            "user_id": str(user_id),
            "chat_id": str(chat_id) if chat_id else "private",
            "intent": "chat",
            "tool_calls": []
        }

        final_state = await self.app.ainvoke(state)

        return final_state["reply"]