# Literature Group 正式验收审计交接

开发分支：`wu/literature-acceptance-audit`。
开发工作树：`D:\research\aic\worktrees\literature-acceptance-audit`。
基线：`a619422`。从干净的正式工作树创建；未覆盖现有改动。
当前实现尚未进行新的正式联调，不能据此宣布五项正式验收通过。

第三次 Task `task-e88431cbb7054355a8c19e3078219832` 保持原始成功记录。
没有重跑该 Task、修改旧 SQLite 行，或补写历史 HTTP / GROUP_LEFT / 清理日志。
验收器明确拒绝将该历史 Task 作为下一次正式验收对象。

## 修改文件

| 文件 | 用途 |
| --- | --- |
| `aip/literature_audit.py` | 独占创建每进程 JSONL；追加、锁、fsync；异步 Task 上下文；Result 脱敏、摘要、落盘回读 |
| `aip/audited_group_client.py` | 应用内 SDK 子类，观察消息异常、退群入口、清理结果和真实资源引用 |
| `aip/group_literature_partner.py` | 注入审计；GROUP_JOINED、可信 DISBAND、GROUP_LEFT；应用退出另行记录 |
| `aip/group_literature_handler.py` | 关联 Task；SQLite reservation 决策、Result 保存、completed 写后回读、失败事件 |
| `business_execution.py` | 真正 backend 调用前后事件；异常和取消也记录结束 |
| `semantic_http.py` | 真正 `client.send(request)` 前后持久化；逐 Task 请求序号 |
| `run_group_literature.py` | 正式运行创建审计文件，打印文件位置 |
| `verify_literature_acceptance.py` | 只读查询 SQLite 和 JSONL；五项判定；独占写入新的验收报告 |
| `scripts/test_literature_audit_offline.py` | 禁止真实 HTTP transport、MQ、DNS、外网连接的离线回归运行器 |
| `tests/test_literature_acceptance_audit.py` | 审计、实际 SDK 模拟清理、HTTP MockTransport、失败与重复执行测试 |
| `docs/literature_acceptance_audit.md` | 本交接文档、人工启动与验收流程 |

没有修改安装的 acps_sdk、七字段 Task、六字段 Result、AIP 生命周期或 SQLite 表及去重语义。
正式入口必须传入 AuditJournal；原有离线测试兼容路径可不传审计，此时不具备验收证据。

## 证据格式与判定

所有正式审计和验收报告默认位于仓库外 `D:\research\aic\evidence\`。
每次进程启动独占创建 `literature-<run UUID>.jsonl`，以二进制追加写入，每条记录 fsync。
不复用旧文件、不截断、不修复残缺尾行；损坏或不完整证据只能判失败。
每条记录包含 runId、mode、顺序号和 UTC 时间。跨进程分别写文件，不竞争同一个 JSONL。

| 验收项 | 持久化证据及规则 |
| --- | --- |
| SQLite completed | `SQLITE_COMPLETED` 事件包含写后回读状态，验收器再次以 `mode=ro` 精确查询 Agent/Session/Task |
| 实际 HTTP 仅一次 | 恰好一对 `HTTP_STARTED` / `HTTP_FINISHED`，同 requestId、逐 Task 序号 1、状态 200、无异常；来自 httpx.send 入口 |
| 六字段 Result | `RESULT_SAVED` 保存脱敏六字段副本、原始 canonical JSON SHA256、脱敏副本 SHA256、原始契约校验结果；`RESULT_READBACK_VALID` 指向记录序号；验收时重新校验副本 |
| DISBAND 与清理 | 顺序为 `DISBAND_RECEIVED` → `LEAVE_ENTERED` → `CLEANUP_ENTERED` → `GROUP_LEFT` → `CLEANUP_SUCCEEDED` → `LEAVE_FINISHED`；SDK 在清理内执行 GROUP_LEFT 回调，所以成功事件在回调之后 |
| 无重复/异常/残留 | 同 Group 只有一个 Session/Task；backend 开始/结束各一次且成功；无 Task/SDK/清理失败；Group 字段清空，保留的原 channel/owned connection 对象确已关闭 |

重复 Start 的 `TASK_RESERVATION=duplicate` 是成功去重证据，不等于重复业务执行；验收按 backend 实际开始次数判定。
执行时序还要求加入 Group → backend 开始 → HTTP 开始/结束 → backend 结束 → Result 保存/回读 → SQLite completed → DISBAND。
Group 级清理事件通过同一 Group 的唯一 Task 关联；管理命令的 session 单独记作 managementSessionId，不冒充业务 Session。

HTTP 只持久化关联 ID、请求 ID/序号、白名单或脱敏端点、HTTP 状态、耗时和固定异常分类。
不持久化查询内容、Key、Authorization、请求头、响应头或响应正文。已有内存诊断不被复制进审计。
所有端点都经过计数；路径中的论文 ID 等内容会脱敏，计数不受影响。
请求开始前先落盘；若进程在开始记录后崩溃，缺失结束记录会拒绝验收，不把意图当作成功请求。

Result 的摘要、论文标题、原始 URL、扩展 data 和 warning 文本均不写入副本。
副本的论文标识为摘要，URL 为明确的 `https://evidence.invalid/redacted/...` 占位地址，不是论文来源。
原始 Result 在保存前使用现有 Pydantic Schema 和 `validate_result` 关联校验。
回读后再次执行相同契约校验。原始摘要只能用于以后持有原始 Result 时比对，不能还原原文。
来源固定为 `partner_redacted_copy`，报告固定声明 `partner_audit_not_leader_cache`。
这不是历史 Leader 原始缓存的副本或回读证明；如需验证下一次 Leader 自身缓存，应另保存 Leader 的原始回读证据。

