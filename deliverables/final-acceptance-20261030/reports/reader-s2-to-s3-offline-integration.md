# ResearchMesh Reader S2 -> Repository S3 离线集成核验（2026-10-29）

## 结论

**NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。

真实 Reader `paper_notes.v1` 已通过 canonical hash、Schema、S2 依赖绑定、Repository 输入拆分及安全约束核验；相关自动化测试全部通过。使用该真实 Result 发起的本地隔离静态执行未调用模型、未执行目标代码，但 CodeWiki 在克隆目标仓库 `https://github.com/microsoft/LoRA` 时以 `REPOSITORY_CLONE_FAILED` 安全失败，因而尚未产生真实 `repository_record`。在定位并一次性验证克隆环境前，不应进入正式平台 S3。

## 1. 真实 Reader 输入

- Reader 运行目录：`<REDACTED_LOCAL_PATH>
- 实际保存文件：`reader_journal.json` 的 `outcome.result`
- Result schema：`paper_notes.v1`
- Canonical JSON SHA-256：`ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`
- 论文：`a8ca46b171467ceb2d7652fbfb67fe701ad86092`，*LoRA: Low-Rank Adaptation of Large Language Models*
- 分析层级：`fulltext`
- evidence：6 条；均来自本轮真实结果，未重建、改写或替换。
- 论文笔记对象 canonical SHA-256：`612685a7c0c787e4a5a30209942694f5e0c3097aac0b070e98e81bb3bd06a6d3`
- 映射出的目标仓库：`https://github.com/microsoft/LoRA`

上述 Result hash 与 Reader S2 跨端验收报告所记 Leader、Partner、journal 一致。

## 2. Repository 入口、版本与调用链

- 核验工作树：`<REDACTED_LOCAL_PATH>
- HEAD：`842b9a65615fa9db9a7b459d74bdecb58ce25cf2`
- 工作树仅有核验前已存在的两个未跟踪文件，未清理、覆盖或提交：
  - `config/amp/fluent-bit-heartbeat-reader-test.conf`
  - `docs/three_agent_deployment_freeze_20261026.md`
- 正式入口：`run_group_repository.py --serve`
- Partner 派生：`aip_repository_partner.py` -> `.venv-repository\Scripts\python.exe` -> `repository_aip_worker.py`
- 隔离执行器：`aip/repository_unified_executor.py::RepositoryStaticExecutor`
- Repository 运行环境探针：`<REDACTED_LOCAL_PATH> 3.12.10。
- CodeWiki：2.0.0，固定源码 commit `f0975ea19b17568ceb55cd5422206e1a1ae13c0b`。
- 当前常驻正式 Repository Partner 进程创建于 2026-10-27 13:04:57：外层 PID 13244（launcher `.venv`），子 PID 37668。它早于当前核验 HEAD；退出前 PID/内存模块版本没有保存为本轮证据，不能声称该常驻进程已加载当前 commit。
- 从历史 Repository 验收基线 `2564a137` 到当前 HEAD，Repository/S2->S3 源码无差异，仅两份 Repository 文档有变化；这降低了代码漂移风险，但不能替代正式前的运行时版本核验。

调用链：Planner S3 task -> `input_binding.bind_task_input()` -> `repository_paper_context.prepare_repository_task()` -> `repository_adapter` -> `RepositoryStaticExecutor` -> `.venv-repository` 隔离 worker -> CodeWiki 静态分析 -> native `repository_record` -> 标准 `code_records.v1` item kind `repository` -> `attach_comparison()` 添加可追溯论文对照。

## 3. Schema、字段映射与依赖

实测绑定结果：

- S3 `dependsOn=["S2"]`；只有已完成 S2 Result 才能绑定并派发。
- `paper_notes.v1.items[0].source` -> `input.papers[0]`。
- 完整笔记（`method/findings/limitations/analysisLevel/evidence`）-> `input.paperNotes[0]`。
- `source.repositoryUrls[0]` -> `input.repositoryUrls[0]`（无显式 URL 时）。
- S3 固定 `taskType=repository_analyze`、`requiredSkill=repository.analyze`、`outputSchema=code_records.v1`。
- 安全约束：`maxRepositories=1`、`executeCode=false`、`allowRepositorySearch=false`、`requirePaperNotes=true`。
- 派发给 Partner 的业务 payload 恰为 7 个协议字段；`dependsOn` 和 `requiredSkill` 不泄漏到 BusinessTask。
- `prepare_repository_task()` 先校验恰好一篇真实 fulltext 笔记、来源一致和非空证据，再将论文上下文与只接受 `repositoryUrls` 的原生静态请求分离；静态结果成功后才确定性附加 `paperComparison`。
- native wrapper 的类型为 `repository_record`；Leader 对外标准结果是 `code_records.v1`，item kind 为 `repository`。两者不可混称。

失败处理覆盖：缺笔记、多笔记、非 fulltext/mock/abstract、无 evidence、论文来源不一致、上下文超限、无效/非 HTTPS GitHub 仓库、仓库定位/克隆失败均 fail closed；失败不会生成虚假的业务完成或 `repository_record`。

## 4. 离线测试与实际静态执行

### 自动化测试

使用 `.venv-reader` 执行 Reader->Repository handoff、Repository static/live-chain/dual-mode、AIP v0.2、Group platform 和 release-readiness 测试：

- 首轮：85 个 assertion PASS；`tests.test_repository_static_executor` 因该测试文件使用顶层导入 `test_group_repository_platform`，包式调用发生 1 个 collection/import error。该错误已保留，不记作测试通过。
- 按项目测试布局改用 discovery：`python -m unittest discover -s tests -p test_repository_static_executor.py -v`，1/1 PASS。
- 合计实际测试断言：86 PASS，0 assertion failure；另有 1 次已解释并由正确入口复核通过的初始 collection error。

### 使用真实 Reader Result 的内存集成

PASS：

- canonical Result hash 精确匹配。
- 1 篇 fulltext / 6 evidence 被原样解析。
- S2->S3 依赖、字段映射、Partner payload Schema、论文笔记 digest 和仓库 URL 均通过。
- 原生静态输入只含 `repositoryUrls`；论文上下文不会被静态 backend 静默忽略。

### 使用真实 Reader Result 的隔离 Repository 静态执行

