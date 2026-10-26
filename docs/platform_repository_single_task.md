# 正式平台 Repository 单任务入口

`run_platform_repository.py` 只用于首轮正式 Repository Group 验收。它复用 Literature/Reader
已验证的 Leader 状态机、AIC/Group/Session/Task 关联、重复 `TaskResult.id` 幂等处理、一次 Start、
一次 Complete、持久化检查点和有界清理。

首轮固定边界：

- 一个明确的 HTTPS GitHub 仓库根地址；
- `repository_analyze` / `repository.analyze` / `code_records.v1`；
- `maxRepositories=1`；
- `executeCode=false`；
- `allowRepositorySearch=false`；
- `requirePaperNotes=false`，且不携带 papers/paperNotes；
- static 模式，不调用 DeepSeek，不执行目标仓库代码；
- Leader 不自动重试 Start。

默认样例是 `examples/group_repository_single_task_v02.json`，目标仓库为
`https://github.com/pypa/sampleproject`。离线预检不会连接平台、clone 仓库或运行 CodeWiki：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_platform_repository.py `
  --leader-aic LEADER_AIC `
  --partner-aic REPOSITORY_AIC `
  --credentials-dir C:\path\to\credentials
```

正式执行必须由吴确认实际 Partner branch/commit、AIC、AMQPS 与 Inbox 已就绪，再增加：

```text
--execute --partner-ready --partner-commit <40位commit> --confirm-after-join
```

程序在 `GROUP_JOINED` 后输出新 Group/Session/Task ID 并等待人工输入 `SEND`。只有 Partner 侧
确认同一 Group 的唯一 `GROUP_JOINED`、且 Worker/Provider 计数仍为 0 后才可放行。默认业务等待
300 秒，最大允许 600 秒。

跨端最终验收还需吴侧提供：static Worker/clone/CodeWiki 实际执行次数、SQLite completed、六字段
Result 回读、canonical JSON SHA-256、`DISBAND/GROUP_LEFT` 和无资源残留证据。Leader 的
`passed=true` 不能替代 Partner formal 审计。
