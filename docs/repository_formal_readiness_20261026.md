# Repository 正式平台就绪度与验收结果（2026-10-26）

## 最终状态

**REPOSITORY_FORMAL_ACCEPTANCE_PASS**

Repository 已从准备阶段进入正式验收完成状态。完整原始证据索引见 `docs/repository_formal_acceptance_20261026.md`。

## 已验证配置

- AIC：`1.2.156.3088.1.BUPT.RECKYJ.13NQBJ.YM4WBJ.0E0X`，与证书 CN/SAN 一致。
- 分支：`wu/project-release-readiness`。
- commit：`2564a13767eb9db7e8d10ac8ee752ece5b224008`。
- Wrapper：当前工作树 `agents/repository`，通过独立 PowerShell 进程级 `RM_REPOSITORY_WRAPPER_ROOT` 指定；共享 `.env` 未修改。
- Python 3.12.10；CodeWiki 2.0.0，来源 commit `f0975ea19b17568ceb55cd5422206e1a1ae13c0b`。
- Task：七字段 `repository_analyze`，skill=`repository.analyze`，输出=`code_records.v1`。
- 模式：static；单仓库；不搜索其他仓库；不执行目标代码；不安装目标依赖；DeepSeek 关闭。
- 超时：Worker 180 秒，外层 Backend 210 秒；正式单任务约 5.3 秒完成，不代表所有仓库时长。
- 共享 SQLite、formal audit、MQ 断线容错、持久去重、Result 持久化和 Group cleanup 已接入。

## 正式验收结果

- Group：`rm-repository-5c7a20e6197c45b7bb75734ba30a32b0`。
- sampleproject commit：`621e4974ca25ce531773def586ba3ed8e736b3fc`。
- 12 个组件、7 条依赖边、16 条证据。
- `llmEnabled=false`、`modelCalls=0`、`llmEnrichmentMs=0.0`。
- 六字段 Result 和 `code_records.v1` 校验通过。
- canonical SHA-256：`f741f66984b0f50e380ff01a39b63b932f19f6b9732bf23bbe2c0580f36d6f80`。
- SQLite=`completed`；唯一 Start/Backend/Complete；清理完整且无残留。

内部模型计数不是独立 HTTP 审计；Git 浅克隆是本轮允许且实际发生的网络请求。

## 对下一阶段的意义

Repository 单任务不再是阻塞。下一阶段需验证正式三 Agent 连续链路，尤其是：

- S2 fulltext note 通过现有 `input.paperNotes` 绑定到 S3；
- Repository 使用 `requirePaperNotes=true` 的既有 static 对照路径；
- 不新增 Schema、不启用 enrichment；
- Leader 保存三阶段关联、聚合、幂等和失败阻断证据。

详细准备清单见 `docs/three_agent_formal_chain_readiness_20261026.md`。

## 保留的运行注意事项

- 不复用历史 Group、Session、Task。
- 传统 Windows PowerShell 不得停留在 QuickEdit/文字选择状态。
- 超时后先查 SQLite、formal journal 和 Worker，不自动重派。
- 当前工作区没有赵侧生产 Leader runner 命令；必须由赵提供，不能以本地入口替代正式验收。