输出目录：`<REDACTED_LOCAL_PATH>

- `repository_input.json`：确认 S3、`repository_analyze`、`code_records.v1`、1 条真实 `paperNotes`、LoRA 仓库 URL 和全部安全约束。
- `backend_outcome.json`：`scope=ISOLATED_CODEWIKI_STATIC_NO_LLM`、`phase=failed`、`error.code=REPOSITORY_CLONE_FAILED`、`exceptionType=ValueError`。
- 诊断：`llmEnabled=false`、`modelCalls=0`、`targetCodeExecuted=false`、`targetDependenciesInstalled=false`、`pythonNetworkAttemptsBlocked=0`。
- `repository_worker.log`：`Repository worker: failed REPOSITORY_CLONE_FAILED`。
- 未产生 Result，未伪装成正式验收结果，未自动重试。

该项 **FAIL**：真实目标仓库未成功克隆，因此未完成代码静态分析，也未生成可验证的真实 `repository_record`。

## 5. 真实平台联调剩余前置条件

1. 只读定位 `REPOSITORY_CLONE_FAILED` 的底层 Git stderr/退出码；当前安全输出只保留了稳定错误码，信息不足以区分网络、代理、TLS、Git 或远端可达性问题。
2. 在不调用模型、不创建平台任务的隔离环境中，对同一精确 URL 完成一次受控静态执行，取得符合 Schema 的真实 `repository_record`；不得用 mock 替代。
3. 正式前按既有流程协调切换/重启 Repository Partner，并现场核验 Partner、派生 worker 的 PID 链、工作目录、源码路径、commit 及 `.venv-repository` 解释器。当前常驻进程的内存版本未验证。
4. 验证 Inbox/AMQPS/Heartbeat、无重复消费者和无旧任务残留后，方可创建新的正式 S3 Group/Session/Task。
5. 正式 S3 必须绑定本报告所列 Reader canonical hash，并维持 `executeCode=false`、`allowRepositorySearch=false`、无模型调用。

## 6. 验收矩阵

| 项目 | 结果 | 证据 |
|---|---|---|
| 真实 Reader Result 定位与 hash | PASS | Reader `reader_journal.json`; SHA-256 `ec6979...a1427` |
| `paper_notes.v1` / 1 fulltext / 6 evidence | PASS | 同上 |
| S2->S3 依赖和字段映射 | PASS | 实测绑定；`input_binding.py`; `repository_paper_context.py` |
| Repository payload/schema/safety constraints | PASS | 内存集成与 `repository_input.json` |
| 错误映射与 fail-closed | PASS | 自动化测试及 `backend_outcome.json` |
| 自动化回归 | PASS（含已复核的初始收集问题） | 86 assertions PASS |
| 真实目标仓库静态分析 | FAIL | `REPOSITORY_CLONE_FAILED` |
| 真实 `repository_record` 生成 | FAIL / 未生成 | backend phase failed |
| 当前正式 Partner 运行时版本 | 未验证 | 仅有确定性派生配置与现存 PID，不具退出前内存快照 |
| 正式平台 S3 | 未执行（符合边界） | 未建 Group/Session/Task，未发 Start |

最终状态：**NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。唯一直接业务阻塞是目标仓库克隆失败；此外，正式启动前仍需完成常驻 Repository Partner 的协调版本切换与运行时核验。

## 7. 2026-10-29 克隆排障与 Partner 运行状态补充

### 7.1 首次失败的确定根因

原失败目录 `repository-group-f64971b484754d5c9c8905d624f6ce85` 仅保存 `repository_input.json`、`backend_outcome.json` 和一行稳定错误日志。worker 将原生 warning 压缩为 `REPOSITORY_CLONE_FAILED`，该目录本身没有保留 Git stderr 或退出码。

后续只读诊断取得了可复现的底层证据：

- Git 2.56.0；用户级 `http.proxy` 和 `https.proxy` 均为 `<REDACTED_LOCAL_ADDRESS>`。
- `<REDACTED_LOCAL_ADDRESS>` 无监听，TCP 连接失败。
- DNS 将 `github.com` 解析为 `20.205.243.166`，直连 TCP 443 成功。
- 默认配置执行 `git ls-remote https://github.com/microsoft/LoRA HEAD`：退出码 128，stderr 为 `Failed to connect to github.com:443 over proxy <REDACTED_LOCAL_ADDRESS> ... Could not connect to server`。
- 仅对当前命令使用空代理覆盖：退出码 0，HEAD 为 `c4593f060e6a368d7bb5af5273b8e42810cdef90`。
- `<REDACTED_LOCAL_PATH> 下未找到可复用的 `LoRA` 工作树、同名克隆或残留 `gitprobe_*` 缓存。

因此原始失败的确定根因是 **Git 指向未运行的本机代理**，而不是 DNS、GitHub 443、Git 缺失或目标 URL 无效。未修改全局 Git、系统代理或项目配置。

### 7.2 最小临时修复及唯一一次受控执行

最小修复采用仅对子 worker 生效的 Git config 环境覆盖，清空 `http.proxy`/`https.proxy`；全局配置保持不变。使用原 `repository_input.json` 在全新目录执行一次隔离静态 worker：

`<REDACTED_LOCAL_PATH>

结果：

- 输出 scope：`ISOLATED_CODEWIKI_STATIC_NO_LLM`
- `phase=failed`
- `error.code=REPOSITORY_CLONE_FAILED`
- `modelCalls=0`
- `targetCodeExecuted=false`
- `targetDependenciesInstalled=false`
- `result=null`
- 没有执行第二次克隆。

该执行从 00:33:49 到 00:37:04。Repository fetcher 的 clone timeout 配置为 90 秒，但当前 worker 只保存稳定错误码，且 `RepositoryAgent.analyze()` 会把 fetch context 内的 clone、HEAD 解析和临时目录清理 `RepositoryCloneError` 都映射为同一 `REPOSITORY_CLONE_FAILED`。因此，仅凭总时长不能严谨断言第二次失败一定是网络 clone timeout；其底层 Git stderr/退出码仍 **未保存、未验证**。不得把“符合超时特征”写成已证明根因。

### 7.3 Reader 输入复核

重新从以下文件的 `outcome.result` 按项目 canonicalization（UTF-8、sort keys、无空白 separators、禁止 NaN）计算：

`<REDACTED_LOCAL_PATH>

结果仍为 `ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`；Schema 0.2、Result schema `paper_notes.v1`、1 个 item。受控执行的输入是原失败输入文件的逐文件复制，没有重建论文笔记。

### 7.4 当前 Repository Partner 只读运行证据

- 进程链：PID 13244 -> PID 37668，均创建于 2026-10-27 13:04:57。
- 命令行：`<REDACTED_LOCAL_PATH> -X utf8 run_group_repository.py --env-file <REDACTED_LOCAL_PATH> --serve`。
- Win32 显示外层 executable 为 launcher `.venv\Scripts\python.exe`，子进程实际 executable 为 `<REDACTED_LOCAL_PATH> Partner 本身不是 Repository Task worker。
- 工作目录/源码证据：Heartbeat 实际写入目标工作树 `<REDACTED_LOCAL_PATH> `run_group_repository.py`。工作树 HEAD 当前为 `842b9a65615fa9db9a7b459d74bdecb58ce25cf2`。进程启动后内存模块没有保存 hash，因此“启动时加载的每个模块等于当前 HEAD”未验证。
- 静态派生配置：env 指向 `<REDACTED_LOCAL_PATH> 和 `<REDACTED_LOCAL_PATH> Python 3.12.10、CodeWiki 2.0.0、CodeWiki commit `f0975ea19b17568ceb55cd5422206e1a1ae13c0b`。
- contextual static 路径：`run_group_repository.py` -> `serve_analysis()` -> `RepositoryGroupHandler` -> `RepositoryStaticExecutor` -> per-task `.venv-repository` `repository_aip_worker.py` -> `prepare_repository_task()` -> `RepositoryAgent(enrichment_enabled=False)` -> CodeWiki static -> `attach_comparison()`。
- 当前没有 `repository_aip_worker.py` Task worker 子进程；因此未来 Task worker 的 PID、内存模块和实际解释器尚未验证，只验证了确定性派生配置。
- AMQPS：PID 37668 到 `49.232.42.120:5671` 为 Established。
- Heartbeat：AIC `1.2.156.3088.1.BUPT.RECKYJ.13NQBJ.YM4WBJ.0E0X` 每约 15 秒持续写入，最新核验至 2026-10-29 00:39:18 +08:00。
- Inbox：代码路径在 AMQPS connect 后调用 `start_inbox_consuming()` 并打印 `INBOX_LISTENING`；但初始 stdout 未落盘，formal journal `literature-4bf78503d82342ac808e581f3089289a.jsonl` 只有 `AUDIT_STARTED`。故当前 Inbox 仅有进程、AMQPS、Heartbeat 的间接健康迹象，`INBOX_LISTENING` **缺少直接持久化证据，未验证**。
- 重复消费者：只发现这一条 Repository Partner 进程链，无第二个 Repository Partner/worker；PASS。
- SQLite：以 `mode=ro` 检查 `<REDACTED_LOCAL_PATH> Repository AIC 仅一条历史 task claim，状态 `completed`；无 working/reserved claim。未发现残留 Task reservation。
- 是否可安全接收邀请：**未证实**。没有重复消费者或活动 reservation，AMQPS/Heartbeat 正常，但 Inbox 直接监听证据缺失，且失效 Git 代理会让正式静态任务失败。

