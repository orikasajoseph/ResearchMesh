# ResearchMesh Reader 正式 S2 启动阶段核验（2026-10-28）

## 结论

**Reader READY（仅限等待 Leader 邀请阶段）。** Reader Partner 已连接正式 AMQPS、持续监听 Inbox，并连续写入 Heartbeat。没有创建或接受邀请，没有 Group/Session/Task，没有 Start，没有业务 Worker，也没有真实模型调用。

## Git 与现场保护

- 工作区：`<REDACTED_LOCAL_PATH>
- 启动时 HEAD：`0c0f8a5e27d325709d4a91f81ce5b8d8bd22048a`
- 目标 commit：`0c0f8a5e27d325709d4a91f81ce5b8d8bd22048a`
- HEAD 与目标一致；目标对象类型为 commit。
- 工作树只存在两个启动前已有的未跟踪文件：
  - `config/amp/fluent-bit-heartbeat-reader-test.conf`
  - `docs/three_agent_deployment_freeze_20261026.md`
- 两文件未覆盖、未清理、未暂存。未 push、merge 或创建 PR。

## 启动前快照

- 未发现命令行为 `run_group_reader.py`、`reader_aip_worker.py` 或 `aip_reader_partner` 的既有 Python 进程。
- 未发现 `.venv-reader` Python 进程或 Reader 所属 5671 连接。
- SQLite 使用 URI `mode=ro` 只读查询；没有 `reserved` 或运行中记录。Reader AIC 的最新记录仍为旧失败 Task 的 `uncertain`，更新时间 `2026-10-28 13:37:49 UTC`，未修改。
- 启动前 Reader Heartbeat 最后样本属于旧实例；本次样本以 uptime 重新从 0 开始，可明确区分。

## 启动方案与进程快照

正式入口从目标工作树以 `run_group_reader.py --serve` 启动。启动时明确设置：

- 外层 launcher：`<REDACTED_LOCAL_PATH>
- Task Worker Python 配置：`<REDACTED_LOCAL_PATH>
- Reader wrapper root：`<REDACTED_LOCAL_PATH>
- runtime home：`<REDACTED_LOCAL_PATH>
- 未设置或修改全局 cost-map、代理、MQ、证书、AIC、平台注册信息或系统配置。

启动时间：`2026-10-28 23:21:29 CST`。

脱敏进程树：

- PID `38972`：launcher venv shim；PPID `50856`；解释器路径为 launcher `.venv\Scripts\python.exe`。
- PID `35424`：PID 38972 的子进程；实际 CPython 为 `<REDACTED_LOCAL_PATH> `run_group_reader.py --serve`。
- 两个进程的命令行均指向既有 env 文件，未在报告中记录任何凭据值。
- 启动工作目录由 `Start-Process -WorkingDirectory` 固定为目标工作树；入口使用相对路径 `run_group_reader.py`，因此实际入口为 `<REDACTED_LOCAL_PATH>
- 当前没有 `reader_aip_worker.py` 进程。业务 Worker 只会在新 Task 后派生；本阶段禁止邀请和 Start，因此无法也不应生成 Worker PID。外层 Partner 不使用 launcher 环境执行模型业务。

## 版本和源码路径核验

- 启动工作树 commit：`0c0f8a5e27d325709d4a91f81ce5b8d8bd22048a`。
- `.venv-reader` 独立加载探针报告的 `sys.executable`：`<REDACTED_LOCAL_PATH>
- 该解释器解析的实际源码：
  - `<REDACTED_LOCAL_PATH>
  - `<REDACTED_LOCAL_PATH>
  - `<REDACTED_LOCAL_PATH>
- SHA-256：
  - `run_group_reader.py`: `500DBBF742172BB5468423A42B101A2BA0C3512559509CAB9B9908F70D0D83FB`
  - `reader_aip_worker.py`: `5ADD9348DACF92F794D50EBF2F9F0965950640523E2849DEB78F998A6DE5A11C`
  - `reader_adapter.py`: `02911070D06D2292CA2C63E3F65C6DFB683F16725E691A0E23A36F668883A36D`
  - provider: `9AF8644E0456E5738EAB17FE2CE5A5998F0EA38FAC7525DB9093B5F39D6C31D6`

注意：以上证明 Partner 的启动工作树及其为未来 Worker 配置/验证的解释器和源码路径。只有收到授权后的新 Task 才会产生实际 Worker 进程；届时仍必须对该 Worker PID、父子链、解释器和加载路径再次做运行态核验，不能用本探针代替。

## AMQPS、Inbox 与 Heartbeat

启动 stdout 依次出现：

1. `AUDIT_JOURNAL: <REDACTED_LOCAL_PATH>
2. `AMQPS_CONNECTED`
3. `INBOX_LISTENING`

