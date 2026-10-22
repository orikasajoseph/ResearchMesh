# 正式平台 Literature 单任务联调

Leader 入口为 `run_platform_literature.py`，通信逻辑为 `group_literature_leader.py`。
单任务入口已推送基线为 `zhao/planner-integration@3090f7b`；正式业务任务仍待双方在线验收。

## 已完成和本轮范围

2026-10-20 正式 Leader ACL 写入和删除通过。2026-10-21 invitation-only 已由双方核对通过：
`rm-invite-e2636e5f4077438eb875a62a337540f3`，Leader 入群确认和清理均成功，未发送 TaskCommand。

新的单任务入口会另建随机临时 Group，使用正式证书连接 AMQPS，邀请指定 Literature AIC，
等待入群确认，再发送一次 Start。任务为七字段 v0.2 Literature，第一轮限定 maxResults=1。
校验六字段业务产物后保存记录，发送一次 Complete，收到同一任务的 completed 后才能算协议完成。

本轮不调用 Planner，不调用 Leader 本机检索 Wrapper，不启动本地临时 Partner，不执行 Reader/Repository。
Partner 由显式 AIC 指定，尚未覆盖 ADP 自动发现。原 `run_group_invitation.py` 保持仅邀请用途。

## 与吴先确认

1. 上轮 DISBAND 后 Literature 已退出旧群、Inbox 继续监听。
2. 吴明确同意本轮发送一个真实 literature_search 任务，白名单仍为正式 Leader AIC。
3. 确认实际运行的 Partner commit，已绑定约定业务入口，接收七字段 Task、返回六字段 Result。
   此处记录的是吴声明的版本，Leader 不会自动验证远程部署版本。
4. Literature 后端超时约 120 秒，Leader 默认总任务期限 180 秒；准备保留真实 Provider 调用诊断。
   按当前双方约定，先等待吴完成统一业务入口绑定与 SQLite 持久化去重回归，再确认部署 commit。
   单次任务成功只证明该次执行；重复消息和重启恢复能力需另查吴侧专项测试。

默认任务位于 `examples/group_literature_single_task_v02.json`：
检索题为 LoRA: Low-Rank Adaptation of Large Language Models 的论文，最多1篇。
不要求 Reader 全文，不分析仓库。可定位的检索记录还需双方人工核对是否匹配目标论文。

## 第一步 离线预检

在桌面 ResearchMesh 项目中执行：

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 run_platform_literature.py `
  --leader-aic "1.2.156.3088.1.BUPT.RECKYJ.Q77J9R.52YOLZ.11P6" `
  --partner-aic "1.2.156.3088.1.BUPT.RECKYJ.4K8V3F.0113BD.00TL" `
  --credentials-dir "C:\Users\orika\Documents\Codex\ResearchMesh-platform"
```

不带 `--execute` 时只检查本机证书、CN、AIC、任务字段，未联网。
`passed=true` 仅表示预检通过；`protocolCompleted=false`、`businessReady=false` 为预期。
无需登录网页，不需要重新申请证书。使用现有 `requirements-mq-check.txt` 工具环境。

## 第二步 双方就绪后真实单任务

此步骤会触发 Partner 的真实检索和外部 API 配额。将下面占位值换成吴确认的实际 commit；
不能把之前参考分支的 commit 当作已经确认的部署版本。

```powershell
$partnerCommit = "替换为吴确认的7至40位十六进制commit"
.\.venv-mq-check\Scripts\python.exe -X utf8 run_platform_literature.py `
  --leader-aic "1.2.156.3088.1.BUPT.RECKYJ.Q77J9R.52YOLZ.11P6" `
  --partner-aic "1.2.156.3088.1.BUPT.RECKYJ.4K8V3F.0113BD.00TL" `
  --credentials-dir "C:\Users\orika\Documents\Codex\ResearchMesh-platform" `
  --partner-commit $partnerCommit --execute --partner-ready --confirm-after-join
```

程序确认 GROUP_JOINED 后会暂停并输出 Group、Session、Task ID。将三个 ID 发给吴；
只有收到吴侧同一 Group 的 GROUP_JOINED 确认后，输入大写 `SEND` 并按 Enter，程序才发送 Start。
默认最多等待 600 秒，可用 `--release-timeout` 调整；等待超时、终端关闭或输入其他内容时，
程序不发送 TaskCommand，并清理本轮 Group。
正常事件顺序：GROUP_JOINED_CONFIRMED → TASK_START_PUBLISHED → TASK_STATE_... →
BUSINESS_RESULT_VALIDATED → TASK_COMPLETE_PUBLISHED → TASK_COMPLETED_CONFIRMED → CLEANUP_FINISHED。
accepted/working 中间状态可能因后台交付顺序而省略；必须有产物校验和 Complete/completed。

最终 `protocolCompleted=true`、`businessReady=true`、`cleanupSucceeded=true`、`passed=true`。
这些标志仅为 Leader 自动检查结果。双方还需核对论文标题及链接是否对应指定 LoRA、
吴侧同一任务的真实 Provider 日志，以及 DISBAND 接收和离群回调，才能共同宣布正式验收通过。
Leader 发送 DISBAND 后短暂等待并删除本轮资源，cleanupSucceeded 不代表收到 Partner 离群确认。
Partner 失败时只保存白名单业务错误码，不保存任意远端错误文本；没有可识别错误码时按任务 ID 查吴侧日志。
空检索结果可以完成协议，但 `businessReady=false`、`passed=false`，保留 NO_RESULTS，不编造论文。
错误、超时或 Ctrl+C 均尝试发送 DISBAND 并清理本轮随机群组资源和 ACL。
清理失败会保留结果但 passed=false。只操作本轮临时群，不删除持久 Inbox 或共享 Exchange。

`outputs/platform-literature/<本轮目录>/` 内有 record.json、report.md，收到有效产物后另存 result.json。
record.json 在外部操作前及每个状态变化时原子保存，含有效结果和标识符，不含私钥或邀请 token。
超时和断线后 remoteStateUnknown=true，禁止直接重跑同一请求；先核对 Partner 任务记录。
Start 发布出错也不自动重发，避免发送响应丢失但后台已经执行时重复调用。

## 验收证据

官方 SDK 启用 AMQP user_id 身份绑定；Leader 另核对 AIC、Group、Session、Task ID。
使用现有共享 Task/Result 校验，拒绝模拟数据、额外业务状态、错误类型、超量结果、提前 completed 和变更产物。
Leader 不读取 Partner 的 API Key，`providerExecutionVerified=false` 保留为未独立验证；
结合吴侧同一任务的 Provider 日志后再记录真实检索来源，不能凭返回 JSON 推断真实 API 调用。

## 离线回归

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_platform_literature tests.test_group_invitation -v
.\.venv\Scripts\python.exe -X utf8 verify_project.py
```

离线测试只使用内存传输和替身，无真实平台连接、论文 API、PDF 或 LLM。

2026-10-21 本轮开发验收：新增 28 项离线用例；完整 712 项自动测试（0 跳过）、5 个模拟场景与本机 HTTP 检查通过。
记录目录为 `outputs/acceptance-20261021-220556-467710/`。
实际 `.venv-mq-check` 环境使用正式 Leader 证书运行默认离线预检通过，记录为
`outputs/platform-literature/20261021-220504-ed86ba62/record.json`，networkAttempted=false、taskCommandsSent=0。
尚未执行新的正式平台 Literature 业务任务。
完整回归还修正了一条旧对齐测试：历史发布记录已改用 localCommits，不再读取已删除的 workingTreeChangesPublished。
