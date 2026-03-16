# 🤖 HerbBot | 集成化智能 QQ 助手

> **Current Status:** `v1.2.0 - Stable`  
> **Style Profile:** `CSGO Theme` / `Tactical Assistant`  
> **Developer:** [Zhangherbal/Github]

HerbBot 是一款基于 **Model Context Protocol (MCP)** 与 **LangChain** 架构开发的 QQ 机器人。它不仅拥有 CSGO 风格的趣味互动，更集成了混合检索 RAG 知识库与自动化浏览器操作能力。

---

## 📅 开发日志 (Changelog)

### 🛠️ Update : 2026-03-16
> **"Mission Accomplished" - 核心功能补完**

- **[RAG 强化]** 引入 **ZhipuAI Rerank-2** 算法，结合向量检索与 **BM25 混合检索**，大幅提升知识提取精度。
- **[MCP 扩展]** 成功打通 `Puppeteer` 节点，赋予 AI 模拟点击、网页抓取及实时资料搜集能力。
- **[技能模块]** 上线 `微博热搜` 功能（基于官方 API 联动），实现非爬虫式的信息流获取。
- **[规划]** 预备导入《计算机学院大学生自救指南》作为核心 RAG 知识储备。

---

### 🛠️ Update : 2026-03-15
> **"Tactical Upgrade" - 架构深度演进**

- **[逻辑大脑]** 全面接入 `LangChain` 与 `LangGraph` 状态机，实现 ReAct 多步推理逻辑。
- **[身份隔离]** 实现 RAG 权限管理系统，支持根据用户 ID/群号进行文档存储隔离。
- **[文档总结]** 接入 PDF 自动解析与摘要功能，支持大文件异步入库。
- **[提醒系统]** 优化 `[SEC:xxx]` 标签触发机制，提升定时任务稳定性。

---

### 🛠️ Update : 2026-03-12
> **"Initial Spawn" - 项目原型建立**

- **[UI 风格]** 确立 CSGO 术语库风格，完成基础对话流程。
- **[基础技能]** - 🔔 **定时提醒**：战术部署执行器。
  - 📦 **开箱模拟**：CSGO 模拟开箱抽奖功能。
  - 🌤️ **环境侦察**：实时天气查询系统。
- **[MCP 初探]** 完成 Model Context Protocol 的基础 Stdio 管道搭建。

---

## 🚀 核心功能展示 (Core Features)

| 模块 | 功能描述 | 技术栈 |
| :--- | :--- | :--- |
| **RAG 知识库** | 身份隔离存储、混合检索、Rerank 排序 | ChromaDB + BM25 |
| **MCP 浏览器** | 读取官网内容+提取通知公告 | Puppeteer + Node.js |
| **CSGO Skills** | 提醒、开箱、微博热搜、天气查询 | API + Python |
| **PDF 处理** | 异步下载、文本切片、自动摘要生成 | pypdf + ThreadPool |
技术栈： langgraph + langgraph + MCP + RAG(Rerank+混合检索) + 
---

## 未来计划 (Roadmap)

- [ ] **架构优化**：减少 LangGraph 冗余跳转，缩短 LLM 响应时延（TTFT）。
- [ ] **知识库扩充**：完成大学生自救指南后导入
- [ ] **多模态增强**：接入图像识别，支持直接分析网页截图内容（烧钱，大概率没钱做。。）。
- [ ] **MCP优化**：MCP的fetch速度太慢，后续可以自己手写优化。
---

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
---

> *"Counter-Terrorists Win. Bot is online."*