stderr 为空。PID 35424 拥有到正式 AMQPS 端点 5671 的 Established 连接；健康判断不只依赖 TCP，还同时要求上述应用层状态和连续 Heartbeat。

本次启动后的 Heartbeat UTC 时间与 uptime：

- `15:21:38.791669Z` — `0.023s`
- `15:21:53.796022Z` — `15.028s`
- `15:22:08.808879Z` — `30.041s`
- `15:22:23.811959Z` — `45.044s`

时间间隔约 15 秒且 uptime 单调递增，证明 Heartbeat 周期任务持续运行。

## 邀请、任务与初始化状态

- 新 journal 当前只有 `AUDIT_STARTED`，runId 为 `c12cf528db9a4ac8bfe5976af9dd7c8d`。
- 没有 `GROUP_JOINED`、邀请、reservation、backend started、Task、Result 或 Complete 事件。
- stdout 没有重复 `AMQPS_CONNECTED` 或重复 `INBOX_LISTENING`；系统进程表只有一个 Reader Partner 进程链，未发现重复消费者。
- Task Worker 尚未启动，因此不存在本轮 `runtime_initialization.jsonl`；不存在“未完成初始化阶段”。旧输出和旧初始化日志均未修改。
- Reader 正在不修改 SQLite 的情况下等待新邀请。

## 当前阶段边界

Reader 已满足进入 Leader 邀请阶段的启动条件，但尚未获准接受邀请。下一步必须等待用户确认；确认前不得创建 Group/Session/Task、发送或接受邀请、发送 Start 或产生模型费用。

## 单请求版本切换（2026-10-28 23:47 CST）

### 阶段一：旧 Group 退出与清理证据

Reader formal journal `literature-c12cf528db9a4ac8bfe5976af9dd7c8d.jsonl` 对旧 Group
`rm-reader-cf05282740494d44b6fbc53cb1fb0e37` 记录了完整 Reader 侧链路：

1. `DISBAND_RECEIVED`，2026-10-28T15:43:10.821515Z；
2. `LEAVE_ENTERED`；
3. `CLEANUP_ENTERED`；
4. `GROUP_LEFT`，`residual=[]`、`groupResourcesCleared=true`；
5. `CLEANUP_SUCCEEDED`，`failures=[]`、`residual=[]`、`groupResourcesCleared=true`；
6. `LEAVE_FINISHED`；
7. `INBOX_RESTART_STARTED`；
8. `INBOX_RESTART_SUCCEEDED`，2026-10-28T15:43:13.276019Z。

旧 stdout 随后再次输出 `INBOX_LISTENING`，stderr 为空。SQLite 以 URI `mode=ro` 查询：该 Reader AIC 没有 `reserved`、`working` 或 `awaiting_completion` 记录；指定旧 Session/Task 无记录。系统中没有 `reader_aip_worker.py` 进程。阶段一据此通过，不依赖 Leader 报告单独推断。

### 旧进程树定向退出

终止前再次确认的旧 Reader 树：launcher shim PID 38972、实际 Partner CPython PID 35424、其 conhost PID 41348。仅执行 `taskkill /PID 38972 /T /F`；三个指定 PID 和旧 Reader 连接随后均消失。Literature PID 20688/42808 和 Repository PID 13244/37668 保持运行，未受影响。全部旧 journal、输出和 SQLite 均保留。

### 阶段二：新版本进程与运行证据

