try:
    import pysqlite3 as sqlite3
    import sys

    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass
from langchain_community.document_loaders import UnstructuredPDFLoader
import botpy
import redis.asyncio as redis
from config.settings import QQ_APP_ID, QQ_SECRET, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
from core.vector_store import VectorManager
from core.graph import HerbGraph
from core.memory import RedisMemory
from core.skill_manager import SkillManager
from skills import load_all_skills
import numpy as np
import random
from PIL import Image
import random
from datetime import datetime
import time
import asyncio
import httpx
import fitz  # PyMuPDF
import os
import re
import traceback
import time
from core.mcp_client import MCPManager
try:
    import fitz
except ImportError:
    import pymupdf as fitz

from rapidocr_onnxruntime import RapidOCR
ADMIN_LIST = ["DBFAECCD2C16102A945494949E65C886"]

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
def anti_dup(content):
    """
    通过注入动态毫秒戳和随机码，确保每一条发给QQ的消息内容都是唯一的。
    """
    nonce = f"\n[Ref-{int(time.time()*1000) % 100000}-{random.randint(100, 999)}]"
    return f"{content}{nonce}"

class MyBot(botpy.Client):
    def __init__(self, herb_graph, *args, **kwargs):
        # 1. 必须先提取并保存自定义参数，再调用父类初始化
        self.herb_graph = herb_graph
        super().__init__(*args, **kwargs)

        # 初始化辅助组件
        self.vm = VectorManager()
        self.mcp = MCPManager()
        self.memory = herb_graph.redis_mem

    async def on_ready(self):
        print(f"[*] 机器人 {self.robot.name} 已上线")
        # 启动后台清理任务
        asyncio.create_task(self._cleanup_task())

    async def _cleanup_task(self):
        while True:
            # 1. 统一等待时间（比如每小时执行一次全量清理）
            await asyncio.sleep(3600)

            # --- 任务 A：清理过期文档 ---
            try:
                print("[Cleanup] 正在清理过期向量文档...")
                self.vm.delete_expired_docs()
            except Exception as e:
                print(f"Vector Cleanup Error: {e}")

            # --- 任务 B：清理过期记忆碎片 ---
            try:
                print("[Cleanup] 正在清理 7 天前的记忆碎片...")
                seven_days_ago = time.time() - (7 * 24 * 3600)

                # 这里的 self.memory.redis 确保是你初始化好的 Redis 连接
                keys = await self.memory.redis.keys("user:*:facts")
                if keys:
                    for key in keys:
                        # 如果 key 是 bytes 类型，记得处理
                        k = key.decode() if isinstance(key, bytes) else key
                        affected = await self.memory.redis.zremrangebyscore(k, "-inf", seven_days_ago)
                        if affected > 0:
                            print(f"  > 已清理 {k} 中的 {affected} 条过期记录")
            except Exception as e:
                print(f"Memory Cleanup Error: {e}")
    async def _reminder_timer(self, message, reply, user_id):
        # 1. 提取秒数
        match_sec = re.search(r"\[SEC:(\d+)\]", reply)
        if not match_sec:
            return
        seconds = int(match_sec.group(1))

        # 2. 【改进正则】不再要求紧挨着，只要 【内容】 存在于字符串中即可
        # 这样不管 AI 怎么染色，只要有【】就能抓到
        match_task = re.search(r"【(.*?)】", reply)

        # 如果还是没抓到，尝试找“任务：内容”这种格式
        if not match_task:
            match_task = re.search(r"任务[：:](.*?)[\s\()]", reply)

        task = match_task.group(1).strip() if match_task else "洗衣服"  # 实在抓不到就拿个例子垫背

        print(f"[Timer] {seconds}s -> {task}")

        # 3. 等待
        await asyncio.sleep(seconds)

        # 4. 提醒内容加个随机干扰或时间戳，双重保险
        import time
        remind = f"🔔 Herb提醒 ({time.strftime('%H:%M')})\n该去：{task}"

        # 5. 双通道发送逻辑 (你的这个逻辑很稳！)
        try:
            # 优先主动推送 (C2C)
            await self.api.post_c2c_message(
                openid=user_id,
                msg_type=0,
                content=remind
            )
        except Exception as e:
            print(f"主动推送失败，尝试被动回复: {e}")
            try:
                # 备选：被动回复
                await message.reply(content=remind)
            except Exception as e2:
                print(f"提醒推送彻底失败: {e2}")
    async def _handle_pdf_and_summarize(self, message, file_url, file_name):
        """
        不再多次 reply，而是通过一条消息的不断拼接，最后一次性发出。
        这样物理规避了 QQ 对同 ID 多次回复的去重拦截。
        """
        user_openid = getattr(message.author, 'user_openid', None) or getattr(message.author, 'id', 'unknown')
        save_path = f"./data/temp/{int(time.time())}_{file_name}"
        os.makedirs("./data/temp", exist_ok=True)

        # 唯一后缀生成器
        def get_nonce():
            return f"\n[SID:{random.randint(10000, 99999)}]"

        # 初始化进度日志，最后一次性发送
        log_steps = []
        log_steps.append(f"🔍 正在处理文档：《{file_name}》")

        try:
            # 1. 下载
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(file_url)
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(resp.content)
            log_steps.append("✅ 文件下载完成")

            # 2. 解析
            def _parse():
                doc = fitz.open(save_path)
                # 限制页数防止超时
                text = "\n".join([page.get_text() for page in doc[:10]])
                doc.close()
                return text

            full_text = await asyncio.to_thread(_parse)
            if not full_text.strip():
                await message.reply(content=f"⚠️ 无法提取文字内容。{get_nonce()}")
                return
            log_steps.append("✅ 文本解析成功")

            # 3. 入库 (注意参数对齐)
            try:
                await asyncio.to_thread(self.vm.add_document, full_text, user_openid, file_name)
            except Exception as ve:
                print(f"入库细节错误: {ve}")
            log_steps.append("✅ 知识图谱已更新")

            # 4. 生成总结
            summary_text = "摘要生成中..."
            try:
                # 极简总结请求
                prompt = f"请用100字左右总结这份文档《{file_name}》的核心要点：\n\n{full_text[:2000]}"
                res = await asyncio.wait_for(self.herb_graph.gen_llm.ainvoke(prompt), timeout=15.0)
                summary_text = re.sub(r'</?DATA_BLOCK[^>]*>', '', res.content.strip())
            except Exception as se:
                print(f"总结超时: {se}")
                summary_text = "摘要生成稍有延迟，请直接提问细节。"

            # --- 最终发送（只发送这一条消息！） ---
            # 这一条合并了所有步骤，确保 100% 能发出
            final_log = "\n".join(log_steps)
            final_content = (
                f"📘 文档处理详情：\n"
                f"{final_log}\n\n"
                f"📑 核心笔记：\n"
                f"{summary_text}\n"
                f"{get_nonce()}"
            )

            await message.reply(content=final_content)

        except Exception as e:
            print(f"PDF 处理崩溃: {e}")
            # 即使是报错也加上随机后缀
            await message.reply(content=f"⚠️ 文档系统异常: {type(e).__name__}{get_nonce()}")

        finally:
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except:
                    pass

    async def _handle_all_messages(self, message):
        """
        Herb 核心处理器：支持 PDF 物理切分识别、Graph 运行、多重去重干扰、分级记忆存储
        """
        content = message.content.strip()
        user_id = getattr(message.author, "user_openid", None) or getattr(message.author, "id", "unknown")
        group_id = getattr(message, "group_openid", None) or "private"
        # session_id = f"{group_id}:{user_id}" # 新架构下主要使用 user_id 索引 Redis

        # 1. 附件处理 (保持不变)
        if hasattr(message, 'attachments') and message.attachments:
            for attach in message.attachments:
                if attach.filename.lower().endswith('.pdf'):
                    asyncio.create_task(self._handle_pdf_and_summarize(message, attach.url, attach.filename))
            return

        if not content: return

        try:
            # 2. 运行 Graph
            reply = await self.herb_graph.run(
                user_input=content,
                user_id=user_id,
                chat_id=group_id
            )
            if not reply: return

            # --- 3. 40054005 深度去重干扰增强 ---
            # 插入不可见的零宽字符组合 + 毫秒级随机数，确保消息 MD5 绝对唯一
            zws = "".join(random.choices(["\u200b", "\u200c", "\u200d"], k=5))
            nonce_ref = f"\n{zws}[Ref:{int(time.time() * 1000) % 10000}-{random.randint(100, 999)}]"

            # 无论是否是定时任务，都注入干扰，防止相同问题的相同回答被拦截
            final_reply = reply + nonce_ref

            # 4. 发送回复
            await message.reply(content=final_reply)

            # --- 5. 分级记忆处理 ---
            # 存入用户行为碎片 (不再使用 add_message)
            # 我们只存关键动作，不存全量聊天记录，防止 Redis 爆炸
            user_fact = f"用户说: {content[:30]}"
            await self.memory.store_fact(user_id, user_fact, importance="low")

            # 存入 AI 回复摘要
            # 脱水处理：如果回复太长，只存摘要，保护下一次对话的上下文空间
            history_save = reply if len(reply) < 150 else reply[:140] + "..."
            ai_fact = f"Herb答: {history_save}"
            await self.memory.store_fact(user_id, ai_fact, importance="low")

            # 6. 启动异步定时器
            if "[SEC:" in reply:
                asyncio.create_task(self._reminder_timer(message, reply, user_id))

        except Exception as e:
            traceback.print_exc()
            # 错误信息也必须加去重，否则连续报错时会被 QQ 拦截导致你看不到报错原因
            err_nonce = f"{int(time.time()) % 10000}-{random.randint(10, 99)}"
            await message.reply(content=f"Herb 脑回路短路了... [ERR:{type(e).__name__}-{err_nonce}]")

    async def on_at_message_create(self, message):
        await self._handle_all_messages(message)

    async def on_c2c_message_create(self, message):
        await self._handle_all_messages(message)


