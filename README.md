# 🤖 HerbBot | 集成化智能 QQ 助手

> **Current Status:** `v1.2.0 - Stable`  
> **Style Profile:** `CSGO Theme` / `Tactical Assistant`  
> **Developer:** [Zhangherbal/Github]

HerbBot 是一款基于 * **LangChain** 架构开发的 QQ 机器人。骚骚的。

---

## 📅 开发日志 (Changelog)
### 🛠️ Update : 2026-04-08
重构了架构，总结整体流程。
- **[RAG]** 结合MultiQuery，混合检索，父子索引，RRF算法，rerank,身份隔离存储，并根据RAG的语料进行RAG结构优化（支持扫描件但是入库时间长一些），设计了精确事实，边界测试，模糊表达测试试验，(Hit@5): 88.83%

- **[Memory]** 设计了由“短期-中期-长期”组成的记忆体系，滑动窗口维护短期记忆，中期记忆利用 Redis ZSet 构建行为代谢层，通过 TTL 自动清理机制对带时间戳的行为碎片进行 7 天滚动删除，有效过滤长周期交互产生的噪声；长期记忆基于 Redis Hash 存储结构化用户画像，并引入异步记忆归纳策略，通过后台 LLM 定期“反思”将低维碎片升维总结，降低长期记忆冗余度。

- **[Graph结构]** 利用低温模型进行逻辑决策与多轮推理，配合高温模型实现高度拟人化的表达。核心流程由“规划-执行-审查-染色”闭环构成：Planner 节点负责任务的原子化拆解，实现本地 Tool Use、远程 MCP 与 RAG 的动态路由；Critic 节点则通过反馈控制回路，对工具输出结果进行语义评估，自主决定迭代探索或任务终结；在数据处理上使用 <DATA_BLOCK> 结构化协议，统一了多源异构数据的交互标准，并挂载异步记忆节点实现执行链路与记忆代谢的解耦。

- **[SKILLS]** 支持热插拔的SKILLS，自己实现了定时器，CSGO模拟开箱，今日语录，微博热搜查询，天气查询等。
  
-  **[MCP]** 支持网页内容抓取。
 
---

## 未来计划 (Roadmap)
看OpenClaw代码，看看能不能还原一个HerbClaw。其次有QQ平台的限制，无法完成一些功能，除此之外需要学习agent评测。
由于SKills较少，目前调用工具幻觉率极低，但是Skills增多之后的幻觉处理问题，希望在HerbClaw复现中实现。


## 📷 运行预览
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/f90e1458-2dfc-4a62-afc7-8f0120f50c7a" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/78a978f8-2590-4f6a-ab53-6f0f4970b197" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/8f021d3d-fe23-41f5-96db-e44346f1790f" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/3dcdd552-7e4d-4f5b-a7fd-60f5b05274f1" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/57cc8af3-f5bb-49bc-9585-aa6206d2e928" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/f6c825f7-c4ba-4ae1-b538-8bc98bf13412" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/5c999171-4d48-4772-8ee7-2f7048f9cf6e" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/dffa444c-538e-4751-8134-b56f1a40d41f" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/59fc635d-72b5-4414-ad9e-e3dfdf6a476d" />
<img width="600" height="400" alt="Image" src="https://github.com/user-attachments/assets/30aad4f4-d297-40d8-b6ac-2fde77fdb84f" />

---

> *"Counter-Terrorists Win. Bot is online."*
