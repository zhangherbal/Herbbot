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

        # 1. 初始化切分器
        # 父块：保证语义完整，用于最后喂给大模型
        parent_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        # 子块：保证检索精度，用于向量匹配
        child_splitter = RecursiveCharacterTextSplitter(chunk_size=200, chunk_overlap=40)

        # 2. 执行切分
        parent_docs = parent_splitter.create_documents([text])

        final_to_add = []

        # 统一转换 user_id 和 chat_id 为字符串，防止检索时类型不匹配
        u_id_str = str(user_id)
        c_id_str = str(chat_id) if chat_id else "private"

        print(f"[*] 正在处理文档: {file_name}, 初始父块数: {len(parent_docs)}")

        for i, p_doc in enumerate(parent_docs):
            # 构造父块唯一 ID
            # 如果是评估模式传入了 doc_id，第一块使用原 ID，后续带上后缀
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

            # 基于当前父块切分子块
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

        # 3. 分批次写入数据库 (核心修复：解决 400 参数错误)
        total_count = len(final_to_add)
        # 智谱 API 对单次 Embedding 请求有数量限制，建议 batch 为 16-32
        batch_size = 32

        print(f"[*] 准备分批写入 {total_count} 个片段 (含父子块)...")

        for i in range(0, total_count, batch_size):
            batch = final_to_add[i: i + batch_size]
            # 显式提取 IDs，解决参数匹配问题
            batch_ids = [d.metadata["id"] for d in batch]

            try:
                self.vector_store.add_documents(batch, ids=batch_ids)
                if (i + batch_size) % 128 == 0 or (i + batch_size) >= total_count:
                    print(f"   进度: {min(i + batch_size, total_count)}/{total_count} 已完成")

                # 频率控制：避免请求过快被封禁
                time.sleep(0.5)
            except Exception as e:
                print(f"批次写入失败 (Index {i}): {str(e)}")
                # 遇到 400 错误通常是某个块太长或字符异常，可在此处打印 batch[0].page_content[:50] 调试

        # 4. 重新加载 BM25 索引以包含新数据
        print("[*] 正在同步 BM25 索引...")
        self._load_bm25_from_chroma()

        print(f"《{file_name}》处理入库完成！")
        return total_count
    def query(self, text, user_id, chat_id=None, k=10):
        """
        工业级 RAG 检索流程：
        1. 混合检索（向量 + BM25）
        2. 权限与隔离过滤 (user_id / chat_id)
        3. 智谱 Rerank 精排（带 API 数量限制保护）
        4. 父块回溯 (Parent-Child Retrieval)
        """
        if not self.bm25 or len(self.bm25_docs) == 0:
            self._load_bm25_from_chroma()

        try:
            # --- 1. 混合检索：向量检索 ---
            # 注意：这里我们放宽检索，拿到较多候选块给 Rerank
            search_kwargs = {"k": k * 2}

            # 增加权限过滤逻辑：只检索当前用户或当前群聊的数据，或者是公共管理员数据
            # 如果不需要严格过滤，可以简化 filter
            current_filter = {
                "$or": [
                    {"user_id": str(user_id)},
                    {"chat_id": str(chat_id) if chat_id else "private"},
                    {"is_admin": True}
                ]
            }

            child_vec_docs = self.vector_store.similarity_search(
                text,
                **search_kwargs,
                filter=current_filter
            )

            # --- 2. 混合检索：BM25 检索 ---
            bm25_results = []
            if self.bm25:
                tokenized_query = list(jieba.cut(text))
                bm25_results = self.bm25.get_top_n(tokenized_query, self.bm25_docs, n=k * 2)

            # --- 3. 合并与去重 ---
            combined_children = []
            seen_content = set()
            for d in child_vec_docs + bm25_results:
                if d.page_content not in seen_content:
                    combined_children.append(d)
                    seen_content.add(d.page_content)

            if not combined_children:
                return []

            print(f"DEBUG [第一阶段]: 混合检索完成，候选子块数量 = {len(combined_children)}")

            # --- 4. 智谱 Rerank 精排 (含 API 限制修复) ---
            # 限制发送给 API 的文档数量，防止 HTTP 400 错误 (硬限制 64，建议 50)
            rerank_input_list = combined_children[:50]

            try:
                # 调用精排接口
                reranked_children = self._zhipu_rerank(text, rerank_input_list, top_n=5)
                print(f"DEBUG [第二阶段]: Rerank 成功，返回 Top {len(reranked_children)} 个子块")
            except Exception as e:
                print(f"⚠️ Rerank 接口异常，启动自动降级逻辑: {e}")
                # 如果 Rerank 挂了（如 SSL 报错或限流），直接取混合检索的前 5 个
                reranked_children = rerank_input_list[:5]

            # --- 5. 父块回溯内容 ---
            final_context_docs = []
            seen_parents = set()

            for child in reranked_children:
                p_id = child.metadata.get("parent_id")
                # 兼容性：如果子块本身就是父块或没 parent_id
                target_id = p_id if p_id else child.metadata.get("id")

                if target_id and target_id not in seen_parents:
                    # 使用 where 语法通过 metadata['id'] 进行二次回溯
                    parent_data = self.vector_store.get(where={"id": target_id})

                    if parent_data and parent_data['documents']:
                        doc = Document(
                            page_content=parent_data['documents'][0],
                            metadata=parent_data['metadatas'][0]
                        )
                        final_context_docs.append(doc)
                        seen_parents.add(target_id)
                    else:
                        # 兜底：回溯失败时，直接使用子块（子块内容通常也是完整的或足以回答）
                        final_context_docs.append(child)
                        seen_parents.add(target_id)

                # 只要拿到 3 个高质量的父块上下文，就足以支撑大模型回答
                if len(final_context_docs) >= 3:
                    break

            # --- 6. 最终调试打印 ---
            retrieved_ids = [d.metadata.get('id') for d in final_context_docs]
            print(f"DEBUG [最终输出]: 成功召回父块 ID 列表 = {retrieved_ids}")

            return final_context_docs

        except Exception as e:
            import traceback
            print(f"检索系统崩溃，错误详情:\n{traceback.format_exc()}")
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