### 7.5 更新后的验收判定

仍为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。

阻塞：

1. 临时覆盖代理后的唯一一次受控执行仍未生成 `repository_record`，且第二次失败的底层 stderr/退出码未被当前审计结构保留。
2. 正式 Partner 会继承当前失效 Git 代理；未修改正在运行的 Partner，故即便 Inbox 健康也不能安全执行目标仓库分析。
3. `INBOX_LISTENING` 没有直接持久化证据；未来 Task worker 运行时版本也必须在实际派生后核验。

最少人工协调步骤：批准一个最小代码修复，使 fetcher/worker在不泄露 URL或凭据的前提下持久化 Git stage、return code、超时标记及脱敏 stderr，并明确正式静态 worker 的受控代理策略；离线测试后允许再做一次受控 LoRA 克隆/静态分析。只有生成且校验真实 `repository_record` 后，才协调重启 Partner、验证 Inbox 与派生 worker，再创建正式 S3。

## 8. 2026-10-29 最小错误审计与 Git 子进程直连修复

### 8.1 代码定位结论

- `agents/repository/researchmesh_repository/fetcher.py` 使用 `subprocess.run()` 执行两条 Git 命令：浅克隆，以及在克隆工作树内执行 `rev-parse HEAD`。
- clone 的 `RepositoryAgentConfig.clone_timeout_seconds=90.0` 确实直接传入 `subprocess.run(timeout=...)`；HEAD 解析 timeout 为 15 秒。
- 临时目录清理由 fetch contextmanager 的 `finally` 调用 `_remove_temp_root()`，最多尝试三次处理 Windows 只读文件。
- 修改前，clone stderr 被截短后放入 `RepositoryCloneError` 字符串；HEAD 和 cleanup 只产生泛化字符串。`RepositoryAgent` 再将其变成 native warning，`repository_aip_worker.py` 最终只提取稳定错误码，因此阶段、return code、timeout 类型和原始脱敏 stderr 没有进入 `backend_outcome.json`。
- Git 子进程原来继承完整 `os.environ`，且命令没有覆盖 `http.proxy`；因此用户级 Git proxy 与大小写 proxy 环境变量都会影响它。

现有证据仍不能证明代理覆盖后的第二次失败一定是 timeout；历史 worker没有保存新字段，本修复不会回填或伪造历史诊断。

### 8.2 实现

修改文件：

1. `agents/repository/researchmesh_repository/fetcher.py`
   - 为 clone、HEAD、cleanup 分别生成有界 `git_diagnostics`。
   - 每条记录包含 `stage`、`returncode`、`timedOut`、脱敏且最多 1000 字符的 `stderr`、`durationMs`、`repositoryUrl`、`resolvedCommit`。
   - clone/HEAD 子进程启动失败、真实 `TimeoutExpired`、非零退出码、HEAD 无结果及 cleanup 失败均可区分。
   - 对外继续抛出 `RepositoryCloneError`，不改变业务错误码。
   - Git 命令使用命令级 `-c http.proxy= -c https.proxy=`；只为该 child 环境移除六个大小写 proxy 变量，设置 `NO_PROXY/no_proxy=github.com`，保留全部无关环境变量。没有执行 `git config` 写操作。
2. `agents/repository/researchmesh_repository/agent.py`
   - 保留原 native warning/error 行为，并向 worker 暴露本次 `git_diagnostics`。
3. `repository_aip_worker.py`
   - 将内部诊断持久化为 `backend_outcome.json` 的 `diagnostics.gitStages`；失败时仍为原有 `REPOSITORY_CLONE_FAILED` 且 `result=null`。
4. `agents/repository/tests/test_repository_fetcher_audit.py`
   - 新增 clone 成功、非零 stderr、真实 timeout、HEAD 失败、cleanup 失败、脱敏和 child-only 直连测试。
5. `tests/test_repository_live_chain.py`
   - 新增 worker 持久化诊断且不伪造结果的测试。

该策略不会改变全局 Git 配置、系统代理或任何当前运行进程。当前正式 Partner仍加载旧内存代码，只有后续协调重启并从新 commit 派生 Task worker 才会生效。

### 8.3 离线回归

本阶段没有执行真实 clone、网络 Repository 分析、模型调用或平台任务。

- `.venv-repository`：新增 fetcher + Repository Agent/本地 CodeWiki fixture，27/27 PASS。
- `.venv-reader`：worker持久化测试及 Repository live chain，15/15 PASS。
- 完整 S2->S3 相关回归：86/86 PASS（设置 `PYTHONUTF8=1`）。
- Repository static executor 独立 discovery：1/1 PASS。
- `py_compile`：PASS。
- `git diff --check`：PASS。

保留的失败证据：首次运行 86 项时 84 PASS、2 ERROR；两项均为 `test_repository_dual_mode.py` 用未指定编码的 `Path.read_text()` 在当前 GBK 默认环境读取 worker 写出的 UTF-8 JSON，异常为 `UnicodeDecodeError`。在 `PYTHONUTF8=1` 下同一模块 25/25 PASS，随后完整 86/86 PASS；未修改业务代码或测试来掩盖该环境问题。

### 8.4 更新后的状态

本地修复 commit：`0ba6fb95e90f7563a3551bd49a34e9bb54152774`，仅包含上述 5 个文件；未 push、merge 或创建 PR。

代码和离线测试已达到“允许下一次受控静态执行”的条件，但尚未生成真实 `repository_record`，因此总体仍为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。

下一次最少步骤：

