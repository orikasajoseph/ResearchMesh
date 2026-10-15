# 赵侧最新联调交接（2026-10-15）

发布分支：`zhao/planner-integration`。本次提交包含此前未发布的第三周支持、
指定仓库静态 AIP 链路及复用 Reader 的上下文对照；完整版本号以 Git 提交为准。
没有合并 main，没有修改张/吴的远端分支。

## 已完成与实测

1. 指定 GitHub 仓库 → 现有 Leader/LocalAipResolver → 官方 ACPs SDK 2.2.0 本机 AIP →
   Repository Wrapper → 真实 CodeWiki 静态分析 → Markdown/JSON 报告。
2. 复核旧的真实检索/Reader/PDF/AIP 记录，使用 bind_task_input 将已完成 S2 的全文笔记绑定到 S3，
   只新执行 Repository AIP，保留旧 Reader AIP taskId、依赖来源和源文件哈希。
3. 复合适配层实际消费笔记，输出方法/发现/局限与仓库摘录的词面/标识符线索、两侧出处和笔记哈希。
   不是只回显笔记，也不是静默丢弃上下文。
4. DeepSeek 增强保持关闭；无新模型/Reader/论文检索请求，无目标代码执行、目标依赖安装或复现验证。
5. 推送前完整回归：502 项测试、0 跳过，5 个模拟场景及本地 HTTP 检查通过。
   本机记录：`outputs/acceptance-20261015-214002-459837`。

用户亲自运行成功的两轮记录：

- 独立 sampleproject：`outputs/aip-repository-20261015-183108-e1139b5a`。
- 旧 LoRA Reader → 新 LoRA Repository：`outputs/aip-research-handoff-20261015-212909-5eeab910`。

这些原始记录仅留本机，不上传仓库。它们是本机真实 AIP 记录，不是梧桐平台验收。

## 代码和接口对齐

- 业务 Task 仍为 v0.2；`repository_analyze` / `requiredSkill=repository.analyze` / `code_records.v1`。
- Leader/Partner 之间仍用官方 AIP；Partner 顶层业务内容仍只有七字段。
- `requiredSkill`、`dependsOn` 保留在 Leader 内部；已完成的 S2 不重跑。
- 共享模型、导出 Schema、Planner、shared common/llm 本轮未修改。
- `agents/repository/researchmesh_repository/*.py` 八个文件原样来自张
  `be99b31b3c8411ea88a96d08b0337d6049882046`，未修改其核心实现或 CodeWiki upstream。
- 赵侧在 `repository_paper_context.py` 新增复合适配：张原生 Wrapper 只收到其支持的 repositoryUrls，
  论文上下文由该适配层单独消费。原生 `repository_adapter.to_repository_request` 仍拒绝不支持的非空上下文。
- `items[].data.paperComparison` 是赵侧可选结果扩展，需要张/吴确认后再作为团队共同输出约定，
  不能视为已改变全组冻结 Schema。
- 关键路径：`run_aip_repository.py`、`run_reader_repository.py`、`aip_repository_partner.py`、
  `repository_aip_worker.py`、`reader_repository_handoff.py`、`repository_adapter.py`、
  `repository_paper_context.py`、`resolver/local.py`。

## 不要误报的边界

- 当前对照为确定性词面/标识符匹配，不是语义层面的实现对应、实验一致性或复现难度评估。
- `paperContextCompared=true` 不等于 `semanticReproductionAnalysis=true`；后者保持 false。
- `readerReused=true`、`newModelCalls=0`、`freshThreeAgentEndToEnd=false`。
- 尚未验证一次全新的自然语言→检索→阅读→仓库三 Agent 流程。
- LocalAipResolver 是显式本地注册，不是平台 ADP。正式在线、身份、跨机器、ADP/MQ 仍需与吴联调。
- 没有用这次新测试替代张原 22 项 CodeWiki/增强验收或吴的正式平台验收。

## 张/吴可以如何复测

在各自的干净或单独 integration 工作区引入本提交；不要覆盖未提交改动或盲目合并旧共享协议。
先准备项目既有 Leader Python 3.12 环境，然后：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_project.py
.\.venv\Scripts\python.exe -X utf8 scripts/setup_repository_static.py --execute
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --execute
```

新建的 `.venv-repository` 与 Leader/Reader 隔离；固定 CodeWiki commit
`f0975ea19b17568ceb55cd5422206e1a1ae13c0b`。这是静态专用的精简依赖环境，不是完整 CodeWiki CLI 环境。
安装/分词缓存预热不需要模型 Key。完整步骤见 `docs/repository_local_aip_static.md`。

论文对照入口为 `run_reader_repository.py --reader-source <自己的有效原记录目录> --repository <明确仓库URL> --execute`。
原 PDF/科研输出未进 Git，因此其他电脑不能直接照抄赵的 outputs 路径。
详见 `docs/reader_repository_handoff.md`；缺源记录时先复测独立仓库流程，不用假数据冒充真实 S2。

## 请队友对齐

- 张：确认复合适配层、paperComparison 可选扩展与原生 Wrapper 的边界；协调后续语义增强如何消费论文证据，
  避免在两个分支重复改造。需要模型调用时另行明确请求预算、费用和失败降级。
- 吴：基于当前七字段任务/产物接正式 ADP、在线和 MQ，提供对应分支/commit、脱敏结果及失败场景；
  AIC/证书路径按本机配置，Key/私钥/证书不发群、不进 Git。

`docs/week3_progress_20261014.md` 为 10 月 14 日历史快照；当前状态以本说明及两份运行文档为准。
