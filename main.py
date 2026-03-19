import botpy
import asyncio
import re
import requests
import os
from config.settings import QQ_APP_ID, QQ_SECRET
from core.vector_store import VectorManager
from core.graph import HerbGraph
from core.mcp_client import MCPManager
from core.skill_manager import SkillManager
import httpx
from core.memory import RedisMemory
from config.settings import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
ADMIN_LIST = ["DBFAECCD2C16102A945494949E65C886"]
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
class MyBot(botpy.Client):

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.vm = VectorManager()
        self.mcp = MCPManager()
        self.sm = SkillManager()
        self.memory = RedisMemory(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD
        )

    async def on_ready(self):
        print(f"机器人 {self.robot.name} 已上线")

        # 加载 MCP 工具 (保持原有逻辑)
        base_path = os.path.dirname(os.path.abspath(__file__))
        mcp_path = os.path.join(base_path, "node_modules", "@modelcontextprotocol", "server-puppeteer", "dist",
                                "index.js")

        if os.path.exists(mcp_path):
            try:
                await self.mcp.connect_to_server("node", [mcp_path])
                print("[*] MCP Puppeteer 连接成功")
            except Exception as e:
                print(f"[!] MCP 启动失败: {e}")

        # 获取工具并初始化 Graph
        mcp_tools = await self.mcp.get_tool_schemas()
        skill_tools = self.sm.get_schemas()
        all_tools = mcp_tools + skill_tools

        from core.graph import HerbGraph
        self.graph = HerbGraph(self.vm, self.mcp, self.sm, all_tools)
        print(f"[*] HerbGraph 就绪，当前加载工具数: {len(all_tools)}")
    async def _cleanup_task(self):

        while True:
            await asyncio.sleep(3600)

            self.vm.delete_expired_docs()

    async def _reminder_timer(self, message, reply, user_id):

        match_sec = re.search(r"\[SEC:(\d+)\]", reply)

        match_task = re.search(r"\[SEC:\d+\]【(.*?)】", reply)

        if not match_sec:
            return
        seconds = int(match_sec.group(1))
        task = match_task.group(1) if match_task else "任务"
        print(f"[Timer] {seconds}s -> {task}")
        await asyncio.sleep(seconds)
        remind = f"🔔 Herb提醒\n该去：{task}"
        try:
            await self.api.post_c2c_message(
                openid=user_id,
                msg_type=0,
                content=remind
            )
        except:
            try:
                await message.reply(content=remind)
            except:
                print("提醒推送失败")

    async def _handle_pdf_and_summarize(self, message, file_url, file_name):
        msg_seq = 1
        # 提取身份信息
        user_openid = getattr(message.author, 'user_openid', None) \
                      or getattr(message.author, 'id', 'unknown')
        group_openid = getattr(message, 'group_openid', None)
        is_admin = user_openid in ADMIN_LIST

        save_path = f"./data/temp/{file_name}"

        try:
            os.makedirs("./data/temp", exist_ok=True)

            # 1. 异步下载文件
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("GET", file_url) as resp:
                    resp.raise_for_status()
                    with open(save_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(8192):
                            if chunk:
                                f.write(chunk)

            if not PdfReader:
                await message.reply(content="⚠️ 环境未安装 pypdf 库", msg_seq=msg_seq)
                return

            # 2. 解析 PDF 内容
            # 注意：PdfReader 对象初始化很快，但 extract_text 较慢
            reader = PdfReader(save_path)
            pages_text = []
            for p in reader.pages:
                t = p.extract_text()
                if t: pages_text.append(t)

            full_text = "\n".join(pages_text)

            if not full_text.strip():
                await message.reply(content="⚠️ PDF 解析失败，未找到有效文本内容", msg_seq=msg_seq)
                return

            # 3. 异步执行耗时的向量入库操作 (防止阻塞主循环)
            # 使用 to_thread 将同步的 add_document 丢到线程池运行
            print(f"[*] 开始处理文档入库: {file_name}")
            # 3. 异步执行耗时的向量入库操作
            await asyncio.to_thread(
                self.vm.add_document,
                text=full_text,
                user_id=user_openid,
                doc_id=file_name,  # <-- 建议至少把文件名作为 ID 存入，或者根据业务逻辑生成 ID
                chat_id=group_openid,
                is_admin=is_admin,
                file_name=file_name
            )
            await message.reply(
                content=f"✅ 《{file_name}》处理完成\n已存入向量库并开启混合检索支持。",
                msg_seq=msg_seq
            )
            msg_seq += 1

            # 4. 文档总结 (调用 LLM)
            # 截取前 3000 字左右进行总结，确保上下文质量
            summary_prompt = f"请用100字左右总结以下文档的核心内容：\n\n{full_text[:3000]}"
            summary = await self.graph.gen_llm.ainvoke(summary_prompt)

            await message.reply(
                content=f"📑 文档总结：\n{summary.content}",
                msg_seq=msg_seq
            )

        except Exception as e:
            print(f"[PDF ERROR]: {e}")
            await message.reply(content=f"❌ PDF 处理失败: {str(e)}", msg_seq=msg_seq)

        finally:
            # 无论成功失败，确保清理临时文件
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except:
                    pass

    async def _handle_all_messages(self, message):
        """统一消息处理器 - 已优化记忆管理逻辑"""
        content = message.content.strip()
        user_id = getattr(message.author, "user_openid", None) or getattr(message.author, "id", "unknown")
        group_id = getattr(message, "group_openid", None) or "private"
        session_id = f"{group_id}:{user_id}"

        # 1. 处理 PDF 附件
        if hasattr(message, 'attachments') and message.attachments:
            for attach in message.attachments:
                if attach.filename.lower().endswith('.pdf'):
                    asyncio.create_task(self._handle_pdf_and_summarize(message, attach.url, attach.filename))
            return

        if not content:
            return

        try:
            # 2. 从 Redis 获取历史记忆（限制长度，防止上下文爆炸）
            history = await self.memory.get_history(session_id, limit=8)

            # 3. 执行 Graph 逻辑获取回复
            reply = await self.graph.run(
                content,
                history,
                user_id,
                group_id
            )

            # 4. 立即回复用户（提升用户体验感）
            await message.reply(content=reply)

            # 5. 【核心优化】更新 Redis 记忆（执行记忆脱水）
            # 先保存用户的原生输入
            await self.memory.add_message(session_id, "user", content)

            # 对 AI 回复进行长度检查
            # 如果回复包含热搜特征且长度过长，存入“脱水版”记忆
            history_reply = reply
            if len(reply) > 400 or "微博热搜" in reply:
                # 这里的占位符能告诉模型：你刚才已经报过热搜了，别再报了
                history_reply = "【系统记录：Herb 已向用户展示实时微博热搜榜单，此处由于篇幅过长已折叠。】"
                print(f"[Memory] 检测到长文本/热搜，已进行脱水处理存储。")

            await self.memory.add_message(session_id, "assistant", history_reply)

            # 6. 检查并开启提醒任务
            if "[SEC:" in reply:
                asyncio.create_task(self._reminder_timer(message, reply, user_id))

        except Exception as e:
            import traceback
            print(f"❌ 运行异常: {e}")
            traceback.print_exc()  
            await message.reply(content="Herb 刚才操作失误，这波没打好，等我缓一波。")
    async def on_at_message_create(self, message):

        await self._handle_all_messages(message)

    async def on_c2c_message_create(self, message):

        await self._handle_all_messages(message)


if __name__ == "__main__":

    intents = botpy.Intents.default()

    intents.public_messages = True
    intents.direct_message = True

    client = MyBot(intents=intents)

    client.run(appid=QQ_APP_ID, secret=QQ_SECRET)
