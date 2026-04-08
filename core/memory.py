import time
import json


class RedisMemory:
    def __init__(self, redis_client):
        self.redis = redis_client

    # --- 1. 存储逻辑分级 ---
    async def store_fact(self, user_id: str, content: str, importance="low"):
        """
        importance="high": 存入 Hash (永久画像)
        importance="low": 存入 ZSet (带时间戳的碎片)
        """
        if importance == "high":
            # 这种通常是用户显式要求的，如“记住我叫吴超”
            # 我们简单以内容前4个字作为 Key
            await self.redis.hset(f"user:{user_id}:profile", content[:4], content)
        else:
            # 这种是自动记录的行为，如“询问了百度”
            # score 设为当前时间戳，方便按时间清理
            await self.redis.zadd(f"user:{user_id}:facts", {content: time.time()})

    # --- 2. 读取逻辑分级 ---
    async def get_user_summary(self, user_id: str):
        # 提取永久画像
        profile = await self.redis.hgetall(f"user:{user_id}:profile")
        # 提取最近的 5 条碎片记忆 (按时间戳倒序)
        facts = await self.redis.zrevrange(f"user:{user_id}:facts", 0, 4)

        return {
            "profile": profile or {},
            "facts": [f.decode('utf-8') if isinstance(f, bytes) else f for f in facts]
        }

    # --- 3. 自动化归纳 (Consolidation) ---
    async def consolidate_if_needed(self, user_id: str, llm):
        count = await self.redis.zcard(f"user:{user_id}:facts")
        if count < 20: return  # 攒够20条再归纳

        # 取出最旧的 20 条
        old_facts = await self.redis.zrange(f"user:{user_id}:facts", 0, 19)
        facts_str = "\n".join([f.decode() for f in old_facts])

        prompt = f"你是一个记忆整理员。请将以下琐碎的行为记录归纳为 1 条关于用户偏好的结论：\n{facts_str}"
        res = await llm.ainvoke(prompt)

        # 存入 profile，并清理掉这 20 条
        await self.redis.hset(f"user:{user_id}:profile", f"summary_{int(time.time())}", res.content)
        await self.redis.zremrangebyrank(f"user:{user_id}:facts", 0, 19)
        print(f"[Memory] 已完成用户 {user_id} 的记忆归纳")