async def main():
    # 1. 初始化 Redis
    print("[1/5] 正在连接 Redis...")
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,  # 建议加上这个，省得以后到处 decode('utf-8')
        db=1
    )
    mem = RedisMemory(redis_client)
    # 2. 初始化向量库
    print("[2/5] 正在加载向量模型...")
    vm = VectorManager()
    sm = SkillManager()
    load_all_skills(sm)
    print(f"[*] 技能工厂初始化完毕，已加载 {len(sm.get_schemas())} 个技能")
    print("[3/5] 正在加载MCP...")
    mcp = MCPManager()
    try:
        # 正确的单次调用方式，Windows 下使用 npx.cmd
        await mcp.connect_to_server("npx.cmd", ["-y", "@playwright/mcp@latest"])
        print("[*] MCP 服务器连接成功！")
    except Exception as e:
        print(f"[!] 启动失败: {e}")
    # 3. 初始化 Graph
    print("[4/5] 正在构建 Herb 智能体引擎...")
    # 确保 HerbGraph 的 __init__ 接收这两个参数
    herb_graph = HerbGraph(vector_manager=vm, redis_memory=mem,mcp_manager=mcp,skill_manager=sm,)

    # 4. 启动机器人
    print("[5/5] 正在拨号连接 QQ 服务器...")
    intents = botpy.Intents.default()
    intents.public_messages = True
    intents.direct_message = True  # 开启私聊支持

    client = MyBot(herb_graph=herb_graph, intents=intents)
    await client.start(appid=QQ_APP_ID, secret=QQ_SECRET)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Herb 已离线。")
