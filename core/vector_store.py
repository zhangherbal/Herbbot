import asyncio
import os
import re
import time
import jieba
import requests
import functools
from rank_bm25 import BM25Okapi
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import ZhipuAIEmbeddings
from langchain_core.documents import Document
from config.settings import ZHIPUAI_API_KEY
import httpx

def safe_api_call(retries=3, delay=1.5):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if i == retries - 1: raise e
                    print(f"⚠️ API 调用波动 ({e}), 正在进行第 {i + 1} 次指数退避重试...")
                    time.sleep(delay * (i + 1))
            return None

        return wrapper

    return decorator


class VectorManager:
    def __init__(self):
        self.embeddings = ZhipuAIEmbeddings(model="embedding-2", api_key=ZHIPUAI_API_KEY)
        self.persist_directory = "./data/chroma_db"
        self.vector_store = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=self.embeddings
        )
        self.bm25 = None
        self.bm25_docs = []
        self._load_bm25_from_chroma()

    def _load_bm25_from_chroma(self):
        all_data = self.vector_store.get(where={"doc_level": "child"})
        if all_data and all_data['documents']:
            self.bm25_docs = [
                Document(page_content=d, metadata=m)
                for d, m in zip(all_data['documents'], all_data['metadatas'])
            ]
            tokenized_corpus = [list(jieba.cut(doc.page_content)) for doc in self.bm25_docs]
            self.bm25 = BM25Okapi(tokenized_corpus)

    def _apply_rrf(self, vector_docs, bm25_docs, k=60):
        """
        RRF 算法实现
        """
        doc_scores = {}

        # 处理向量检索列表
        for rank, doc in enumerate(vector_docs):
            # 使用 content 作为唯一标识进行打分
            content = doc.page_content
            if content not in doc_scores:
                doc_scores[content] = {"score": 0, "doc": doc}
            doc_scores[content]["score"] += 1.0 / (k + rank + 1)

        # 处理 BM25 检索列表
        for rank, doc in enumerate(bm25_docs):
            content = doc.page_content
            if content not in doc_scores:
                doc_scores[content] = {"score": 0, "doc": doc}
            doc_scores[content]["score"] += 1.0 / (k + rank + 1)

        # 按 RRF 分数从高到低排序
        reranked_results = sorted(
            doc_scores.values(),
            key=lambda x: x["score"],
            reverse=True
        )

        # 提取排序后的文档对象
        return [item["doc"] for item in reranked_results]
    @safe_api_call(retries=3)
    async def _zhipu_rerank_async(self, query, docs, top_n=3):
        if not docs: return []

        url = "https://open.bigmodel.cn/api/paas/v4/rerank"
        headers = {"Authorization": f"Bearer {ZHIPUAI_API_KEY}"}
        payload = {
            "model": "rerank-2",
            "query": query,
            "documents": [d.page_content for d in docs],
            "top_n": top_n
        }

        async with httpx.AsyncClient() as client:
            try:
                # 异步发送请求，不阻塞其他用户
                response = await client.post(url, headers=headers, json=payload, timeout=10.0)
                res_data = response.json()

                reranked_docs = []
                for item in res_data.get("results", []):
                    reranked_docs.append(docs[item["index"]])
                return reranked_docs
            except Exception as e:
                print(f"Rerank 异步调用失败: {e}")
                return docs[:top_n]  # 降级逻辑

    def add_document(self, text, user_id, doc_id=None, chat_id=None, is_admin=False, file_name="unknown"):
        """
        父子索引入库逻辑：支持长文档切分、权限隔离、分批写入保护
        """
        import time
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document

        parent_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        child_splitter = RecursiveCharacterTextSplitter(chunk_size=200, chunk_overlap=40)


        parent_docs = parent_splitter.create_documents([text])

        final_to_add = []

        u_id_str = str(user_id)
        c_id_str = str(chat_id) if chat_id else "private"

        print(f"[*] 正在处理文档: {file_name}, 初始父块数: {len(parent_docs)}")

        for i, p_doc in enumerate(parent_docs):

            p_id = doc_id if (doc_id and i == 0) else f"{doc_id or int(time.time())}_p_{i}"

            p_doc.metadata = {
                "id": p_id,
                "doc_level": "parent",
                "user_id": u_id_str,
                "chat_id": c_id_str,
                "source": file_name,
                "is_admin": is_admin
            }
            final_to_add.append(p_doc)

            children = child_splitter.split_documents([p_doc])
            for j, c_doc in enumerate(children):
                c_doc.metadata = {
                    "id": f"{p_id}_c_{j}",
                    "parent_id": p_id,  # 追溯父块的关键 Key
                    "doc_level": "child",
                    "user_id": u_id_str,
                    "chat_id": c_id_str,
                    "source": file_name,
                    "is_admin": is_admin
                }
                final_to_add.append(c_doc)

        total_count = len(final_to_add)

        batch_size = 32

        print(f"[*] 准备分批写入 {total_count} 个片段 (含父子块)...")

        for i in range(0, total_count, batch_size):
            batch = final_to_add[i: i + batch_size]
            batch_ids = [d.metadata["id"] for d in batch]

            try:
                self.vector_store.add_documents(batch, ids=batch_ids)
                if (i + batch_size) % 128 == 0 or (i + batch_size) >= total_count:
                    print(f"   进度: {min(i + batch_size, total_count)}/{total_count} 已完成")

                time.sleep(0.5)
            except Exception as e:
                print(f"批次写入失败 (Index {i}): {str(e)}")

        print("[*] 正在同步 BM25 索引...")
        self._load_bm25_from_chroma()

        print(f"《{file_name}》处理入库完成！")
        return total_count

    async def query(self, text, user_id, chat_id=None, k=10):
        """
        工业级 RAG 检索流程：
        1. 权限过滤
        2. 异步并行混合检索 (Vector + BM25)
        3. RRF 排名融合 (解决分值不可比问题)
        4. 智谱 Rerank 精排
        5. 父块回溯内容
        """
        if not self.bm25 or len(self.bm25_docs) == 0:
            self._load_bm25_from_chroma()

        try:
            # --- 1. 权限与隔离过滤 ---
            current_filter = {
                "$or": [
                    {"user_id": str(user_id)},
                    {"chat_id": str(chat_id) if chat_id else "private"},
                    {"is_admin": True}
                ]
            }

            # --- 2. 混合检索 (并行执行提高效率) ---
            fetch_k = k * 3
            vec_task = asyncio.to_thread(
                self.vector_store.similarity_search,
                text,
                k=fetch_k,
                filter=current_filter
            )
            tokenized_query = list(jieba.cut(text))
            bm25_results = self.bm25.get_top_n(tokenized_query, self.bm25_docs, n=fetch_k) if self.bm25 else []

            child_vec_docs = await vec_task

            # --- 3. RRF 排名融合 ---
            combined_children = self._apply_rrf(child_vec_docs, bm25_results)

            if not combined_children:
                print(f"[Vector] ⚠️ 未找到匹配文档: {text}")
                return []
            # --- 4. 智谱 Rerank 精排 ---
            rerank_input = combined_children[:50]
            try:
                reranked_children = await self._zhipu_rerank_async(text, rerank_input, top_n=5)
                print(f"[Vector] Rerank 完成，召回 Top {len(reranked_children)} 个高质量子块")
            except Exception as e:
                print(f"[Vector] Rerank 失败，启动降级逻辑: {e}")
                reranked_children = rerank_input[:5]
            # --- 5. 父块回溯 (Parent-Child Retrieval) ---
            final_context_docs = []
            seen_parents = set()
            for child in reranked_children:
                p_id = child.metadata.get("parent_id")
                target_id = p_id if p_id else child.metadata.get("id")
                if target_id and target_id not in seen_parents:
                    # 回溯父块获取更完整的上下文
                    parent_data = await asyncio.to_thread(
                        self.vector_store.get,
                        where={"id": target_id}
                    )
                    if parent_data and parent_data['documents']:
                        doc = Document(
                            page_content=parent_data['documents'][0],
                            metadata=parent_data['metadatas'][0]
                        )
                        final_context_docs.append(doc)
                        seen_parents.add(target_id)
                    else:
                        final_context_docs.append(child)
                        seen_parents.add(target_id)

                if len(final_context_docs) >= 4:
                    break
            return final_context_docs
        except Exception as e:
            import traceback
            print(f"[Vector]检索链条崩溃:\n{traceback.format_exc()}")
            return []
    def delete_expired_docs(self):
        """清理过期文档"""
        try:
            current_now = int(time.time())
            expired_data = self.vector_store.get(
                where={"$and": [{"expired_at": {"$ne": 0}}, {"expired_at": {"$lt": current_now}}]}
            )
            ids_to_del = expired_data.get('ids', [])
            if ids_to_del:
                self.vector_store.delete(ids=ids_to_del)
                self._load_bm25_from_chroma()
                print(f"清理了 {len(ids_to_del)} 条过期内容")
        except Exception as e:
            print(f"清理失败: {e}")
