import json
import asyncio
from openai import OpenAI
from config.settings import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME


class AgentLoop:
    def __init__(self, skill_manager, mcp_manager, send_message_func=None):
        self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        self.skills = skill_manager
        self.mcp = mcp_manager
        self.send_msg_func = send_message_func

    async def run(self, user_input, history, user_id, channel_id, max_steps=5):
        mcp_schemas = await self.mcp.get_tool_schemas() if self.mcp else []
        all_tools = self.skills.get_schemas() + mcp_schemas

        messages = history + [{"role": "user", "content": user_input}]

        step = 0
        while step < max_steps:
            step += 1
            response = self.client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=all_tools if all_tools else None,
                tool_choice="auto"
            )

            curr_message = response.choices[0].message
            messages.append(curr_message)

            if not curr_message.tool_calls:
                return curr_message.content, messages

            tasks = []
            tool_call_info = []

            for tool_call in curr_message.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                print(f"[*] 正在调用工具: {name} | 参数: {args}")

                if name == "set_reminder":
                    mins = args.get("minutes", 1)
                    task_name = args.get("task", "闹钟")
                    target_id = user_id if (user_id and len(user_id) > 20) else channel_id

                    asyncio.create_task(self._reminder_timer(mins, task_name, target_id))

                tasks.append(self._execute_single_tool(name, args, user_id))
                tool_call_info.append(tool_call)

            results = await asyncio.gather(*tasks)

            for i, result in enumerate(results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_info[i].id,
                    "name": tool_call_info[i].function.name,
                    "content": str(result)
                })

        return "抱歉，任务处理步骤过多，我先歇会儿。", messages

    async def _execute_single_tool(self, name, args, user_id):
        try:
            if name in self.skills.skills:
                return self.skills.execute(name, args, user_id)
            else:
                return await self.mcp.call_tool(name, args)
        except Exception as e:
            return f"错误: {str(e)}"

    async def _reminder_timer(self, minutes, task, target_id):

        try:
            print(f"[*] 闹钟已启动：{minutes} 分钟后提醒 {task}")
            await asyncio.sleep(int(minutes) * 60)

            if self.send_msg_func:
                reminder_text = (
                    f"⏰【Herb 闹钟提醒】\n"
                    f"喂！时间到了！醒醒！\n"
                    f"你定的任务：『{task}』\n"
                    f"赶紧去干活，别让哥在这儿干等！"
                )

                await self.send_msg_func(target_id, reminder_text)
                print(f"[*] 闹钟提醒已成功推送至 ID: {target_id}")
        except Exception as e:
            print(f"[!] 计时器内部出错: {e}")