1. 使用包含本修复的新 commit，在不触碰正式 Partner的隔离进程中，原样复核 Reader canonical hash。
2. 仅执行一次 `https://github.com/microsoft/LoRA` 静态分析；不得访问其他仓库、执行代码、安装依赖或调用模型。
3. 无论成功或失败，保存新的 `backend_outcome.json`；失败时必须读取 `diagnostics.gitStages`，成功时核对 clone/head/cleanup、`resolvedCommit` 与 remote。
4. 只有生成并验证真实 `repository_record` 后，才协调正式 Partner版本切换、Inbox核验及正式 S3。

## 9. 2026-10-29 唯一一次 LoRA 受控静态执行

### 9.1 执行门禁

- 工作树 HEAD：`0ba6fb95e90f7563a3551bd49a34e9bb54152774`，精确匹配授权目标。
- 工作树仍仅有两个既有未跟踪文件；未覆盖或清理。
- Reader 原始路径：`<REDACTED_LOCAL_PATH> 的 `outcome.result`。
- 重新按 canonical JSON 规范计算 SHA-256：`ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`，精确匹配。
- 执行输入来自上一轮已验证的 `repository_input.json` 逐文件复制；没有重新阅读、生成或改写论文笔记。
- 输入：唯一仓库 `https://github.com/microsoft/LoRA`、1 篇 `paperNotes`、`requirePaperNotes=true`、`executeCode=false`、`allowRepositorySearch=false`、`outputSchema=code_records.v1`。
- 当前源码已加载命令级空 Git proxy 和仅子进程 proxy 环境隔离；没有修改全局 Git或系统代理。

### 9.2 唯一执行现场

新输出目录：

`<REDACTED_LOCAL_PATH>

仅启动一次 `repository_aip_worker.py --execute`。worker 已退出，未发现残留 worker；失败后没有第二次 clone、worker执行或平台任务。

`backend_outcome.json`：

- `scope=ISOLATED_CODEWIKI_STATIC_NO_LLM`
- `phase=failed`
- `error.code=REPOSITORY_CLONE_FAILED`
- `error.exceptionType=ValueError`
- `result=null`
- `modelCalls=0`
- `pythonNetworkAttemptsBlocked=0`
- `targetCodeExecuted=false`
- `targetDependenciesInstalled=false`

### 9.3 Git阶段诊断

`diagnostics.gitStages` 有且仅有两项：

1. clone
   - `returncode=null`：timeout异常路径没有取得进程退出码。
   - `timedOut=true`：由真实 `subprocess.TimeoutExpired` 分支记录，不是根据墙钟时间推断。
   - `durationMs=178023.081`。
   - 脱敏 stderr 显示 checkout 从 72%推进到 `100% (1189/1189), done.`，但进程没有在 timeout 路径内正常返回。
   - `resolvedCommit=null`。
2. cleanup
   - `returncode=null`：文件系统清理阶段没有 Git退出码。
   - `timedOut=false`。
   - `stderr=""`。
   - `durationMs=1794.118`，清理成功。
   - `resolvedCommit=null`。

没有 HEAD 阶段记录，因为 clone 子进程没有正常返回，代码未进入 `rev-parse HEAD`。配置的90秒 timeout确实传入 `subprocess.run()`；本次记录的178秒是该调用从启动到抛出并完成子进程/管道收尾的实际观测耗时，不能解释为 timeout未传递，也不能解释为 clone成功。

stderr虽然显示工作树文件更新到100%，但没有成功 return code、HEAD解析或保留下来的工作树，故 **工作树完整性、remote和目标 commit均未验证**。不得用进度行替代成功 clone或commit证据。

### 9.4 Schema与业务结果

- 输入 Schema和论文笔记绑定在执行前再次通过。
- clone失败发生在静态分析前，未运行 CodeWiki分析，未生成 native `repository_record`。
- 因 `result=null`，不存在可进行的 `code_records.v1` 输出 Schema校验。
- 错误映射符合预期：内部 clone timeout诊断持久化，对外保持 `REPOSITORY_CLONE_FAILED`，没有虚假业务完成或伪造结果。
- 模型调用、目标代码执行和目标依赖安装均为0/false。

### 9.5 最终结论

本次唯一受控执行：**FAIL（clone timeout）**。

总体状态仍为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**，且不得进入 Repository Partner版本切换阶段。直接阻塞是：LoRA clone checkout已报告100%，但 Git进程没有在当前90秒 timeout内正常返回，导致没有退出码、HEAD、resolved commit或可分析工作树。

后续若再次推进，必须另行授权新的受控执行；本阶段不再重试。下一项代码评审应聚焦为何 Windows Git在 checkout完成后未退出，以及是否需要在不降低安全边界的前提下调整 clone策略/timeout，同时继续保留单次执行和阶段审计。

## 10. 2026-10-29 timeout机制修复（无真实Git执行）

### 10.1 90秒与178秒的代码级解释

旧实现调用 Python 3.12 `subprocess.run(..., timeout=90, capture_output=True)`。本机标准库实现为：

1. `process.communicate(timeout=90)` 等待直接 Git进程并读取stdout/stderr；
2. 超时后 `process.kill()`，Windows实现只调用 `TerminateProcess` 终止直接 PID；
3. Windows分支随后执行 **无 timeout** 的第二次 `process.communicate()` 来收集输出；
4. 若后代进程仍持有继承的stdout/stderr管道，第二次 communicate可继续等待EOF。

因此90秒只约束第一次 communicate，并不约束超时后的输出回收。该确定代码路径能够解释审计的178秒。历史现场没有保存Git进程树，故“当时具体哪个Git后代持有管道、为何在checkout 100%后仍存活”仍为 **未验证**，不得进一步猜测。

旧 `returncode=null` / `timedOut=true` 逻辑准确表示进入真实 `TimeoutExpired` 分支、异常对象没有正常退出码；它不能表示进程树已完全终止。checkout 100%只是stderr进度，不能替代子进程成功退出。

### 10.2 最小实现

修改：

- `agents/repository/researchmesh_repository/config.py`
  - clone 90秒、checkout 60秒、Git验证15秒分别配置并校验为正数。
- `agents/repository/researchmesh_repository/fetcher.py`
  - clone改为 `git clone --no-checkout --depth 1 --no-tags`，把对象获取与工作树checkout拆开。
  - 独立执行 `checkout --detach HEAD`、`rev-parse HEAD` 和 `status --porcelain --untracked-files=no`。
  - 只有各阶段成功退出、HEAD为40位十六进制、工作树验证干净时，才设置 `resolvedCommit` 并yield给CodeWiki。
  - 每阶段使用显式 `Popen` 与独立timeout。
  - Windows为owned PID创建新进程组；超时后用精确PID的 `taskkill /PID <pid> /T /F` 终止整个owned树，并以5秒上限等待/回收输出。POSIX使用新session和 `killpg`。
  - 超时后的二次communicate也限制为5秒；无法取得退出码时仍记录null。
  - clone/checkout/HEAD/worktree任一失败即停止，不进入后续阶段或CodeWiki。
  - cleanup仍在finally运行；若已有主错误，cleanup失败只追加诊断，不覆盖主错误。
- `agents/repository/tests/test_repository_fetcher_audit.py`
  - 更新为完全Mock Popen，不执行真实Git或网络。

对外错误Schema、`RepositoryCloneError` 与 `REPOSITORY_CLONE_FAILED` 保持兼容。当前正式Partner没有重启，仍未加载本次代码。

### 10.3 测试

- 新增/Repository Agent：30/30 PASS。
- Repository Live Chain：15/15 PASS。
- S2->S3相关完整回归：86/86 PASS（`PYTHONUTF8=1`）。
- Repository static executor discovery：1/1 PASS。
- `py_compile`：PASS。
- `git diff --check`：PASS。

保留的测试失败：新增测试首轮有2个ERROR，原因是Mock `TimeoutExpired`错误使用 `stdout=` 参数；Python 3.12构造参数应为 `output=`。修正测试夹具后同组30/30通过，业务代码未因该夹具错误作规避修改。

测试覆盖：正常退出；checkout输出100%后挂起；Windows owned tree使用 `/T /F`；timeout后退出码可为null；clone失败与cleanup失败并存时主错误不被覆盖；耗时/timedOut来源；只有合法HEAD和干净工作树才产生resolvedCommit；worker失败保持 `result=null`。

### 10.4 状态与下一次停止条件

本地修复commit：`57d24e757a7b53d212b0225bcb03c4ced8dde794`，仅包含上述3个文件；未push、merge或创建PR。

状态保持 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**；本阶段没有执行clone/fetch/checkout/ls-remote、没有访问GitHub，也没有新建真实输出目录。

下一次受控执行必须另行授权，并且：

1. 先核对新commit、Reader canonical hash和唯一LoRA URL。
2. 新建唯一输出目录，仅启动一个隔离worker。
3. 预期阶段顺序为 `clone -> checkout -> head -> worktree -> cleanup`；任一阶段非零、timeout、HEAD非40位或工作树不干净，立即停止且不得重试。
4. 仅当HEAD/worktree均有同一 `resolvedCommit` 且worker生成真实、Schema有效的 `repository_record` 时，才可讨论Partner版本切换。

## 11. 2026-10-29 新分阶段实现的唯一一次授权执行

### 11.1 门禁

- 实际执行代码commit：`57d24e757a7b53d212b0225bcb03c4ced8dde794`，与授权目标精确一致。
- 工作区仅有两个既有未跟踪文件，执行前后均未改变。
- Reader输入：`<REDACTED_LOCAL_PATH> 的 `outcome.result`。
- 重新计算canonical SHA-256：`ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`，精确匹配。
- 输入仍为唯一 `https://github.com/microsoft/LoRA`、1篇真实paper note、`requirePaperNotes=true`、`executeCode=false`、`allowRepositorySearch=false`、`outputSchema=code_records.v1`。
- 执行入口已核对包含 `clone --no-checkout`、独立checkout/HEAD/worktree阶段、child-only直连及Windows owned PID tree `/T /F`终止逻辑。

