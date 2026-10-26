# Repository 正式平台验收记录（2026-10-26）

## 验收结论

**REPOSITORY_FORMAL_ACCEPTANCE_PASS**

- Group：`rm-repository-5c7a20e6197c45b7bb75734ba30a32b0`
- Session：`session-12fa5ac490ea4ed386da3a3c07689e47`
- Task：`task-1df25892b76b4009bb8c941b4db86487`
- Partner commit：`2564a13767eb9db7e8d10ac8ee752ece5b224008`
- Leader 状态：`accepted -> working -> awaiting-completion -> completed`
- 业务结果：`code_records.v1` 校验通过，sampleproject 静态分析成功

本结论只覆盖上述正式平台单任务。Leader 回传状态与 Partner 原始证据已经交叉核对；三 Agent 正式连续链路仍需另行验收。

## 证据索引

下表中的 `D:\...` 均为吴侧验收机器上的本机证据定位，不是团队部署路径；团队可复用的是本仓库内的文档、代码相对路径和记录的 Git commit。原始运行证据不纳入 Git。

| 证据 | 位置或标识 | 核对结果 |
|---|---|---|
| Git 版本 | commit `2564a13767eb9db7e8d10ac8ee752ece5b224008` | 本轮 Partner 启动版本 |
| Partner formal journal | `D:\research\aic\evidence\literature-369f227c73754043bc9e612c4aab6ca3.jsonl` | 唯一入组、执行、结果持久化、Complete 与完整清理链 |
| SQLite | `D:\research\aic\state\group_tasks.sqlite3` | `(Repository AIC, Session, Task)` 最终为 `completed` |
| Worker 证据目录 | `D:\research\aic\worktrees\reader-repository-readiness\outputs\repository-group-f83cd7c3947845359d5e4b46b3e3a452` | 本轮唯一 Repository Worker 目录 |
| Task 输入 | 上述目录中的 `repository_input.json` | sampleproject、static、单仓库、禁止执行/搜索、不要求 paperNotes |
| Worker 原始结果 | 上述目录中的 `backend_outcome.json` | phase=`completed`，包含完整六字段 Result 与诊断 |
| Worker 日志 | 上述目录中的 `repository_worker.log` | `Repository worker: completed` |
| Leader canonical Result | SHA-256 `f741f66984b0f50e380ff01a39b63b932f19f6b9732bf23bbe2c0580f36d6f80` | 与 Partner 对原始业务 Result 的 canonical 复算一致 |
| Leader 验收记录 | 由赵保存的状态、Result 校验、重复计数和清理结果 | 当前工作区不伪造或复制不存在的 Leader 原始文件 |

原始 journal、SQLite 和 Worker 文件均保留原位；本文件仅建立索引，不覆盖或清理证据。formal journal 文件名沿用历史 `literature-*` 前缀，但 runId 和本轮事件独立。

## 业务执行证据

- Start、`BACKEND_STARTED`、`BACKEND_FINISHED(success)` 各一次，Worker 只启动一次并正常退出。
- 真实 Git 浅克隆：`https://github.com/pypa/sampleproject`。
- 克隆 commit：`621e4974ca25ce531773def586ba3ed8e736b3fc`。
- CodeWiki 2.0.0，固定来源 commit `f0975ea19b17568ceb55cd5422206e1a1ae13c0b`。
- 静态分析结果：12 个组件、7 条依赖边、7 个 artifact、16 条证据。
- `cloneMs=3403.769`、`staticAnalysisMs=590.982`、`totalMs=4077.008`。
- `targetCodeExecuted=false`、`targetDependenciesInstalled=false`、`paperContextCompared=false`。
- `llmEnabled=false`、`modelCalls=0`、`llmEnrichmentMs=0.0`，无 enrichment。
- Result warnings 为许可证及论文/仓库关联未核验声明，不是执行失败。

`modelCalls=0` 是 Worker 内部计数，`llmEnrichmentMs=0.0` 是业务诊断；本轮没有独立 HTTP 请求审计，因此不将内部计数扩展解释为“所有网络请求为零”。Git 浅克隆确实产生了网络请求。

## Result 一致性

原始业务 Result 顶层严格为六字段：`schemaVersion`、`subtaskId`、`taskType`、`summary`、`warnings`、`items`。formal journal 记录 `originalSchemaValid=true`、`RESULT_SAVED` 和 `RESULT_READBACK_VALID`。

对 `backend_outcome.json` 中的原始业务 Result 使用：

- UTF-8；
- JSON 对象键递归排序；
- 紧凑分隔符 `,` 和 `:`；
- 非 ASCII 字符不转义；

得到 SHA-256：

`f741f66984b0f50e380ff01a39b63b932f19f6b9732bf23bbe2c0580f36d6f80`

该值与 formal journal 的 `originalSha256` 及 Leader canonical SHA-256 完全一致。

## 最终状态与清理

- `TASK_COMMAND(start)`、Complete 各一次；没有重复 Backend 或重复 Result 证据。
- SQLite 最终状态：`completed`。
- 清理链：`DISBAND_RECEIVED -> LEAVE_ENTERED -> CLEANUP_ENTERED -> GROUP_LEFT -> CLEANUP_SUCCEEDED -> LEAVE_FINISHED`。
- `residual=[]`、`failures=[]`、`consumerCallbackCancelled=false`、`groupResourcesCleared=true`。
- Worker 和 Git clone 进程均无残留。
- `INBOX_RESTART_SUCCEEDED` 后 AMQPS 5671、heartbeat 和 Inbox 恢复正常。

## 已解决运行问题

首轮 invitation 因 Windows 控制台选择状态导致长时间调度停顿，旧 invitation 延迟处理后失败；退出选择状态并使用全新 Group 后，本轮成功。该历史问题不影响本轮 PASS，但正式演示时应避免传统 PowerShell QuickEdit/选择模式阻塞进程。
