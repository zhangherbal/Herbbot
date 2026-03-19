import redis.asyncio as redis
import json


class RedisMemory:
    def __init__(self, host='localhost', port=6379, db=1,password=123456):
        # 使用异步 Redis 客户端
        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            password=password,
            db=db,
            decode_responses=True
        )
        self.r = redis.Redis(connection_pool=self.pool)

    async def get_history(self, session_id: str, limit=10):
        """获取最近 N 轮对话"""
        key = f"chat_history:{session_id}"
        # 获取列表最后 limit 条数据
        data = await self.r.lrange(key, -limit, -1)
        return [json.loads(m) for m in data]

    async def add_message(self, session_id: str, role: str, content: str):
        """存入新消息并设置过期时间"""
        key = f"chat_history:{session_id}"
        msg = json.dumps({"role": role, "content": content}, ensure_ascii=False)

        # 管道操作：存入消息 + 设置过期时间 (如24小时)
        async with self.r.pipeline(transaction=True) as pipe:
            await pipe.rpush(key, msg)
            await pipe.expire(key, 86400)
            await pipe.execute()

    async def clear_history(self, session_id: str):
        await self.r.delete(f"chat_history:{session_id}")