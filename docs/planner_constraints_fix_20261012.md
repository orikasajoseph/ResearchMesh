# Planner 约束白名单修复

## 原因与修复

2026-10-12 的 research-live-20261012-161743-937838 中，Planner 自增 paper_read.constraints.requireEvidenceQuotes=true。原 Plan 只做结构及已知字段类型校验，没有约束键白名单；Reader 仅接受 maxPapers，在派发前以 UNSUPPORTED_CONSTRAINT 拒绝。本次原记录显示一次规划请求，未检索、未阅读。

planner_contract.validate_plan 现在补充按 taskType 的封闭白名单：

- literature_search：yearFrom、yearTo、maxResults。
- paper_read：maxPapers。
- repository_analyze：maxRepositories、executeCode、allowRepositorySearch、requirePaperNotes。

外层字段和 Task v0.2 不变；不修改 Zhang Wrapper、AIP 或 Key 配置。未知键和跨任务的约束键明确拒绝，不静默删除。Planner 首次输出不合格时按原机制最多修正一次，仍不合格就失败，不用假计划兜底。提示词明确证据/引用/分析重点写入 goal，不创建 requireEvidenceQuotes、includeEvidence 等新字段。

规划校验的 UnsupportedConstraintError 是 ValueError 子类，直接调用的科研流水线仍保存明确 UNSUPPORTED_CONSTRAINT 错误；不影响已支持的历史约束和 Adapter 边界。

## 本次已保存计划的显式修正

经用户同意，在 outputs/planner-repair-20261012-161743-937838/plan.json 另存一份计划，仅删除上述多余键。goal 原样保留“原文证据（引用原文片段）”，年份、篇数、查询、任务 ID、依赖都不改。

repair.json 记录来源、原文件 SHA-256 和唯一变化。不覆盖原目录、不伪造 pipeline.json，也不把修正当成模型新输出或执行结果。该目录只有计划，因此 --resume 跳过 Planner 后会首次执行真实检索和阅读；阅读仍可能产生多次模型请求及费用。

在项目根目录续跑（需要用户显式执行，本轮修复不会自动发请求）：

```powershell
& "C:\Users\orika\Documents\Codex\rmr-1011-70db\Scripts\python.exe" -X utf8 run_research.py --resume "outputs/planner-repair-20261012-161743-937838" --with-reader --reader-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\reader-upstream-validation-20261011\agents\paper_reader"
```

这只修复约束不匹配；不能保证检索无 429、第一篇有 PDF、下载成功或笔记的证据关联精度，也不代表 AIP/梧桐互联通过。

## 离线验证

- 68 项 Planner/检索/Reader 流水线相关回归测试通过，包括新加的 8 项约束测试。
- 完整验收：281 项自动测试、0 跳过，5 个模拟场景及本地 HTTP 检查通过，记录 outputs/acceptance-20261012-162525-883867。
- 修正计划通过实际续跑加载校验，原文件哈希未变，与原计划相比仅上述键被删除，无假执行检查点。
- 本轮没有调用收费模型、Semantic Scholar 或真实 Reader，未提交或推送 Git。