- 新目标/HEAD：`842b9a65615fa9db9a7b459d74bdecb58ce25cf2`。
- 工作树仍只包含两个既有未跟踪文件，未覆盖或清理。
- 启动时间：2026-10-28 23:47:47 CST。
- launcher shim PID：46564，解释器 `<REDACTED_LOCAL_PATH>
- Partner CPython PID：24424，PPID 46564，实际 CPython `<REDACTED_LOCAL_PATH>
- 工作目录由启动参数固定为 `<REDACTED_LOCAL_PATH> `run_group_reader.py ... --serve`。
- Task Worker 配置明确为 `<REDACTED_LOCAL_PATH> root 为目标工作树的 `agents\paper_reader`。
- stdout：`AMQPS_CONNECTED` 后为 `INBOX_LISTENING`；stderr 为空。
- PID 24424 拥有正式 5671 AMQPS Established 连接；应用层日志和 Heartbeat 共同用于健康判定，不以 TCP 单独判定。
- 新 formal journal：`<REDACTED_LOCAL_PATH> `AUDIT_STARTED`；没有 Group、Session、Task、邀请或 Start。
- 新 Heartbeat 本次 uptime 样本包括 0.044、15.058、30.072、45.078、60.081 秒，约 15 秒间隔并单调递增。
- 系统中只有 PID 46564→24424 一条 Reader Partner 链，无重复消费者，无 Task Worker。

### 新源码与单请求预算部署核验

使用正式 `.venv-reader` 做无网络 import 探针，实际解析路径为：

- `<REDACTED_LOCAL_PATH>
- `<REDACTED_LOCAL_PATH>
- `<REDACTED_LOCAL_PATH>

探针确认 `ModelHttpAudit` 构造签名包含 `max_requests=1`、存在 `ModelRequestBudgetExceeded`；`PaperQAProvider.analyze` 不含 `.aquery(`，包含唯一 `call_single` 路径。这证明未来 Worker 的指定解释器和部署源码为新 commit 的单请求实现。

限制说明：本阶段禁止创建 Task/Start，因此实际 `reader_aip_worker.py` 进程尚未生成；其运行中 PID、父子链和内存内模块版本目前为“未验证（尚不存在）”，不能用 import 探针冒充。新 Task 获得授权后，必须在 Start 派生 Worker 时再次核验其 PID、`.venv-reader` 解释器、源码路径和 commit，并以 transport 审计确认真实请求最多 1 次。

### 阶段结论

**READY_FOR_NEW_INVITATION**。该结论仅授权进入全新邀请/Group 建立阶段，不等于授权发送 Start，也不等于正式 S2 已通过。

## 正式 S2 最终验收（2026-10-29 00:06 CST）

### 本轮唯一身份

