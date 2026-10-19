# QLoRA 三 Agent 本机 AIP 脱敏验收摘要

日期：2026-10-19。基于本机一次真实运行的 `acceptance.json`、`leader_result.json`、Literature 后端诊断及 Reader/Repository 运行记录整理。原始文件保留在 Git 忽略的 `outputs/`，**不随代码提交**。此摘要不包含凭据、个人路径、PDF 或模型输出全文。

## 实际执行与结果

Planner 经 `AipOrchestrator`、`LocalAipResolver` 和三个本机 Partner 自动执行 `S1→S2→S3`。初始计划只给 QLoRA 标题，没有预填 PDF 或仓库 URL。S1 实际使用 Semantic Scholar（HTTP 200；一次请求），OpenAlex 未调用；Resource Resolver 自动核验 [arXiv PDF](https://arxiv.org/pdf/2305.14314) 和 [artidoro/qlora](https://github.com/artidoro/qlora)（GitHub 搜索候选经 README 中论文标识与标题核验）。S2 动态绑定本轮 S1 PaperRef，复用经身份与内容校验的 26 页 PDF，实际执行 PaperQA + 配置的 DeepSeek。S3 绑定本轮 `paperNotes` 和经验证的 `repositoryUrls`，实际执行 CodeWiki 静态分析 + DeepSeek enrichment。

| 阶段 | AIP taskId | AIP 状态 / Leader | 耗时 | 业务结果与证据 |
| --- | --- | --- | ---: | --- |
| S1 Literature | `task-fc770218-2bd1-44f5-b73c-719854006e77` | `complete→completed` / PASS | 8.71 秒 | `paper_records.v1`：1 篇；PDF、仓库来源经验证 |
| S2 Reader | `task-071f26d7-56ad-4bc6-9646-cf688e81caa7` | `complete→completed` / PASS | 117.54 秒 | `paper_notes.v1`：1 条；论文 evidence 5 条 |
| S3 Repository | `task-93c35abf-065a-47e7-a2c5-4f6f7e592677` | `complete→completed` / PASS | 72.47 秒 | `code_records.v1`：1 条；代码 evidence 38 条、论文 evidence 5 条、双侧关联 5 条 |

总耗时 **206.96 秒**。三阶段均有 `TaskCommand`（`start` / `get` / `complete`）、`TaskResult`、`completed` 状态和 Leader 业务校验。S1→S2 的 PaperRef、S2→S3 的 paperNotes 与 repositoryUrls 自动绑定；三个业务 schema 均有效。Literature 有一条 `SOURCE_URLS_NORMALIZED` warning；三阶段无执行错误。

Repository 静态结果：36 个代码组件、12 条依赖边、5 条 README 运行命令、5 个复现障碍。结构化 `entrypoints=0`，不应把 README 命令说成已验证入口；一条提取命令存在字符编码异常。论文笔记归纳了 NF4、双重量化与分页优化器的内存节省方法、论文报告的实验结果和评估局限；这些是带论文页码证据的分析结果，仍需人工核对原文。论文与代码关联引用了双方有效 evidence ID，属于有证据的静态分析与模型解释。**没有执行 QLoRA 仓库代码，也没有完成论文复现。**

Semantic Scholar 请求 1 次，OpenAlex 0 次；Resource Resolver 外部请求 4 次。DeepSeek 实际请求次数、token 和费用：**unknown**（当前日志未提供完整 usage）；不得从时长反推。Reader 可复用已校验 PDF/embedding 缓存，但本轮的 S2/S3 业务结果为新执行。正式梧桐 ADP 发现、AMQP/Group MQ、平台部署和跨机器运行尚未验收。

本次之后既有离线记录：主测试 **513 PASS**，Literature 独立 **2 PASS**，Reader 独立 **14 PASS**，Repository 独立 **22 PASS**。本摘要整理阶段没有再次运行收费 API 或真实全链。