### 11.2 唯一执行现场

输出目录：

`<REDACTED_LOCAL_PATH>

只启动一次隔离 `repository_aip_worker.py`。没有自动重试、第二次worker或平台任务。结果：

- `scope=ISOLATED_CODEWIKI_STATIC_NO_LLM`
- `phase=failed`
- `error.code=REPOSITORY_CLONE_FAILED`
- `result=null`
- `modelCalls=0`
- `pythonNetworkAttemptsBlocked=0`
- `targetCodeExecuted=false`
- `targetDependenciesInstalled=false`

### 11.3 Git阶段

`diagnostics.gitStages` 只有两项：

1. clone
   - `returncode=1`
   - `timedOut=true`
   - `durationMs=90392.524`
   - 脱敏stderr：`Cloning into '...researchmesh_repository_tvr6me2g\\repository'...`
   - `resolvedCommit=null`
2. cleanup
   - `returncode=null`
   - `timedOut=false`
   - `durationMs=45.48`
   - stderr为空
   - `resolvedCommit=null`

clone在90秒上限触发真实timeout；新版有界回收后总阶段耗时约90.4秒，不再出现旧实现约178秒的无界二次communicate。`returncode=1` 是owned Git进程树被终止和回收后取得的实际直接进程退出码。执行结束后的只读进程快照未发现 `git*` 或本轮 `repository_aip_worker.py` 残留；这与 `/PID <owned-pid> /T /F` 路径一致。没有单独持久化taskkill返回码字段，因此不能把“taskkill自身returncode”作为已有审计字段声称。

由于clone未成功，严格按设计没有进入checkout、HEAD、worktree验证或CodeWiki。cleanup成功，没有覆盖原始clone timeout。

### 11.4 结果验收

- Git正常完成：FAIL。
- checkout/HEAD/worktree：未执行（正确fail closed）。
- `resolvedCommit`：无。
- 真实 `repository_record`：未生成。
- `code_records.v1`输出Schema：无结果可校验；输入Schema和Reader绑定已通过。
- 模型调用：0。
- 目标代码执行：false。
- 目标依赖安装：false。
- 错误映射：PASS，保持 `REPOSITORY_CLONE_FAILED` 且没有虚假结果。
- 输出/诊断保存：PASS，`repository_input.json`与`backend_outcome.json`保存在新目录。

### 11.5 最终结论

本次授权执行最终为 **FAIL — clone timeout**。总体继续为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。

本次授权已经消耗，失败后已停止。不得自行再次clone、切换Partner或进入正式S3。与上一轮不同，本次证明确认：拆分checkout后，网络/对象获取所在的 `clone --no-checkout` 本身在90秒内仍未完成；问题不是checkout阶段，因为checkout从未启动。

## 12. 2026-10-29 partial clone离线策略（无真实Git操作）

### 12.1 审查结论

当前对象获取阶段在本节修改前为：

`git -c http.proxy= -c https.proxy= ... clone --depth 1 --single-branch --no-tags --no-checkout <LoRA> <temp>`

它已限制为默认分支的一层历史并跳过checkout，但仍会下载该浅层快照的全部blob。stdout/stderr由`Popen`管道捕获；未显式`--progress`时，非TTY环境可能缺少传输进度。clone timeout仍为90秒，超时使用owned进程树终止和有界输出回收。

CodeWiki静态入口 `handle_analyze_repo()` 构造依赖图时遍历当前工作树并读取源文件/项目元数据；Git用途为HEAD记录、ignore规则和可选增量baseline。首次分析没有历史baseline，不要求完整Git历史。Repository证据收集同样读取当前工作树。因此一层历史足够，不能省略目标commit的完整工作树文件，但可用partial clone延迟blob传输到checkout阶段。

### 12.2 最小策略

修改 `agents/repository/researchmesh_repository/fetcher.py`：

- clone增加 `-c protocol.version=2`、`--filter=blob:none`、`--single-branch`、`--progress`，保留`--depth 1 --no-tags --no-checkout`。
- checkout保持独立阶段并增加`--progress`。
- checkout显式使用禁用hooks目录，避免目标仓库checkout hook执行。
- 仅Git子进程环境增加`GIT_LFS_SKIP_SMUDGE=1`，避免LFS smudge拉取静态分析不需要的大对象；不改变用户或系统环境。
- 仍按clone 90秒、checkout 60秒、HEAD/worktree各15秒执行；不重试。
- checkout后仍必须取得合法40位HEAD并通过干净工作树检查，才允许CodeWiki。

预期作用：clone阶段只取浅层commit/tree，blob按工作树checkout需要延迟获取；将网络对象问题明确分配到clone或checkout。由于CodeWiki最终需要工作树源文件，partial clone主要改善阶段化和初始对象量，不能保证总传输量显著下降。

服务器能力限制：Mock只能证明本地请求了protocol v2和`blob:none`，不能证明GitHub本次连接实际支持。若服务器不支持filter，Git可输出`filtering not recognized ... ignoring`并退化；该warning会保存在clone阶段stderr。未访问GitHub验证能力。

### 12.3 离线测试

更新 `agents/repository/tests/test_repository_fetcher_audit.py`，全部Popen均为Mock：

- 验证clone参数包含`--depth 1 --single-branch --filter=blob:none --no-checkout --progress`和protocol v2。
- 验证checkout独立、带`--progress`并禁用hooks。
- 验证Git child环境设置`GIT_LFS_SKIP_SMUDGE=1`且不改变全局环境。
- 验证服务器filter warning被保留，但不会被测试解释为服务器能力证明。
- 保留阶段顺序、timeout进程树处理、失败映射、HEAD/worktree和resolvedCommit条件测试。

结果：

- Fetcher + Repository Agent：31/31 PASS。
- S2->S3相关完整回归：86/86 PASS（`PYTHONUTF8=1`）。
- Repository static executor：1/1 PASS。
- `py_compile`：PASS。
- `git diff --check`：PASS。

本阶段没有执行真实clone、fetch、checkout、ls-remote或任何Git子进程，没有访问GitHub，没有创建真实执行目录。

### 12.4 下一次受控执行前置条件

本地策略commit：`039ebf1483f27e9f0c4ea0fcb7f05ea3e5ec6650`，仅包含fetcher及其Mock测试；未push、merge或创建PR。

状态继续为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。

下一次执行必须获得新的明确授权，并满足：

1. 核对包含本策略的新commit、Reader canonical hash和唯一LoRA URL。
2. 唯一worker、唯一新目录、无自动重试。
3. clone阶段检查filter capability warning、对象传输进度、timeout和returncode；失败立即停止。
4. clone成功后checkout阶段可能进行lazy blob获取，必须独立检查进度、timeout和returncode。
5. 只有HEAD为40位commit、工作树干净且二者`resolvedCommit`一致才运行CodeWiki。
6. 任一阶段timeout、filter退化后仍超时、HEAD/工作树不通过或cleanup异常，立即停止，不进入Partner切换。

## 13. 2026-10-29 partial clone唯一真实验证

独立报告：`<REDACTED_LOCAL_PATH>

