import redis.asyncio as redis
import json
import time


class RedisMemory:
    def __init__(self, host='localhost', port=6379, db=1, password=''):
        self.r = redis.Redis(
            host=host, port=port, password=password, db=db,
            decode_responses=True,
            socket_timeout=5.0
        )

    # --- 1. 对话流管理 (原有逻辑优化) ---
    async def get_history(self, session_id: str, limit=10):
        key = f"herb:chat:{session_id}"
        data = await self.r.lrange(key, -limit, -1)
        return [json.loads(m) for m in data]

    async def add_message(self, session_id: str, role: str, content: str):
        key = f"herb:chat:{session_id}"
        # 增加时间戳，方便后续做时间衰减分析
        msg = json.dumps({
            "role": role,
            "content": content,
            "ts": int(time.time())
        }, ensure_ascii=False)

        async with self.r.pipeline() as pipe:
            await pipe.rpush(key, msg)
            await pipe.ltrim(key, -50, -1)  # 只保留最近50条，防止 List 无限增长
            await pipe.expire(key, 86400)
            await pipe.execute()

    # --- 2. 用户画像管理 (Level 3 基础) ---
    async def update_user_profile(self, user_id: str, traits: dict):
        """
        存入类似: {'name': '张三', 'hero': '李白', 'level': '王者'}
        """
        key = f"herb:profile:{user_id}"
        await self.r.hset(key, mapping=traits)

    async def get_user_summary(self, user_id: str):
        """一次性获取画像和近期事实，用于注入 Prompt"""
        profile = await self.r.hgetall(f"herb:profile:{user_id}")
        # 获取最近存入的 3 条原子事实
        facts = await self.r.smembers(f"herb:facts:{user_id}")
        return {"profile": profile, "facts": list(facts)[:5]}

    # --- 3. 事实存储 (防止摘要丢失细节) ---
    async def store_interest_fact(self, user_id: str, fact: str):
        """存储用户的一个长期兴趣或事实"""
        key = f"herb:facts:{user_id}"
        await self.r.sadd(key, fact)