- Group：`rm-reader-3e4049496f92404ab172df0ee038c1a2`
- Session：`session-c3f99358e92e4e88bfb64926a1ca1c27`
- Task：`task-ebd7867baf114ce09ddcb0c070780ef7`
- Worker runId：`6e387fef93c648c399588c64721481fc`
- Partner formal runId：`336bb9feb98c4afbbdf3621aa3efd6a4`
- 目标 commit：`842b9a65615fa9db9a7b459d74bdecb58ce25cf2`
- Worker 输出：`<REDACTED_LOCAL_PATH>

以下结论只使用上述四元组/runId 的文件和 formal journal，没有混用历史运行。

### Worker 输入、PDF 与初始化

- 输出目录只有本轮 7 个文件：`bound_task.json`、`reader_input.json`、`pdf_resolution.json`、`runtime_initialization.jsonl`、`model_requests.jsonl`、`reader_journal.json`、`reader_worker.log`。
- `bound_task.json` 与 `reader_input.json.payload` 均为 S2、`paper_read`、`paper_notes.v1`、单篇 LoRA 论文。
- PDF 为 26 页、1,609,513 字节，SHA-256 `e9a0d3128767db616085dc0f4e6e455e672e89af823e8ed1282793682787395a`；`pdf_resolution.json` 为 `state=verified`，本轮复用已验证缓存，未重新下载。
- `runtime_initialization.jsonl` 的 `prepare_live_reader`、`provider_construct`、`paperqa_dependencies_import`、`paperqa_settings_construct`、`embedding_model_load` 均具有 started/completed 配对；无 failed 或未完成阶段。
- `reader_worker.log` 只有 embedding 权重加载完成和 LiteLLM provider 信息，无业务异常。

### 唯一真实模型 HTTP 请求

`model_requests.jsonl` 只有两行，且身份字段全部精确匹配本轮 Group/Session/Task/Worker runId：

1. `MODEL_HTTP_STARTED`：2026-10-29T00:00:42.510690+08:00，`requestSequence=1`，`requestId=b4c1d92ba77d42d6b8c8b5d0b72af469`。
2. `MODEL_HTTP_FINISHED`：2026-10-29T00:01:05.797257+08:00，同一 requestSequence 和 requestId；`responseReceived=true`、`httpStatus=200`、`durationMs=23286.648`、`exceptionClass=null`、`cancelled=false`。

因此 started/finished 属于同一个真实物理请求，HTTP 2xx 响应已收到且随后成功解析为业务结果。文件中没有第二条 `MODEL_HTTP_STARTED`、没有 `MODEL_HTTP_REJECTED`、没有其他请求事件。

`reader_journal.json` 同时记录：`modelRequestCount=1`、`modelRequestRejectedCount=0`、`automaticRetries=0`、`modelRequestAuditComplete=true`、`modelRequestAuditFailureStages=[]`。实际进入 transport 的 started 数、审计计数和 Worker journal 完全一致。结论：**严格满足整个 Task 最多一次真实外部模型 HTTP 请求，本轮实际发送 1 次且成功。**

### Worker 运行版本

- 正式 Partner 于 2026-10-28 23:47 CST 从 HEAD `842b9a65615fa9db9a7b459d74bdecb58ce25cf2` 启动；Git reflog 显示该 commit 自 23:40:42 CST 起一直为 HEAD，本轮执行期间没有切换。
- Partner 的 `ReaderExecutor` 配置为 `<REDACTED_LOCAL_PATH> 和目标工作树 wrapper；`aip_reader_partner.py` 使用该已解析解释器、绝对 `reader_aip_worker.py` 路径和项目 ROOT 作为 cwd 派生 Worker。
- 本轮成功导入 PaperQA/LiteLLM、加载 embedding 并完成真实模型请求；launcher `.venv` 缺少该模型依赖，而 `.venv-reader` 具备。
- 本轮 journal 出现 `modelRequestRejectedCount`，并表现为单次 transport/单次结构化结果；这些是目标 commit 新增的运行时行为指纹，旧 commit 不具备。
- 当前 Task Worker 已正常退出，无法事后补采历史 Worker PID 或进程内模块对象；因此“历史 PID/内存快照”未直接验证，但解释器、源码路径和 commit 由确定性派生命令、依赖加载和新版本行为三类证据一致支持。此限制不影响本轮请求/结果真实性判断。

### `paper_notes.v1` 业务结果

`reader_journal.json` 为 `phase=completed`、`readerResultReused=false`、`outcome.error=null`、`businessCompleted=true`、`outcome=paper_note`、`analysisLevel=fulltext`、`returnedCount=1`。

对本轮原始 Result 重新调用项目 `validate_result(result, bound_task)`：通过。结构和内容摘要：

- `schemaVersion=0.2`、`subtaskId=S2`、`taskType=paper_read`；
- 研究问题 summary 541 字符；
- method 420 字符、findings 821 字符、limitations 697 字符；
- 论文来源为 LoRA，paperId 与绑定任务一致；
- 6 条 evidence，全部具有非空 supports 和原文 text；
- locators：`Lo pages 15-17`、`Lo pages 9-11`、`Lo pages 7-8`、`Lo pages 23-24`、`Lo pages 13-14`、`Lo pages 19-20`；
- warnings 为空。

按项目 canonical JSON 算法重新计算的 Result SHA-256 为 `ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`，与 formal journal `RESULT_SAVED.originalSha256` 完全一致；journal 同时记录 `originalSchemaValid=true` 和 `RESULT_READBACK_VALID`。

### Leader 确认、协议终态与清理