- ResearchMesh commit：`039ebf1483f27e9f0c4ea0fcb7f05ea3e5ec6650`
- 现场：`outputs\repository-group-a1c7b5ddf6ee437c8347819dd06ca618`
- clone：exit 0、未超时、4194.576 ms；182个对象、48.82 KiB；未观察到filter warning，但不能据此单独证明远端filter能力。
- checkout：exit 1、`timedOut=true`、60435.971 ms、stderr为空。
- HEAD/worktree/CodeWiki：未执行。
- cleanup：成功，58.531 ms。
- `resolvedCommit=null`、`result=null`，无`repository_record`或`code_records.v1`可校验。
- `modelCalls=0`、`targetCodeExecuted=false`、`targetDependenciesInstalled=false`。
- 执行后无Git或本轮worker残留；没有重试。

最终：**FAIL — checkout timeout**；继续 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。本次证明对象获取阶段已显著缩短并成功，当前阻塞已移动到独立checkout阶段。不得进入Partner切换或正式S3。

## 14. 2026-10-29 checkout诊断固定与克隆策略复核（无真实Git操作）

### 14.1 诊断修复commit

本地commit：`faee5bfbc5721193b3b361bf9bba50c56d6b0038`（`fix(repository): preserve git timeout diagnostics`）。仅包含：

- `agents/repository/researchmesh_repository/fetcher.py`
- `agents/repository/tests/test_repository_fetcher_audit.py`

两个既有未跟踪文件未暂存、未提交；没有push、merge或PR。原始失败现场 `repository-group-a1c7b5ddf6ee437c8347819dd06ca618` 未修改或覆盖。

诊断现在独立保存并脱敏/限长stdout与stderr，分别记录截断状态；记录timeout触发瞬间returncode、最终returncode及其来源、终止尝试/方式/命令返回码、fallback kill。`taskkill /T`成功和父进程回收不被当作独立的后代清理证明，`treeCleanupVerified`保持null。Checkout前快照只记录本地`.git/HEAD`等事实，并明确`worktreeCheckoutVerified=false`。cleanup失败仍追加诊断且不覆盖原始clone/checkout错误；对外错误码和失败即停止语义不变。

### 14.2 克隆量与业务需求复核

CodeWiki入口 `CodeWikiProvider.analyze()` 将完整工作树路径交给 `handle_analyze_repo(use_gitignore=true)`，随后读取组件索引和依赖图。Repository证据收集器读取README、manifest、依赖配置、工作流及其他静态源码证据。当前实现不调用`git log`、`blame`或历史diff；首次静态分析也没有历史baseline。因此：

- `--depth 1 --single-branch --no-tags`足以提供当前目标分析所需的commit/tree；完整Git历史不是业务前置。
- 未指定LoRA目标commit/ref时，应解析远端默认分支的一层HEAD；不能在本地预设commit。若未来要求固定commit，必须由任务输入明确给出并重新设计ref获取与校验，不能隐式猜测。
- `--filter=blob:none --no-checkout`适用于阶段化对象获取，但checkout生成完整工作树时会按需取得缺失blob。它能显著减少clone初始对象量，却不能保证降低最终总下载量。
- 不能采用只取README/manifest的sparse checkout作为当前最小方案：CodeWiki需要遍历和读取实际源码及目录结构，这会造成不完整或误导性的`repository_record`。
- 保持 `clone -> checkout -> head -> worktree -> CodeWiki -> cleanup`。clone 90秒、checkout 60秒、HEAD/worktree各15秒预算本节未修改；任一阶段失败立即停止。

最小建议是保留当前参数和阶段边界，不增加新的获取命令或重试。下一次真实执行重点验证checkout期间的lazy blob获取及60秒预算是否可完成，而不是把Mock结果解释为远端能力证明。

### 14.3 离线验证

全部测试使用Mock subprocess或本地fixture，没有执行clone/fetch/checkout/ls-remote，也没有访问GitHub：

- Repository Fetcher + Agent：36/36 PASS。
- Repository Live Chain、Reader→Repository handoff、AIP Repository：43/43 PASS。
- 合计：79/79 PASS。
- `git diff --check`：PASS。

覆盖候选参数、`clone -> checkout -> head -> worktree -> cleanup`顺序、正常/timeout分支、timeout自然退出竞态、独立输出流、部分输出、终止失败、退出码缺失、cleanup双重失败、合法HEAD与干净工作树的`resolvedCommit`门禁，以及失败不进入CodeWiki/不生成虚假结果。

