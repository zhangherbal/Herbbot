import os
import re
import time
import jieba
import requests
from rank_bm25 import BM25Okapi
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import ZhipuAIEmbeddings
from config.settings import ZHIPUAI_API_KEY


class VectorManager:
    def __init__(self):
        # 1. 初始化 Embedding
        self.embeddings = ZhipuAIEmbeddings(
            model="embedding-2",
            api_key=ZHIPUAI_API_KEY,
        )
        self.persist_directory = "./data/chroma_db"
        self.vector_store = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=self.embeddings
        )

        # 2. BM25 相关内存缓存
        self.bm25 = None
        self.bm25_docs = []  # 存储 LangChain Document 对象映射
        self._load_bm25_from_chroma()

    def _load_bm25_from_chroma(self):
        """初始化时，从 Chroma 加载数据到 BM25"""
        all_data = self.vector_store.get()
        if all_data and all_data['documents']:
            self.bm25_docs = all_data['documents']
            tokenized_corpus = [list(jieba.cut(doc)) for doc in self.bm25_docs]
            self.bm25 = BM25Okapi(tokenized_corpus)

    def _zhipu_rerank(self, query, docs, top_n=3):
        """
        修改点：增加异常处理和超时设置，
        因为 main.py 是异步的，这里如果卡住会影响全局。
        """
        if not docs: return []
        try:
            # 如果只有一条结果，没必要 Rerank
            if len(docs) <= 1: return docs[:top_n]

            url = "https://open.bigmodel.cn/api/paas/v4/rerank"
            headers = {"Authorization": f"Bearer {ZHIPUAI_API_KEY}"}
            # 限制传入 Rerank 的文档数量，防止接口超时
            payload = {
                "model": "rerank-2",
                "query": query,
                "documents": [d.page_content for d in docs[:15]],  # 最多传15条
                "top_n": top_n
            }
            # 设置较短的 timeout
            res = requests.post(url, headers=headers, json=payload, timeout=5).json()
            return [docs[item["index"]] for item in res.get("results", [])]
        except Exception as e:
            print(f"[Rerank Timeout/Error]: {e}, fallback to original results")
            return docs[:top_n]

    def add_document(self, text, user_id, chat_id=None, is_admin=False, file_name="unknown"):
        # 1. 切分：保持较小的 chunk 以提高检索精度
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=80)
        raw_docs = text_splitter.create_documents([text])

        expire_time = 0 if is_admin else int(time.time()) + 86400
        clean_docs = []

        for i, doc in enumerate(raw_docs):
            content = doc.page_content.strip()
            if len(content) <= 2: continue

            content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', content)
            doc.page_content = content
            doc.metadata = {
                "source": file_name,
                "user_id": str(user_id),
                "chat_id": str(chat_id) if chat_id else "private",
                "is_admin": is_admin,
                "expired_at": expire_time,
                "chunk_id": i
            }
            clean_docs.append(doc)

        # --- 核心修改：分批存入 Chroma ---
        if clean_docs:
            batch_size = 50  # 智谱限制 64，我们设 50 留出余量
            for i in range(0, len(clean_docs), batch_size):
                batch = clean_docs[i:i + batch_size]
                try:
                    self.vector_store.add_documents(batch)
                    print(f"[*] 正在写入文档 {file_name} 的第 {i}-{i + len(batch)} 个片段")
                except Exception as e:
                    print(f"[!] Batch 写入失败: {e}")
                    continue  # 某一批失败，继续下一批

            # 全部存完后，同步更新一遍 BM25 索引
            self._load_bm25_from_chroma()

        return len(clean_docs)

    def query(self, text, user_id, chat_id=None, k=10):
        try:
            current_chat = str(chat_id) if chat_id else "private"

            # --- 过滤器：权限 + 有效期 ---
            search_filter = {
                "$and": [
                    {"$or": [{"expired_at": {"$eq": 0}}, {"expired_at": {"$gt": int(time.time())}}]},
                    {"$or": [{"is_admin": {"$eq": True}},
                             {"$and": [{"user_id": {"$eq": str(user_id)}}, {"chat_id": {"$eq": current_chat}}]}]}
                ]
            }

            # --- 第一路：向量检索 ---
            vec_docs = self.vector_store.similarity_search(text, k=k, filter=search_filter)

            # --- 第二路：BM25 关键词检索 ---
            bm25_results = []
            if self.bm25:
                tokenized_query = list(jieba.cut(text))
                # 拿到最相关的文本内容
                top_n_texts = self.bm25.get_top_n(tokenized_query, self.bm25_docs, n=k)
                # 简单包装回对象（此处实际应用中建议通过 ID 匹配更精准）
                from langchain_core.documents import Document
                bm25_results = [Document(page_content=t) for t in top_n_texts]

            # --- 合并与重排序 ---
            # 去重：以内容为准
            seen = set()
            combined_docs = []
            for d in vec_docs + bm25_results:
                if d.page_content not in seen:
                    combined_docs.append(d)
                    seen.add(d.page_content)

            # 调用智谱 Rerank 拿到最精准的 Top 3
            final_docs = self._zhipu_rerank(text, combined_docs, top_n=3)

            return "\n---\n".join([d.page_content for d in final_docs])

        except Exception as e:
            print(f"检索失败: {e}")
            return ""

    def delete_expired_docs(self):
        """定时清理逻辑：增加日志反馈"""
        try:
            current_now = int(time.time())
            # 使用更精准的过滤
            expired_data = self.vector_store.get(
                where={
                    "$and": [
                        {"expired_at": {"$ne": 0}},
                        {"expired_at": {"$lt": current_now}}
                    ]
                }
            )
            ids_to_del = expired_data.get('ids', [])
            if ids_to_del:
                self.vector_store.delete(ids=ids_to_del)
                # 必须重建 BM25 索引，否则会搜到已删除的内容
                self._load_bm25_from_chroma()
                print(f"[Scheduled Task] 清理了 {len(ids_to_del)} 条过期索引")
            else:
                print("[Scheduled Task] 无过期资料需要清理")
        except Exception as e:
            print(f"[Cleanup Error]: {e}")