SDK 子类调用原有 super 方法，不抢先退群，不改 `_pending_leave`，不改 ACK 顺序。
对 SDK 吞掉的 consumer cancel / queue delete / leave status 异常，只读取日志模板中的固定事件码，绝不格式化异常参数。
若 SDK warning 日志被禁用、级别屏蔽或配置过滤器，保守拒绝清理成功证明。
检查 Group 资源时排除独立 Inbox 资源；退出 Group 和结束整个 AMQPS 应用是不同动作。
正常退群不会设置应用停止信号，不要求强制退出进程。现有 SDK 清理可能关闭共享连接，
原有 `INBOX_RESTART_REQUIRED` 提示保留；应用存活不代表 Inbox 已自动恢复或能够立即加入下一组。

## 离线验证

运行命令（只用于离线测试）：

```powershell
Set-Location D:\research\aic\worktrees\literature-acceptance-audit
& D:\research\ResearchMesh\.venv\Scripts\python.exe scripts/test_literature_audit_offline.py
```

Windows asyncio 需要本机临时回环 socketpair，沙箱可能阻断而卡在初始化；可在允许本机回环的终端运行。
运行器阻断真实 HTTP transport、aio_pika connect/connect_robust、DNS 和外部 socket，
只允许 asyncio 所需的临时本机端口。不需要真实 API Key 或正式 MQ。

最终完整回归：149 项，148 通过，1 项基线已有失败；其中新增审计测试 14 项全部通过。
范围包括原有 Group durable handler/store、生命周期、统一 executor/wiring、Semantic HTTP、
Literature adapter/resources、AIP bridge、Wu integration、research pipeline。
新增测试覆盖完整模拟 Group、真正 HTTP MockTransport、异常状态/取消、并发关联、业务超时、
重复 Start/重启去重、Result 回读/脱敏、缺失证据拒绝、写失败阻断 HTTP、
SDK 吞异常、关闭失败、残留对象、日志观察不可用和报告防覆盖。

完整日志：`D:\research\aic\evidence\literature-audit-handoff-20261022.txt`。
基线对照：`D:\research\aic\evidence\literature-audit-baseline-regression-20261022.txt`。
已有失败：`test_research_pipeline.PipelineTests.test_unknown_constraint_not_discarded`，
预期 `UNSUPPORTED_CONSTRAINT`，实际 `PIPELINE_FAILED`。
同一运行器在未改动的 a619422 基线单独运行该模块，24 项中 23 通过、同一项失败。
该问题未混入本次 Group 审计改动；完整回归退出码仍为 1，不能宣称全绿。

## 人工启动命令（本次没有执行）

先人工检查 diff，安排正式 Partner 的切换窗口；不要在相同 AIC 的旧 Partner 仍运行时并行启动新实例。
下面命令读取原正式工作树的已有 `.env`，只向新进程注入，不复制、不输出凭据。
强制使用 Semantic Scholar、限制单请求，关闭 OpenAlex fallback 和附加资源查找，使单请求验收范围明确。

