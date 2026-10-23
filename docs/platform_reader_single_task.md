# 正式平台 Reader 单任务联调

Leader 入口为 `run_platform_reader.py`，复用已通过 Literature 验收的 Group、身份关联、
单次 Start/Complete、结果校验、人工放行和清理实现。该入口只接受一个七字段
`paper_read` Task：`maxPapers=1`，且 PaperRef 必须包含实际可访问的 `pdfUrl`。
默认样例为 `examples/group_reader_single_task_v02.json`，只阅读 LoRA 论文；不调用
Planner、Literature 或 Repository。

Reader 正式 AIC：
`1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN`。

吴声明的 Reader Partner 启动基线为
`wu/project-release-readiness@479fefdd980b7388f529ad84f617f89002b8042e`，内置
Paper Reader Wrapper 来源为 `c730052b348bdffea62fd6a3305dcf556355dc52`。正式运行前仍须
由吴再次确认实际启动 commit、`AMQPS_CONNECTED`、`INBOX_LISTENING` 和新的 formal 审计日志。

## 离线预检

```powershell
cd "C:\Users\orika\Desktop\智创赛\ResearchMesh"
.\.venv-mq-check\Scripts\python.exe -X utf8 run_platform_reader.py `
  --leader-aic "1.2.156.3088.1.BUPT.RECKYJ.Q77J9R.52YOLZ.11P6" `
  --partner-aic "1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN" `
  --credentials-dir "C:\Users\orika\Documents\Codex\ResearchMesh-platform"
```

不带 `--execute` 时只做本机证书和任务预检，不连接 AMQPS，不下载 PDF，不调用模型。
预期 `passed=true`，但 `protocolCompleted=false`、`businessReady=false`。

## 双方在线后的正式命令

Reader Worker 预算约 600 秒，后端超时 660 秒，Partner Group 外层超时 690 秒；Leader
默认任务等待 750 秒。不要把它降到 Literature 的 180 秒。

```powershell
$partnerCommit = "479fefdd980b7388f529ad84f617f89002b8042e"
.\.venv-mq-check\Scripts\python.exe -X utf8 run_platform_reader.py `
  --leader-aic "1.2.156.3088.1.BUPT.RECKYJ.Q77J9R.52YOLZ.11P6" `
  --partner-aic "1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN" `
  --credentials-dir "C:\Users\orika\Documents\Codex\ResearchMesh-platform" `
  --partner-commit $partnerCommit --task-timeout 750 `
  --execute --partner-ready --confirm-after-join
```

程序出现 `GROUP_JOINED_CONFIRMED` 后会输出全新的 Group、Session、Task ID 并等待人工放行。
把三个 ID 发给吴；只有吴确认同一 Group 的 `GROUP_JOINED` 后，才向该进程标准输入发送
一行 `SEND`。不要在普通 PowerShell 提示符中执行 `SEND`，也不要重复发送 Start。

如需文件门控以便另一个终端放行，可保持上述 Python 进程的标准输入连接到一个只使用一次的
控制文件轮询；控制文件必须为空启动，确认入群后仅追加一行 `SEND`。每轮均使用新的控制文件、
Group、Session 和 Task ID。

## 双方验收证据

Leader 自动检查必须满足：七字段 Task、同 AIC/Group/Session/Task 的 TaskResult、六字段
`paper_notes.v1` Result、`analysisLevel=fulltext`、source 与 evidence URL 对应输入 PDF、一次
Complete、completed 和本轮 Leader 清理成功。

吴侧 formal 审计还需证明：同一 Task 只创建一个 Worker、SQLite `completed`、Result 保存并
回读校验、Partner 与 Leader Result SHA-256 一致、实际 PDF/模型调用计数、首次 DISBAND 清理、
`GROUP_LEFT`、`failures=[]`、`residual=[]`。任一超时或断线后都先核对 SQLite，不自动重发。

正式验收器由吴侧运行：

```powershell
python verify_analysis_acceptance.py --task-type paper_read
```

Reader 单任务通过后再准备 Repository；本轮不得顺带触发 Repository。

## 离线回归

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest `
  tests.test_platform_literature tests.test_platform_reader `
  tests.test_platform_release_process tests.test_group_invitation -v
```

这些测试不连接平台、不读取 PDF、不调用模型。

2026-10-24 本机正式 AIC 离线预检通过，记录为
`outputs/platform-reader/20261024-001739-262e2b39/`：`passed=true`、
`networkAttempted=false`、`taskCommandsSent=0`。完整项目验收通过 732 项自动测试（0跳过）、
5 个模拟场景与本地 HTTP 检查，记录为
`outputs/acceptance-20261024-001747-614123/`。这些记录证明 Leader 入口与本机配置就绪，
不代表 Reader Partner 已入群或真实全文阅读已完成。
