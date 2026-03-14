import os
import re
import time
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import ZhipuAIEmbeddings
from config.settings import ZHIPUAI_API_KEY


class VectorManager:
    def __init__(self):
        self.embeddings = ZhipuAIEmbeddings(
            model="embedding-2",
            api_key=ZHIPUAI_API_KEY,
        )
        self.persist_directory = "./data/chroma_db"
        self.vector_store = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=self.embeddings
        )

    def add_document(self, text, user_id, chat_id=None, is_admin=False, file_name="unknown"):
        """
        新增参数：
        - user_id: 上传者 ID
        - chat_id: 群聊 ID (私聊可为 None)
        - is_admin: 是否为管理员（管理员上传的资料设为永久）
        """
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=200, chunk_overlap=50)
        raw_docs = text_splitter.create_documents([text])

        # 设置过期时间：普通用户资料 24 小时后过期 (当前时间戳 + 86400)
        # 管理员资料设置为 0，代表永久
        expire_time = 0 if is_admin else int(time.time()) + 86400

        clean_docs = []
        for doc in raw_docs:
            content = doc.page_content.strip()
            if len(content) > 2:
                content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', content)
                doc.page_content = content

                # 注入身份元数据
                doc.metadata = {
                    "source": file_name,
                    "user_id": str(user_id),
                    "chat_id": str(chat_id) if chat_id else "private",
                    "is_admin": is_admin,
                    "expired_at": expire_time
                }
                clean_docs.append(doc)

        print(f"[*] [{file_name}] 有效片段: {len(clean_docs)}，身份隔离入库中...")

        batch_size = 50
        for i in range(0, len(clean_docs), batch_size):
            batch = clean_docs[i: i + batch_size]
            try:
                self.vector_store.add_documents(batch)
            except Exception as e:
                print(f"[!] 批次入库失败: {e}")

        return len(clean_docs)

    def query(self, text, user_id, chat_id=None, k=3):
        """
        查询时进行身份过滤：
        1. 允许搜到管理员上传的所有资料 (is_admin == True)
        2. 允许搜到当前用户在当前场景上传的资料
        """
        try:
            # 构建 Chroma 的 Metadata 过滤器
            # 这里的逻辑是：(是管理员发的) OR (是我发的 AND 场景匹配)
            current_chat = str(chat_id) if chat_id else "private"

            search_filter = {
                "$or": [
                    {"is_admin": {"$eq": True}},
                    {
                        "$and": [
                            {"user_id": {"$eq": str(user_id)}},
                            {"chat_id": {"$eq": current_chat}}
                        ]
                    }
                ]
            }

            docs = self.vector_store.similarity_search(
                text,
                k=k,
                filter=search_filter
            )
            return "\n".join([d.page_content for d in docs])
        except Exception as e:
            print(f"检索过滤失败: {e}")
            return ""

    def delete_expired_docs(self):
        """定时清理逻辑：删除所有已过期的非管理员文档"""
        try:
            current_now = int(time.time())
            # 找到所有 expired_at != 0 且小于当前时间的文档
            # 注意：Chroma 原生 delete 复杂过滤支持度有限，通常建议按 ID 删除
            # 这里演示逻辑，实际操作中建议在 add 时记录 id
            all_data = self.vector_store.get()
            ids_to_del = []
            for idx, meta in enumerate(all_data['metadatas']):
                exp = meta.get('expired_at', 0)
                if exp != 0 and current_now > exp:
                    ids_to_del.append(all_data['ids'][idx])

            if ids_to_del:
                self.vector_store.delete(ids=ids_to_del)
                print(f"[*] 已自动清理 {len(ids_to_del)} 条过期临时资料")
        except Exception as e:
            print(f"清理过期资料失败: {e}")