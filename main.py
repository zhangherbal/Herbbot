import botpy
from botpy.message import DirectMessage, Message
from config.settings import QQ_APP_ID, QQ_SECRET
from config.prompt import CHARACTER_PROMPT
from core.loop import AgentLoop
from core.skill_manager import SkillManager
from core.mcp_client import MCPManager


class MyBot(botpy.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skill_manager = SkillManager()
        self.mcp_manager = MCPManager()

        self.agent = None
        self.history_cache = {}

    async def on_ready(self):
        print(f"机器人「{self.robot.name}」已上线！")

        async def send_reminder_msg(target_id, text):

            try:
              
                if len(target_id) > 20:

                    await self.api.post_c2c_message(openid=target_id, msg_type=0, content=text)
                    print(f"[主动推送] 闹钟提醒已发送至个人私聊: {target_id}")
                else:
                    await self.api.post_message(channel_id=target_id, content=text)
                    print(f"[主动推送] 闹钟提醒已发送至频道: {target_id}")
            except Exception as e:
                print(f"[!] 主动推送失败，ID: {target_id}，错误: {e}")

        self.agent = AgentLoop(self.skill_manager, self.mcp_manager, send_message_func=send_reminder_msg)

        try:
            await self.mcp_manager.connect_to_server("npx", ["@modelcontextprotocol/server-everything"])
            print("[+] 联网服务加载完毕！")
        except Exception as e:
            print(f"[!] MCP 加载失败: {e}")

    async def _handle_all_messages(self, message, source_type):
        content = message.content.strip()

        user_id = getattr(message.author, 'user_openid', None) or getattr(message.author, 'id', 'unknown_user')
        channel_id = getattr(message, 'channel_id', None) or getattr(message, 'id', None)

        print(f"\n[!!! 捕获到{source_type}消息 !!!] 用户ID: {user_id} 内容: {content}")

        if not content: return
        if not self.agent: return  # 确保 agent 已在 on_ready 初始化

        if user_id not in self.history_cache:
            self.history_cache[user_id] = [{"role": "system", "content": CHARACTER_PROMPT}]

        try:
            print(f"--- Herbbot 正在通过 DeepSeek 思考... ---")


            reply, updated_history = await self.agent.run(
                user_input=content,
                history=self.history_cache[user_id],
                user_id=user_id,
                channel_id=channel_id
            )

            self.history_cache[user_id] = updated_history[-10:]
            await message.reply(content=reply)
            print(f"[已回复] {reply}")

        except Exception as e:
            print(f"!!! Agent运行报错: {e}")
            import traceback
            traceback.print_exc()
            await message.reply(content="咱就是说，大脑突然短路了，稍等下哈~")

    async def on_direct_message_create(self, message: DirectMessage):
        await self._handle_all_messages(message, "【频道私聊】")

    async def on_c2c_message_create(self, message):
        await self._handle_all_messages(message, "【C2C私聊】")

    async def on_at_message_create(self, message: Message):
        await self._handle_all_messages(message, "【@消息】")


if __name__ == "__main__":
    intents = botpy.Intents.default()
    intents.direct_message = True
    intents.public_messages = True
    if hasattr(intents, 'c2c_group_at_messages'):
        intents.c2c_group_at_messages = True

    client = MyBot(intents=intents)
    client.run(appid=QQ_APP_ID, secret=QQ_SECRET)
