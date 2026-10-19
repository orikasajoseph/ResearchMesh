# 给赵：三 Agent 与 Planner 本机 AIP 集成交接

范围：2026-10-19 通过的一次本机 QLoRA 三 Agent AIP 联调，以及本轮 fetch 后对 `origin/zhao/planner-integration@4a39ba2` 的比较。该最新赵分支是推荐的唯一 Planner 基线；其 `planner.py`、`input_binding.py`、`aip_orchestrator.py` 与当前分支内容一致。当前分支相对它有 12 个独有提交，另缺少赵的 `2f2fb0c`、`4a39ba2`。正式合并前请共同确定 `aip_business_partner.py`、`result_contract.py`、本机 Resolver 与三个 Partner 适配文件的归属；本分支不替换 Planner 核心。

## 技能与合同

| 阶段 | Wrapper 与 Partner | skill / Task 类型 | 输出 |
| --- | --- | --- | --- |
| S1 | `agents/literature/researchmesh_literature/`、`aip_literature_partner.py` | `literature.search` / `literature_search` | `paper_records.v1` |
| S2 | `agents/paper_reader/researchmesh_paper_reader/`、`aip_reader_partner.py`、`reader_aip_worker.py` | `paper.analyze` / `paper_read` | `paper_notes.v1` |
| S3 | `agents/repository/researchmesh_repository/`、`aip_repository_partner.py` | `repository.analyze` / `repository_analyze` | `code_records.v1` |

Leader 计划含 `requiredSkill`、`dependsOn`；传给 Partner 的业务 Task 保持 `schemaVersion=0.2` 及**七个**顶层字段：`schemaVersion`、`subtaskId`、`taskType`、`goal`、`input`、`constraints`、`outputSchema`。`requiredSkill` 和 `dependsOn` 只留在 Leader，不能跨 Partner 边界。Partner 结果由 Leader 用现有合同校验，然后发出 AIP `complete` 并收到 `completed`。本机三个阶段均已完成该生命周期。

Planner 归一化的 PaperRef **六字段**为 `paperId`、`title`、`doi`、`url`、`pdfUrl`、`repositoryUrls`。Literature 原始 paper record 还可能有作者、摘要、诊断等字段，不能把这些误当成跨 Agent 的 PaperRef 字段。S1 若有可定位 PDF，`input_binding.bind_task_input()` 从直接依赖的 `paper` item 生成 S2 `input.papers`；Reader 无固定 `reader_input.json` 前置要求，收 Task 后由 Worker 准备独立任务输入。S3 从直接依赖的 `paper_note` item 取得 `paperNotes` 及其 `source` PaperRef，从 PaperRef 取得 `repositoryUrls`；本地 `aip_repository_partner.py` 会筛选必要笔记和证据并传给 Repository。无论文笔记时，S3 保留仓库级分析兼容行为；无可信仓库 URL 时，由现有约束停止 S3，不能编造地址。S1 即使没有仓库 URL，仍可把有效 PDF 交给 S2。

## 本机运行与故障边界

`run_aip_three_agent.py` 是**QLoRA 定向验收入口**：内置确定性测试计划，仅在 S1 查询里放标题，并通过真正的 `Planner`、`AipOrchestrator`、`LocalAipResolver` 和三个 Partner 执行。默认只做免费预检，`--health` 只启动 Partner 查 `/health`，`--execute` 才执行一次真实付费全链。它不是通用 Planner 生产入口。单阶段验收工具包括 `run_aip_literature.py`、`run_aip_reader.py`、`run_aip_repository.py`、`run_aip_s2_s3.py`，不应作为正式编排代码的替身。

本机 `run_aip_three_agent.py` 的 AIP 阶段等待预算分别为 S1 130 秒、S2 675 秒、S3 180 秒；Orchestrator 总任务预算 700 秒；Partner 进程启动健康等待 20 秒。Literature 后端本机预算约 120 秒，Reader Worker 600 秒，Repository 后端约 180 秒。实际 S2 用时 117.54 秒、S3 用时 72.47 秒，平台配置应容纳这些延迟并保留有限轮询。S1/回退检索双源失败时停止下游；S2 失败时停止 S3；S3 失败时保留已成功的 S1/S2 结果和诊断。区分检索失败、资源缺失、Partner/Worker 错误和 AIP 通信错误，不要将任何缺失结果伪造成完成。

离线验证入口：`python -m pytest tests/test_aip_three_agent_offline.py tests/test_s2_s3_paper_binding.py tests/test_aip_literature.py tests/test_aip_dynamic_reader.py tests/test_aip_repository_integration.py`。独立测试位于各 Agent 的 `tests/`。近期已有主测试 513、Literature 2、Reader 14、Repository 22 项 PASS；本交接文档不代表重跑了测试。

## 正式合并建议

把三个 Agent Wrapper/依赖/测试、三个业务 AIP Partner 以及 `run_aip_three_agent.py`、`run_aip_s2_s3.py` 等本机回归入口纳入交付 diff；脚本明确标记为本机开发验收工具。`main` 尚无同一套 Planner v0.2，不能以旧 `main` 为当前集成基线，也不能把赵的历史归为张的新增工作。

赵的 `2f2fb0c` 已加入**另一条 Repository 接缝**：预置 `repository_input.json`、`repository_aip_worker.py` 的静态 CodeWiki Worker、`repository_paper_context.py` 及自己的 `aip_repository_partner.py`；其静态 Worker 明确禁用模型，而当前通过真实验收的 Repository Partner 使用同一 Wrapper 的静态分析 **加 DeepSeek 论文代码 enrichment**，并动态接收 Task。两条路径的用途不同，不能用静态接缝直接覆盖已验收 S3。共同祖先之后双方同改 13 个路径，其中业务差异为 Repository README、`agent.py`、`enrichment.py`、`schemas.py`，以及 `aip_business_partner.py`、`aip_repository_partner.py`、`run_aip_literature.py`、`run_aip_repository.py`。建议赵保留唯一 Planner 与调度入口，正式任务调用张的现有三 Agent 业务 Partner；赵的静态 Worker 可作为隔离测试模式，前提是复用同一 Repository Wrapper 并明确模式选择。`resolver/local.py` 与 `resolver/local_aip.py` 只作为本机测试适配对照，不建设第二个生产调度器。上述同路径实现取舍请赵先确认，本轮未合并。

与吴的平台 `aip/` 桥接按业务七字段、三种 schema、AIP 生命周期与证据 ID 逐项对齐；正式发现/通信交由吴，本机 loopback Resolver 继续作为开发回归入口。