### 14.4 未验证假设与下一次条件

Mock仅证明本地命令请求了protocol v2、浅克隆和`blob:none`，不能证明下一次GitHub连接接受filter、lazy blob实际传输模式或60秒checkout足够。没有filter warning也不能单独证明filter生效。checkout阻塞位置（网络、工作树写入、filter driver或其他Git内部等待）仍需下一次获授权的真实执行由新诊断区分。

下一次受控执行前必须：核对commit `faee5bfbc5721193b3b361bf9bba50c56d6b0038`、真实Reader hash与唯一LoRA URL；使用唯一新目录和唯一worker；禁止自动重试；逐阶段保存新诊断。任何timeout、非零退出、HEAD非40位、工作树不干净或cleanup异常均立即停止。当前总体状态仍为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。

## 15. 2026-10-29 诊断增强后的唯一真实LoRA静态执行

### 15.1 门禁与现场

- ResearchMesh实际HEAD：`faee5bfbc5721193b3b361bf9bba50c56d6b0038`。
- 祖先链包含partial clone策略 `039ebf1`、分阶段timeout `57d24e7`、基础Git审计 `0ba6fb9`；本轮诊断commit在其上。
- 工作区执行前后仅保留两个既有未跟踪文件。
- Reader来源：`outputs\reader-group-6e387fef93c648c399588c64721481fc\reader_journal.json` 的 `outcome.result`。
- canonical规则：UTF-8、sort keys、紧凑JSON；SHA-256为 `ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`，匹配授权值。
- Repository输入与Reader `items[0].data` 完全相等；`requirePaperNotes=true`、`outputSchema=code_records.v1`、唯一URL为 `https://github.com/microsoft/LoRA`。
- 唯一执行ID：`529a42b7fc8c45eaacc68f0c3eacc9fc`。
- 现场：`<REDACTED_LOCAL_PATH>
- `repository_input.json` SHA-256：`640F4F7BECBD35DE2DDF34D99CCECBAC2AFD4F0C0DBFB96EBC0A0646B8EB7404`。
- `backend_outcome.json` SHA-256：`D2A72E573815D7DC69A201F67EB6318327A2BAFE90AC7B8865F3C86A17EB976D`。

只启动一个 `.venv-repository` 静态Worker；没有重试、第二次Git尝试、平台任务或Partner操作。

### 15.2 Git阶段证据

1. clone：PASS
   - `returncode=0`、`returncodeSource=natural-exit`、`timedOut=false`、`durationMs=3321.176`。
   - stdout为空且未截断；stderr保存接收进度尾部并标记 `stderrTruncated=true`。
   - 182/182对象，48.82 KiB，约205 KiB/s。
   - 未发生终止；`resolvedCommit=null`（clone阶段预期不解析HEAD）。
2. checkout：FAIL
   - `timedOut=true`、`timeoutReturncode=null`、最终`returncode=1`、`returncodeSource=post-termination`、`durationMs=60620.048`。
   - stdout/stderr均为空且未截断；因此不能确认timeout时处于网络传输、文件写入、filter执行或其他等待。
   - timeout后尝试 `taskkill-tree`，终止命令返回0，未使用fallback kill；`treeCleanupVerified=null`，没有把taskkill成功声明为独立的完整进程树证明。
   - checkout前本地快照：repository与`.git`目录存在，HEAD为`ref: refs/heads/main`，但`worktreeCheckoutVerified=false`。
3. HEAD/worktree/CodeWiki：未执行，符合失败即停止门禁。
4. cleanup：PASS
   - `timedOut=false`、`durationMs=60.377`、stdout/stderr为空。
   - 执行后按本轮ID和临时目录筛查未发现Git或Worker残留进程。

### 15.3 业务结果

- `phase=failed`、`error.code=REPOSITORY_CLONE_FAILED`、`result=null`。
- `resolvedCommit=null`；没有真实`repository_record`，因此没有`code_records.v1`结果可做Schema成功校验。
- `modelCalls=0`、`pythonNetworkAttemptsBlocked=0`。
- `targetCodeExecuted=false`、`targetDependenciesInstalled=false`。
- 未伪造成功，也未用历史或Mock结果代替。

### 15.4 结论

本轮最终为 **FAIL — checkout timeout**。clone正常完成，但checkout在60秒预算触发真实timeout；增强诊断证明最终退出码1来自timeout后的终止路径，不能解释为Git自然业务退出码。由于两条捕获流均无输出，具体内部等待位置仍未确认。

本次唯一真实执行授权已消耗，已停止且没有第二次尝试。总体继续为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**；不得自行切换Partner或进入正式S3。

## 16. 2026-10-29 checkout timeout策略修复（仅离线）

### 16.1 已确认事实与未知项

最近现场 `repository-group-529a42b7fc8c45eaacc68f0c3eacc9fc` 证明：partial clone在3.32秒完成182个对象，checkout在60.62秒触发timeout；timeout瞬间Git尚未自然退出，最终exit 1来自终止后。checkout两条捕获流均为空，所以不能确认当时正在传输blob、写文件、执行filter driver或等待其他Git内部状态。

代码事实是：`--filter=blob:none`建立promisor partial clone，缺失blob可在读取/checkout时触发按需获取，因此checkout具备lazy network fetch路径，但这不是本次timeout根因的直接证明。checkout同样由`_git_command()`和`_run_git_stage()`启动，命令级`http.proxy=`/`https.proxy=`与去除大小写proxy环境变量会应用到checkout主进程，并由其后续Git子进程继承；没有修改全局配置。

### 16.2 方案比较与选择

方案A：保留`--depth 1 --single-branch --no-tags --no-checkout`，移除`--filter=blob:none`。浅层HEAD所需blob在clone阶段取得；若clone成功，checkout不依赖partial-clone lazy fetch。实现只改一个参数，现有clone 90秒、checkout 60秒、HEAD/worktree 15秒及所有诊断保持不变。风险是此前无filter对象获取曾在90秒超时，因此不能保证下一次成功。

方案B：保留partial clone并在checkout前预取全部所需blob。可靠实现需要新增对象枚举与fetch阶段，并正确覆盖promisor remote保存的filter；这会引入额外命令、timeout、ref/完整性验证和失败映射。单靠Mock不能证明远端返回了完整blob集合，也不能证明checkout不会再次lazy fetch。

选择 **方案A**。原因是它是语义明确且最小的修复，能够把HEAD快照所需对象获取限定到已有clone阶段，并避免新增不易证明完整性的预取协议。选择不代表根因已确认或真实执行必然通过。

### 16.3 修改与离线验证

未提交修改：

- `agents/repository/researchmesh_repository/fetcher.py`：仅移除`--filter=blob:none`并解释边界；其他参数、直连策略、timeout和终止逻辑不变。
- `agents/repository/tests/test_repository_fetcher_audit.py`：验证clone无任何`--filter=`、仍为浅克隆/no-checkout；checkout没有fetch子命令；clone与checkout均使用命令级直连和去proxy child环境。

Mock和本地fixture结果：

- Fetcher + Repository Agent：36/36 PASS。
- Repository Live Chain、Reader→Repository handoff、AIP Repository：43/43 PASS。
- 合计：79/79 PASS。
- `git diff --check`：PASS。

既有timeout竞态、终止结果、cleanup主错误优先、checkout失败后不进入HEAD/worktree/CodeWiki、只有合法HEAD和干净工作树才产生`resolvedCommit`、失败不产生虚假结果的测试继续通过。本节没有执行任何Git子进程、网络探测、Partner或平台操作。

### 16.4 下一次真实执行条件

当前仍为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。下一次必须另获一次明确授权，并先固定本节未提交diff。受控执行应使用唯一新目录和单一worker，无重试；预期clone传输量比partial clone显著增加，必须在90秒内自然exit 0，否则立即停止。只有随后checkout在60秒内自然exit 0、HEAD为40位SHA、工作树干净，才运行CodeWiki。任一timeout/非零退出/验证失败均保存诊断、执行cleanup并停止，不得切换Partner或进入正式S3。

## 17. 2026-10-29 方案A唯一真实LoRA静态执行

### 17.1 门禁与唯一现场

- 实际ResearchMesh commit：`618489f0fc1ac00c8273d916096d970d92b04dbd`。
- 工作区执行前后仅有两个既有未跟踪文件。
- Reader Result路径：`outputs\reader-group-6e387fef93c648c399588c64721481fc\reader_journal.json` 的 `outcome.result`。
- Reader canonical SHA-256（UTF-8、sort keys、紧凑JSON）：`ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`。
- Repository输入中的单篇paper note与Reader `items[0].data`完全相等；`requirePaperNotes=true`、`outputSchema=code_records.v1`。
- 唯一执行ID：`c7ce9bb9e71f45d2b19ba92bd1e8581a`。
- 输出目录：`<REDACTED_LOCAL_PATH>
- `repository_input.json` SHA-256：`640F4F7BECBD35DE2DDF34D99CCECBAC2AFD4F0C0DBFB96EBC0A0646B8EB7404`。
- `backend_outcome.json` SHA-256：`7BE4923232B7DB62C004CD9DD103BECEB1C3583B664CF429ADDEDEF9DFF08840`。
- Result canonical SHA-256：`bf8287e5670a3e5037ad68b30d539112d78a74cedd6e2e1fbec6eeabb389c86b`。

