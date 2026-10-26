# Reader 正式平台验收记录（2026-10-26）

## 验收结论

**READER_FORMAL_ACCEPTANCE_PASS**

- Group：`rm-reader-14165cc0f0b04e9f9b718aa3ee54d7d6`
- Session：`session-1be9e09f11ea422e8ddc356e43114d23`
- Task：`task-aa97db4972914d8bace1ef6201f92bc4`
- Partner commit：`2564a13767eb9db7e8d10ac8ee752ece5b224008`
- Leader 状态：`accepted -> working -> awaiting-completion -> completed`
- 业务结果：处理论文 1 篇，六字段 Result 校验通过，`warnings=0`

本结论同时依据 Partner 原始证据与 Leader 回传结果。它只覆盖上述单一 Group、Session、Task，不代表 Repository 或三 Agent 正式连续链路已经验收。

## 证据索引

下表中的 `D:\...` 均为吴侧验收机器上的本机证据定位，不是团队部署路径；团队可复用的是本仓库内的文档、代码相对路径和记录的 Git commit。原始运行证据不纳入 Git。

| 证据 | 位置或标识 | 核对结果 |
|---|---|---|
| Git 修复版本 | commit `2564a13767eb9db7e8d10ac8ee752ece5b224008` | 包含 Reader 缓存修复和 MQ 断线容错修复 |
| Partner formal journal | `D:\research\aic\evidence\literature-0b11385053e841be875918c3ebe80cdf.jsonl` | 本轮 Group 的完整协议、结果持久化与清理事件 |
| SQLite | `D:\research\aic\state\group_tasks.sqlite3` | 对应 Task 最终状态为 `completed`，未发现本轮重复 Backend |
| Worker 证据目录 | `D:\research\aic\worktrees\reader-repository-readiness\outputs\reader-group-960c773478cc40eb9a89147ffefb57b2` | 含绑定任务、PDF 解析、输入、Worker journal 和日志 |
| Worker journal | 上述目录中的 `reader_journal.json` | phase=`completed`，fulltext 分析完成，自动重试为 0 |
| Worker 日志 | 上述目录中的 `reader_worker.log` | Worker 正常完成并退出 |
| PDF 证据 | 上述目录中的 `pdf_resolution.json` | 26 页、1,609,513 bytes；SHA-256 `e9a0d3128767db616085dc0f4e6e455e672e89af823e8ed1282793682787395a` |
| Partner 原始 Result 哈希 | SHA-256 `a658ed719b3c8110c6eae091a42cd10a746e5b211420fd13d5e7b34713998640` | 对六字段 JSON 按 key 排序、紧凑 UTF-8 序列化计算 |
| Leader Result 哈希 | SHA-256 `9441ab0423a646020fd5ca7fceeb053c854aa935b37d01b2dffd3b1fcfae6c03` | 对同一六字段对象按插入顺序、紧凑 UTF-8 序列化计算 |
| Leader 验收记录 | 由赵保存的状态、结果摘要和清理证据 | Result 校验成功，Start/Complete 各一次，重复 Result 计数为 0 |

原始证据均保留在原位置；本文件只建立索引，不复制、覆盖或清理日志与数据库。

## Partner 侧状态核对

- formal journal 中本轮 `GROUP_JOINED` 和 Start 各出现一次。
- `TASK_RESERVATION` 为新任务，Backend 只启动一次并成功结束。
- PDF 校验、embedding、PaperQA fulltext、generation 和 summary 业务链完成。
- Result 已先持久化，再读取校验并交付；Result 为六字段结构，`items=1`、`warnings=0`。
- SQLite 在唯一 Complete 后进入 `completed`。
- 清理链完整记录 `DISBAND_RECEIVED`、`GROUP_LEFT`、`CLEANUP_SUCCEEDED`、`LEAVE_FINISHED`。
- Group 残留资源为空，Worker 正常退出，Inbox 后续恢复监听。
- 本轮没有自动重试或重复业务执行证据。

Provider 的 generation/summary 独立 HTTP 请求次数未单独持久化，因此不把缺少计数解释为 0 或 1；已能确认的是 Worker 记录 `automaticRetries=0`，且没有第二次 Backend 执行。

## Result 哈希差异说明

两端哈希针对同一解析后的六字段 Result。Partner 的原始摘要使用排序键序列化，Leader 保存摘要对应插入顺序序列化。以 Partner 已有 Result 按 Leader 的插入顺序生成紧凑 UTF-8 JSON，可以精确复现 Leader SHA-256。因此差异仅来自 JSON 字段顺序，不是业务内容不一致。

## 已解决问题

- embedding 缓存路径绑定问题：由 commit `68bde55d3c45ef5a45e9855d67f7b89789d70325` 修复并完成定向验证。
- MQ 断线导致 consumer callback 取消业务的问题：由 commit `2564a13767eb9db7e8d10ac8ee752ece5b224008` 修复；19/19 项定向测试通过，并由本轮正式任务验证断线容错后的完整生命周期。

以上两项归入已解决问题，不再作为当前项目阻塞。
