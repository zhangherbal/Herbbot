import asyncio
import os
import re
import torch
import time
import jieba
from rank_bm25 import BM25Okapi
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# 强制离线模式
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_DATASETS_OFFLINE'] = '1'


class VectorManager:
    def __init__(self):
        print("   [Vector] 正在启动 Herb终极召回优化版...")
        self.embeddings = HuggingFaceEmbeddings(
            model_name="BAAI/bge-small-zh-v1.5",
            model_kwargs={'device': 'cuda'},
            encode_kwargs={'normalize_embeddings': True}
        )
        self.persist_directory = "./data/chroma_db"
        self.vector_store = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=self.embeddings
        )

        self.reranker_path = "./models/reranker"
        try:
            self.reranker_tokenizer = AutoTokenizer.from_pretrained(self.reranker_path, local_files_only=True)
            self.reranker_model = AutoModelForSequenceClassification.from_pretrained(
                self.reranker_path, local_files_only=True).to('cuda').eval()
            print("   [Vector] Reranker 精排就位")
        except Exception as e:
            print(f"   [Error] Reranker 缺失: {e}")
            self.reranker_model = None

        self.bm25 = None
        self.bm25_docs = []
        self._load_bm25_from_chroma()

    def _load_bm25_from_chroma(self):
        data = self.vector_store.get(where={"doc_level": "child"})
        if data and data['documents']:
            self.bm25_docs = [Document(page_content=d, metadata=m) for d, m in
                              zip(data['documents'], data['metadatas'])]
            tokenized_corpus = [list(jieba.cut(re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', d.page_content))) for d in
                                self.bm25_docs]
            self.bm25 = BM25Okapi(tokenized_corpus)

    def add_document(self, text, user_id, file_name="unknown", llm=None):
        """
        针对序号排版的教师简介PDF进行物理隔离切分
        """
        # 1. 基础清理：提取文件名作为默认主题，统一换行符
        subject_default = re.sub(r'(老师|简介|详情|介绍|\.txt|\.pdf|\.docx)', '', file_name)
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # 2. 【核心】物理切分逻辑：匹配 行首数字 + 点 + 空格 (例如 "44. ")
        # 使用正向预查 (?=\n\d+\.?) 确保分割时不会丢失序号本身
        teacher_blocks = re.split(r'\n(?=\d+[\.、]?\s)', text)

        # 如果没切开（可能第一行没换行符），尝试匹配行首
        if len(teacher_blocks) <= 1:
            teacher_blocks = re.split(r'(?<=^)\d+[\.、]?\s', text)

        final_to_add = []
        # 父块（完整档案）和子块（检索片段）的切分器
        c_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=100)

        for i, block in enumerate(teacher_blocks):
            clean_block = block.strip()
            if len(clean_block) < 20:
                continue  # 跳过太短的干扰项

            # 3. 提取当前块的真实老师姓名
            # 匹配模式：数字序号后紧跟的 2-4 个汉字
            name_match = re.search(r'\d+[\.、]?\s*([\u4e00-\u9fa5]{2,4})', clean_block)
            current_teacher = name_match.group(1) if name_match else subject_default

            # 4. 创建 Parent Document (整份档案)
            # 即使向量库返回这个，Herb 也能看到这位老师的全部信息（包括末尾的邮箱）
            p_id = f"{int(time.time())}_t{i}"
            parent_content = f"【{current_teacher}】的完整教师档案：\n{clean_block}"
            p_doc = Document(
                page_content=parent_content,
                metadata={
                    "id": p_id,
                    "doc_level": "parent",
                    "user_id": str(user_id),
                    "subject": current_teacher
                }
            )
            final_to_add.append(p_doc)

            # 5. 创建 Child Documents (检索片段)
            # 强制在每个片段开头注入老师姓名，防止 RAG 检索时“断章取义”
            child_chunks = c_splitter.split_text(clean_block)
            for j, chunk in enumerate(child_chunks):
                child_doc = Document(
                    page_content=f"教师【{current_teacher}】信息片段：{chunk.strip()}",
                    metadata={
                        "id": f"{p_id}_c_{j}",
                        "parent_id": p_id,
                        "doc_level": "child",
                        "user_id": str(user_id),
                        "subject": current_teacher
                    }
                )
                final_to_add.append(child_doc)

        # 6. 批量存入向量库并刷新 BM25 索引
        if final_to_add:
            self.vector_store.add_documents(final_to_add)
            if hasattr(self, '_load_bm25_from_chroma'):
                self._load_bm25_from_chroma()

        return len(final_to_add)

    async def _analyze_intent(self, text, llm):
        if not llm: return "NORMAL"
        prompt = (
            f"分析意图，仅返回标签：NEGATION(否定), AGGREGATION(汇总), MULTI_ATTR(多属性), NORMAL(普通)。\n问题：{text}")
        try:
            res = await llm.ainvoke(prompt)
            tag = res.content.strip().upper()
            return tag if tag in ["NEGATION", "AGGREGATION", "MULTI_ATTR"] else "NORMAL"
        except:
            return "NORMAL"

    async def _advanced_query_transform(self, text, intent, llm):
        """针对不同意图采用不同的改写深度"""
        if not llm: return [text]
        if intent == "MULTI_ATTR":
            prompt = f"请将以下复杂问题拆解为 2 个独立的属性搜索词（例如：既是教授又是博导 -> 教授, 博导）。直接输出词，逗号分隔。\n问题：{text}"
        elif intent == "NEGATION":
            prompt = f"这是一个否定逻辑。请提取被排除项以外的所有正向可能属性关键词。例如：不是教授 -> 讲师, 副教授, 研究员。直接输出关键词，逗号分隔。\n问题：{text}"
        else:
            prompt = f"请将该问题改写为 2 个适合搜索的短语。直接输出，每行一个。\n问题：{text}"

        try:
            res = await llm.ainvoke(prompt)
            lines = res.content.replace(',', '\n').split('\n')
            return [l.strip() for l in lines if l.strip()] + [text]
        except:
            return [text]


    async def _local_rerank_async(self, query, docs, top_n):
        if not docs or not self.reranker_model: return docs[:top_n]
        pairs = [[query, d.page_content] for d in docs]

        def _inf():
            with torch.no_grad():
                inputs = self.reranker_tokenizer(pairs, padding=True, truncation=True, return_tensors='pt',
                                                 max_length=512).to('cuda')
                logits = self.reranker_model(**inputs).logits.view(-1).float()
                return [d for s, d in sorted(zip(logits, docs), key=lambda x: x[0], reverse=True) if s > -4.0]

        return await asyncio.to_thread(_inf)

    async def query(self, text, user_id, llm=None, chat_id=None, k=5):
        try:
            # 1. 意图分析（保持不变）
            intent = await self._analyze_intent(text, llm)

            #：更安全的 User ID 处理 ---
            # 如果是公共文档查询，建议尝试兼容模式
            target_user_id = str(user_id)
            # 这里的 filter 结构如果报错，可以先尝试最简单的单条件测试
            base_filter = {"user_id": target_user_id}

            # 2. 关键词提炼（这步一定要加，否则原句检索率极低）
            search_queries = [text]
            if llm:
                # 提炼一个纯净的关键词，如 "艾勇"
                kw_res = await llm.ainvoke(f"提取关键词，只输出词：{text}")
                kw = kw_res.content.strip()
                if kw and kw not in search_queries:
                    search_queries.insert(0, kw)  # 放在最前面优先搜

            v_results = []
            for q in search_queries:
                # 修正：如果这里拿不到数据，去掉 filter 试试
                res = self.vector_store.similarity_search(
                    q,
                    k=20,  # 稍微多拿点给后面排
                    filter={"user_id": target_user_id}
                )
                v_results.extend(res)

            # 3. BM25 召回（保持不变）
            b_results = self.bm25.get_top_n(list(jieba.cut(text)), self.bm25_docs, n=40) if self.bm25 else []

            # 4. 融合与精排（去掉那个硬编码的 -4.0 阈值进行测试）
            combined = self._apply_rrf_with_penalty(v_results, b_results)

            # 临时调试：如果到这里 combined 还是空的，说明前面的 similarity_search 就没出结果
            if not combined:
                print(f"   [Debug] 向量检索和BM25均无结果。当前库总数: {self.vector_store._collection.count()}")
                # 降权尝试：去掉所有过滤条件的盲搜
                combined = self.vector_store.similarity_search(text, k=k)

            # 5. 精排 (放宽限制)
            reranked = await self._local_rerank_async(text, combined[:40], top_n=20)

            # 6. 回溯 Parent (核心防呆设计)
            final_context, seen_subs = [], set()
            for doc in (reranked or combined):  # 如果精排挂了，用混合检索保底
                m = doc.metadata
                sub = m.get("subject", "unknown")
                p_id = m.get("parent_id")

                if sub not in seen_subs:
                    # 如果有 parent_id 且是子节点，回溯
                    if p_id and m.get("doc_level") == "child":
                        p_res = self.vector_store.get(ids=[p_id])
                        if p_res and p_res['documents']:
                            final_context.append(
                                Document(page_content=p_res['documents'][0], metadata=p_res['metadatas'][0]))
                        else:
                            # 如果回溯失败，直接用当前 child 片段，别让它空着
                            final_context.append(doc)
                    else:
                        final_context.append(doc)

                    seen_subs.add(sub)

                if len(final_context) >= k: break

            return final_context

        except Exception as e:
            import traceback
            traceback.print_exc()
            return []

    def _apply_rrf_with_penalty(self, v_docs, b_docs, penalty_word=None, k=60):
        """带有逻辑降权的混合检索融合算法"""
        doc_scores = {}

        # 1. 向量得分融合
        for rank, doc in enumerate(v_docs):
            content = doc.page_content
            if content not in doc_scores:
                doc_scores[content] = {"score": 0.0, "doc": doc}
            doc_scores[content]["score"] += 1.0 / (k + rank + 1)

        # 2. BM25得分融合 (赋予关键词匹配更高权重)
        for rank, doc in enumerate(b_docs):
            content = doc.page_content
            if content not in doc_scores:
                doc_scores[content] = {"score": 0.0, "doc": doc}
            doc_scores[content]["score"] += 1.5 / (k + rank + 1)

        # 3. 【核心补丁】否定词逻辑降权
        if penalty_word:
            for content in doc_scores:
                # 如果文档中包含被排除的词，将其综合得分降低 90%
                # 这样它不会被“物理删除”，但会排在那些不含此词的文档后面
                if penalty_word in content:
                    doc_scores[content]["score"] *= 0.1

        # 4. 排序并返回
        sorted_results = sorted(doc_scores.values(), key=lambda x: x["score"], reverse=True)
        return [item["doc"] for item in sorted_results]

    def delete_expired_docs(self):
        try:
            current_now = int(time.time())
            expired_data = self.vector_store.get(
                where={"$and": [{"expired_at": {"$ne": 0}}, {"expired_at": {"$lt": current_now}}]})
            ids_to_del = expired_data.get('ids', [])
            if ids_to_del:
                self.vector_store.delete(ids=ids_to_del)
                self._load_bm25_from_chroma()
                print(f"清理了 {len(ids_to_del)} 条过期内容")
        except Exception as e:
            print(f"清理失败: {e}")