只启动一个 `.venv-repository` 静态Worker；没有第二次Git、自动重试、平台任务、Partner操作或模型调用。

### 17.2 Git阶段

1. clone：PASS
   - `returncode=0`、`returncodeSource=natural-exit`、`timedOut=false`、`durationMs=54540.576`。
   - stdout为空、未截断；stderr为脱敏进度尾部，`stderrTruncated=true`。
   - 未发生终止；`treeCleanupVerified=null`，没有过度声明。
2. checkout：PASS
   - `returncode=0`、`returncodeSource=natural-exit`、`timedOut=false`、`durationMs=3082.015`。
   - 完成1189/1189文件并自然返回；HEAD提示短SHA `c4593f0`。
   - 未发生终止。checkout前快照仍正确标记`worktreeCheckoutVerified=false`，不以预检HEAD冒充检出成功。
3. HEAD：PASS
   - `returncode=0`、`timedOut=false`、`durationMs=137.335`。
   - `resolvedCommit=c4593f060e6a368d7bb5af5273b8e42810cdef90`。
4. worktree：PASS
   - `returncode=0`、`timedOut=false`、`durationMs=1426.474`、stdout/stderr为空。
   - `resolvedCommit`与HEAD一致，工作树验证干净。
5. cleanup：PASS
   - `timedOut=false`、`durationMs=1388.023`、stdout/stderr为空。
   - 本轮ID/临时目录关联的Git或Worker进程只读筛查无残留。

证据限制：当前`gitStages` schema持久化阶段耗时和结果，但没有每阶段的绝对开始/结束wall-clock字段。输出文件时间给出Worker执行边界（约09:01:15至09:02:17，Asia/Shanghai），不能替代或伪造成逐阶段绝对时间。本报告仅记录实际保存的duration。

### 17.3 Repository结果与契约

- Worker：`phase=completed`、`error=null`、`scope=ISOLATED_CODEWIKI_STATIC_NO_LLM`。
- 真实`repository_record`已生成，`isMock=false`，URL为唯一授权LoRA仓库，commit为上述40位SHA。
- 项目现有`validate_result()`对保存结果与原Task再次校验：PASS；`kind=repository`，对应`code_records.v1`任务契约。
- 结构统计：33个component、24条dependency edge、1个artifact、35条repository evidence。
- Reader关联：`paperContextCompared=true`、1篇fulltext note；重新计算的notes digest与保存的`inputNotesSha256`均为 `fcd8ad30a25c2614ec86b762a02833b7fb25576d895082526fbb88ee10c36976`；`comparison_valid=true`。
- 对照严格标记为deterministic lexical、非semantic reproduction verification；没有把词面匹配冒充复现实验结论。
- `modelCalls=0`、`llmEnabled=false`、`pythonNetworkAttemptsBlocked=0`。
- `targetCodeExecuted=false`、`targetDependenciesInstalled=false`。

### 17.4 最终结论

本次受控本机静态分析为 **PASS**。方案A在90秒clone预算内取得浅层HEAD所需对象，checkout在60秒预算内完成，随后HEAD、干净工作树、真实CodeWiki结果和Reader上下文绑定均通过。

这证明本机隔离S2→S3静态执行成功，不等同于正式平台S3验收通过。唯一真实执行授权已消耗；已停止，不自行切换Partner、创建正式任务或进入正式S3。

## 18. 2026-10-29 Repository Partner受控版本切换

独立切换记录：`<REDACTED_LOCAL_PATH>

- 旧专属树10892 → 13244 → 37668及conhost 27788已按PID定向退出；旧5671连接消失。
- 预检确认Repository AIC仅有历史`completed`，无活动reservation/Task、无Task Worker、无重复消费者或Group审计事件。
- 首次启动因PowerShell包装的`$env:`提前展开而fail-closed，没有形成Partner；日志已保存。随后按回滚/服务恢复方案只启动一次修正后的入口，没有循环重启。
- 新树：launcher 2444 → venv shim 50732 → 实际Partner 67324；工作目录与Wrapper root为目标worktree，HEAD为`618489f0fc1ac00c8273d916096d970d92b04dbd`。
- Task Worker确定性配置为`<REDACTED_LOCAL_PATH> 3.12.10）和目标worktree Wrapper root。禁止测试Task，因此未来Worker实际PID/内存模块版本仍待正式Task派生后核验。
- 新stdout直接记录`AMQPS_CONNECTED`与`INBOX_LISTENING`；PID 67324有5671 Established连接；heartbeat uptime 15.111 → 30.115 → 45.129 → 60.148秒单调递增。
- 新audit仅`AUDIT_STARTED`；无邀请、Start、Complete、Group或Task事件；无模型调用。

最终：**READY_FOR_NEW_INVITATION**。切换成功后已停止操作，等待用户协调赵发送新邀请。