```powershell
Set-Location D:\research\aic\worktrees\literature-acceptance-audit
& D:\research\ResearchMesh\.venv\Scripts\python.exe -c 'from dotenv import load_dotenv; import os,runpy,sys; load_dotenv(r"D:\research\ResearchMesh-literature-live\.env", override=False); os.environ.update(RESEARCHMESH_S1_SINGLE_REQUEST="1", LITERATURE_SEARCH_PROVIDER="semantic-scholar", LITERATURE_OPENALEX_FALLBACK="0", LITERATURE_RESOLVE_RESOURCES="0"); sys.argv=["run_group_literature.py","--serve"]; runpy.run_path("run_group_literature.py",run_name="__main__")'
```

SQLite 沿用 `D:\research\aic\state\group_tasks.sqlite3`，不迁移、不删除历史行。
启动时记录 `AUDIT_JOURNAL` 输出的具体文件路径。只初始化审计，不会主动创建 Group 或发送 Task。

正式联调完成后，在另一个终端执行以下只读验收（占位符必须替换为本次新 ID）：

```powershell
Set-Location D:\research\aic\worktrees\literature-acceptance-audit
& D:\research\ResearchMesh\.venv\Scripts\python.exe verify_literature_acceptance.py `
  --journal 'D:\research\aic\evidence\literature-<本次run UUID>.jsonl' `
  --database 'D:\research\aic\state\group_tasks.sqlite3' `
  --group '<全新Group ID>' --session '<全新Session ID>' `
  --task '<全新Task ID>' --agent '<Literature Partner AIC>'
```

验收器默认独占保存 `D:\research\aic\evidence\acceptance-<UUID>.json`，输出 reportPath。
只有 formal 模式、非历史第三次 Task、五项全部 true，才返回 accepted=true 和退出码 0。
缺失/损坏证据或不满足规则返回退出码 1；不能拼接不同 run 文件凑证据。
可用 `--report` 指定新报告路径；已有报告会报 FileExistsError，绝不覆盖。

## 下一次正式单任务验收清单

- 人工确认新分支 diff、凭据环境和切换窗口；本次不停止旧正式 Partner。
- 确认全新 Group、Session、Task；不得重用第三次 Task。运行器能确认本次新 reservation 和单任务，历史未记入审计的 Group/Session 是否从未使用仍需 Leader 人工确认。
- 启动经审查的新 Partner，记下 AUDIT_JOURNAL 路径；确认 mode=formal 和 GROUP_JOINED。
- Leader 只发送一个七字段 Literature Task。不要为补证而重发业务执行；重复 Start 应仅被去重。
- 等待六字段 Result，Leader 按现有生命周期发送 Complete；确认 SQLite completed。
- Leader 发送 DISBAND；等待 GROUP_LEFT、CLEANUP_SUCCEEDED、LEAVE_FINISHED，检查无 CLEANUP_FAILED/LEAVE_FAILED。
- 保留应用进程；无需为了证明退群而强制关应用。需要恢复 Inbox 时另行人工安排。
- 运行验收器并保存其报告；五项均 true 且 accepted=true 才能通过。
- 保留原始 JSONL、报告及 Leader 本次独立证据；不修改原证据、不补造历史记录。

## 风险与边界

- SDK 的私有清理入口、资源字段与固定日志事件码存在版本耦合；升级 SDK 后必须重跑模拟生命周期测试再正式切换。
- 清理成功证明包含 SDK 操作返回、本地状态与原对象关闭检查；不是 broker 管理 API 的独立远端盘点。
- fsync 是有意的同步写入，增加少量磁盘延时；磁盘满、权限或写入失败将阻断业务/拒绝验收，不自动重试 Provider。
- JSONL 和摘要用于验收追溯，并非带签名的防篡改账本；读取端拒绝残缺/混合序号，证据目录权限与备份仍由本机运维管理。
- 持续服务每次启动新文件，不轮转/删除旧证据；需人工管理磁盘容量。
- 此次只有离线验证，尚无下一次正式 Group 的新 HTTP、清理或 GROUP_LEFT 实测结论。

正式切换仍停在人工检查：没有启动正式 Partner 或发送新 Task。
2026-10-23 用户已授权将完成的开发代码提交 GitHub；使用独立开发分支，不自动合并 main。