Reader formal journal `<REDACTED_LOCAL_PATH> 记录完整顺序：

- 唯一 `GROUP_JOINED`；
- `TASK_COMMAND action=start`；
- `TASK_RESERVATION sqliteState=reserved`；
- `BACKEND_STARTED`；
- `BACKEND_FINISHED outcome=success`；
- `RESULT_SAVED` 与 `RESULT_READBACK_VALID`；
- 来自 Leader 的 `TASK_COMMAND action=complete`；
- `SQLITE_COMPLETED sqliteState=completed`；
- `DISBAND_RECEIVED`、`GROUP_LEFT residual=[] groupResourcesCleared=true`；
- `CLEANUP_SUCCEEDED failures=[] residual=[] groupResourcesCleared=true`；
- `LEAVE_FINISHED`、`INBOX_RESTART_SUCCEEDED`。

本机没有单独的 Leader 结果存档文件；但 Reader 实际收到 Leader 的 `action=complete` 入站协议命令，且该命令发生在 Result 保存/readback 之后，这是 Leader 已接收并确认本轮 Result 的协议证据，不是仅凭本地业务成功推断。

SQLite 使用 `mode=ro` 查询，本轮精确 Session/Task 为 `completed`，更新时间 2026-10-28T16:01:10Z；该 Reader AIC 没有 `reserved`、`working` 或 `awaiting_completion` 残留。Partner stdout 确认 Group left、Inbox restart succeeded、重新 `INBOX_LISTENING`；Heartbeat 持续递增。Task Worker 已退出，Partner 保持健康监听。

### 逐项验收与最终结论

- 四元组/runId 精确关联：PASS
- 初始化完整：PASS
- 真实模型 HTTP 请求恰好 1 次：PASS
- HTTP 200、响应收到、无异常：PASS
- 单 Task 硬预算与审计一致：PASS
- 无第二次请求或拒绝事件：PASS
- 非复用真实 `paper_notes.v1`：PASS
- 研究问题/方法/发现/局限/evidence 可追溯：PASS
- Result schema 与 canonical hash：PASS
- Leader Result 协议确认与 Complete：PASS
- SQLite completed：PASS
- Group/资源清理与 Inbox 恢复：PASS

## 最终结论：PASS

本轮 ResearchMesh Reader 正式 S2 验收通过。剩余阻塞：无。历史 Worker PID/内存快照未在进程退出前保存，已作为证据限制记录，但不与实际解释器/源码/commit 的一致证据冲突，也不影响本轮正式业务、单请求预算、Leader 确认和协议终态的通过结论。

## 跨端证据最终对账（2026-10-29）

### Leader 权威回执

Leader 已提供本轮权威回执：状态链 `accepted → working → awaiting-completion → completed`；Result 已收到并校验；六字段 Result 含 1 篇 fulltext paper note 和 6 条 evidence；canonical SHA-256 为 `ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`；Start 1 次、Complete 1 次；`protocolCompleted=true`、`businessReady=true`、`cleanupSucceeded=true`。

### 1. Result 一致性：PASS

- Partner 原始 Result 来源：`outputs\reader-group-6e387fef93c648c399588c64721481fc\reader_journal.json` 的 `outcome.result`。
- 项目 canonicalization 来自 `aip/literature_audit.py:digest`：`json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)` 后进行 UTF-8 SHA-256；未依赖原始字段顺序、缩进或手工格式化。
- 重新计算值：`ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`。
- Partner formal journal `RESULT_SAVED.originalSha256`：同一值。
- Leader 权威回执：同一值。三方完全一致。
- 项目 `validate_result(result, bound_task)` 重新执行通过。
- 顶层恰好六字段：`schemaVersion`、`subtaskId`、`taskType`、`summary`、`items`、`warnings`。
- 结果为 S2 / `paper_read` / `paper_notes.v1` 对应结构，恰好 1 个 `paper_note`，`analysisLevel=fulltext`，恰好 6 条 evidence；6 条均有非空 locator、supports 和原文 text。
- Partner journal 为 `readerResultReused=false`、`businessCompleted=true`、`phase=completed`、`outcome.error=null`，与真实结果一致。

### 2. SQLite 与协议状态：PASS

- Partner formal journal 对该 Group 的事件计数：`GROUP_JOINED=1`、`TASK_COMMAND=2`、`TASK_RESERVATION=1`、`BACKEND_STARTED=1`、`BACKEND_FINISHED=1`、`RESULT_SAVED=1`、`RESULT_READBACK_VALID=1`、`SQLITE_COMPLETED=1`。
- 两条且仅两条 TaskCommand 分别为：sequence 3 `action=start`、sequence 9 `action=complete`；Group/Session/Task 全部精确一致，不存在重复 Start 或 Complete。
- Partner journal 的 success/Result/Complete 顺序与 Leader 状态链和 Start/Complete 次数一致。
- SQLite `mode=ro` 查询：精确 Session/Task 唯一记录为 `completed`；Reader AIC 的 `reserved`、`working`、`awaiting_completion` 活动记录为空。
- Leader `protocolCompleted=true`、`businessReady=true` 与 Partner `RESULT_READBACK_VALID`、入站 Complete、`SQLITE_COMPLETED` 一致。

### 3. Worker 版本与运行环境：PASS（带已记录限制）

- `bound_task.json == reader_input.json.payload == reader_journal.json.task`，证明输入在绑定、派生和 Worker 执行层一致。
- 正式 Partner 从 HEAD `842b9a65615fa9db9a7b459d74bdecb58ce25cf2` 启动，本轮期间 reflog 无 commit 切换。
- `ReaderExecutor` 将已解析的 `.venv-reader` 路径交给 factory；factory 的确定性命令为：该 Python、`-X utf8`、项目绝对 `reader_aip_worker.py`、本轮 input/record-dir、目标 wrapper root、`--execute`、bound task 以及本轮 Group/Session/Task/Worker runId，并以项目 ROOT 为 cwd。
- `.venv-reader` 无网络探针解析到目标工作树的 `model_http_audit.py`、`reader_aip_worker.py` 和 provider；`ModelHttpAudit` 签名为 `max_requests=1`，provider 不含 `.aquery(` 且包含 `call_single`。
- 本轮 runtime initialization 全部配对 completed，且新版本特有的 `modelRequestRejectedCount=0` 与单次调用行为实际出现。
- 证据限制：Worker 已正常退出，退出前 PID 和进程内模块对象快照未保存，因此这两项保持“未直接验证”；未将静态探针冒充历史内存快照。确定性命令、解释器依赖、初始化、commit 行为指纹和实际 HTTP/结果证据相互一致。

### 4. 模型 HTTP 请求预算：PASS

- `model_requests.jsonl` 只有 2 个事件：一个 `MODEL_HTTP_STARTED` 和一个 `MODEL_HTTP_FINISHED`。
- 两者均精确匹配本轮四元组/runId，`requestSequence=1`，共同 requestId `b4c1d92ba77d42d6b8c8b5d0b72af469`。
- finished：`responseReceived=true`、`httpStatus=200`、`exceptionClass=null`、`cancelled=false`、`durationMs=23286.648`。
- 没有第二个 started，没有 `MODEL_HTTP_REJECTED`，没有其他请求事件；不得也未补造缺失事件。
- Worker journal：`modelRequestCount=1`、`modelRequestRejectedCount=0`、`automaticRetries=0`、`modelRequestAuditComplete=true`、audit failure stages 为空。
- 实际进入 transport 恰好 1 次，预算计数、审计计数、HTTP 成功和业务完成状态一致。

### 5. GROUP_LEFT 与资源清理：PASS

- Reader journal：`GROUP_LEFT residual=[] groupResourcesCleared=true`；`CLEANUP_SUCCEEDED failures=[] residual=[] groupResourcesCleared=true`；随后 `LEAVE_FINISHED`、`INBOX_RESTART_SUCCEEDED`。
- Partner stdout 再次出现 `INBOX_LISTENING`；Heartbeat 持续递增。
- SQLite 无活动 reservation/working/awaiting-completion；Task Worker 已退出；Partner 保持健康监听。
- Leader `cleanupSucceeded=true` 与 Reader 侧清理结果完全一致。

### 跨端最终结论：PASS

Leader、Partner formal journal、Worker 输出、HTTP transport audit 和 SQLite 五类证据在身份、Result 哈希、请求次数、协议终态和资源清理上完全一致。证据限制仅为未保存历史 Worker PID/内存快照，已明确标注且未被推断为已验证；除此之外无剩余阻塞。
