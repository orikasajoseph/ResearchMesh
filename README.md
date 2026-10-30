# ResearchMesh 智研协作

## 当前状态（2026-10-22）

- 本机三智能体链已真实运行：Planner → Literature → Reader → Repository 静态分析 → 报告。入口为 `run_aip_research.py`；Repository 可选增强另有真实验收记录。
- 正式梧桐 Literature 第三次单任务已通过 Leader 自动验收：返回正确 LoRA 论文，六字段 Result 校验、Complete/completed 和 Leader 清理成功。
- 吴已反馈同一 Task 的 SQLite 唯一记录为 completed，收到 GROUP_JOINED 与 DISBAND_RECEIVED；该次 Provider HTTP 请求次数、Partner 缓存回读和 GROUP_LEFT 证据仍待核对。
- 2026-10-30 最终验收：显式 AIC 的正式平台 S1→S2→S3 连续业务链已完成并通过；三个 Partner 的单条 Kafka heartbeat 已获得 Broker ACK 与指定 offset 独立回读。平台 Consumer 消费、Registry alive 关联及三个正式 Agent 的 DSP/Discovery 可见性仍待平台核验，自动 Discovery 不记为通过。最终脱敏材料见 [`deliverables/final-acceptance-20261030/`](deliverables/final-acceptance-20261030/)。
- 三个业务包均已在 `agents/` 中。协议沿用 v0.2 七字段 Task、六字段 Result，平台身份与状态置于官方 AIP 消息中。

当前核对的已推送基线为 `zhao/planner-integration@cd56780`；后续补丁是否提交以 Git 状态为准。
证据位置、修复说明及剩余工作见 [当前进度与本地修复](docs/current_status_20261022.md)。下方带早期日期的条目是历史记录。

正式梧桐 Literature 单任务 Leader 入口：`run_platform_literature.py`。
默认仅离线预检；双方就绪并确认 Partner commit 后，通过正式 Group 发送一次检索任务。
七字段 Start → 校验六字段产物 → Complete/completed → 清理，详见
[正式平台 Literature 单任务联调](docs/platform_literature_single_task.md)。

> 2026-10-20 后续集成：OpenAlex Provider、Resource Resolver配置、动态Reader接缝已选择性迁入，保留旧链路兼容；备用检索/资源解析默认关闭，显式配置才新增联网。平台稳定业务入口为 `business_execution.execute_business_task`，默认拒绝执行，不新增协议字段。隔离Worker与持久Reader复用方式见 [给吴的业务入口说明](docs/delivery/platform_business_entry_v1.md)。新增能力尚未进行真实联网/收费或正式平台验收。

> 2026-10-20 团队快照对齐：本轮集成起点是 `zhao/planner-integration@d4bb9d8`，业务增量提交为 `e3586d5`。13项业务同路径中8个Repository业务模块与张预览一致，Reader业务包已选择性迁入。后续Provider/动态接缝已补齐，当前684项免费回归通过；状态与历史阶段区分见 [逐文件集成记录](docs/delivery/zhao_integration_alignment_20261020.md)。

> 2026-10-20 已补齐新问题的本机三智能体统一入口：`run_aip_research.py` 支持规划→检索→阅读1篇→仓库1个→报告，兼容原两阶段。LoRA 场景已完成一次真实 Planner→检索→阅读→仓库静态分析→报告验收；这不代表新增 OpenAlex/动态平台接缝或正式梧桐链路已经验收。默认仓库静态模式，额外增强必须显式开启。运行方式与边界见 [统一入口说明](docs/three_agent_single_entry.md)。

> 2026-10-19 Repository 双模式已整合：默认静态零模型；仅 `--analysis-mode enhanced --execute` 才允许收费增强。复用旧 LoRA Reader 的真实增强验收已通过（模型 1 次），不代表正式梧桐联调完成。见 [运行说明](docs/repository_dual_mode.md)、[真实增强验收](docs/repository_dual_mode_live_20261019.md) 和 [桌面项目整合说明](docs/repository_dual_mode_integration_20261019.md)。

> 2026-10-21 invitation-only 已由双方核对通过。`run_group_invitation.py` 默认离线，真实 ACL 和邀请分别需要显式开启。
> 见 [建群与邀请说明](docs/group_invitation.md)。此入口只负责邀请，不发送 TaskCommand；业务验收使用独立的 Literature 入口。

> 2026-10-16 平台接入更新：Leader 已取得身份与 clientAuth 证书，独立梧桐 MQ 连接检查通过。
> 新增可配置的 `run_mq_check.py`，默认离线、加 `--execute` 才连接；不建群、不发任务、不调用模型。
> 安装、运行、安全边界及与吴的下一轮对齐见 [MQ 检查工具说明](docs/platform_mq_check.md)。
> 下文带较早日期的状态与测试数量保留为历史说明，不代表本轮已完成平台 ADP/群组科研链。

> 2026-10-04 开发分支已实现业务接口 v0.2：`requiredSkill`、`repository_analyze`、明确仓库地址后才静态分析。待全组确认后合并，尚非全组冻结版本。请先读 [v0.2 接口](docs/InterfaceContract_v0.2.md) 和 [本次交接](docs/v02_handoff.md)。旧 v0.1 文档、历史验收只作参考。

## 组员首次运行

建议使用 Python 3.12（吴当前交付包至少需要 3.11）。在克隆的项目目录创建各自的环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-llm.txt -r requirements-aip.txt
.\.venv\Scripts\python.exe -X utf8 verify_project.py
.\.venv\Scripts\python.exe -X utf8 run_demo.py
```

上述验证不需要 API Key、不调用真实模型。真实规划另将 `.env.example` 复制成自己的 `.env` 并填写本机 Key；不要覆盖已有 `.env`。团队不共享密钥和虚拟环境。

`run_wu_aip.py` 另外需要吴的交付目录，通过 `--wu-root` 指定；默认计划已放入仓库 `examples/aip_transport_plan_v02.json`，不再依赖赵电脑上的 outputs。该入口仅检查本地 AIP 传输。

## 共享仓库目录与当前状态

- `planner/`：规划模块的预留归属目录；当前可运行的 Leader / Planner / Resolver 仍位于根目录与 `resolver/`，本次不移动入口。
- `aip/`：AIP / 梧桐适配的归属目录；由其他成员继续接入。
- `agents/`：Literature、Paper Reader、Repository 三类功能 Agent。
- `common/`：公共基础模块，包括统一 LLM Provider。
- `docs/`：业务协议、接口说明与联调交接文档。

早期 Literature / LLM 交付记录（历史，不代表当前平台状态）：

- Literature Agent v0.1：**DONE / FROZEN**。
- Semantic Scholar real API：**PASS**。最新 `AlphaEvolve` 回归返回 5 篇，
  `latencyMs=2750.866`，`retryCount=0`；第一篇有 DOI，`pdfUrl=null` 属正常缺失。
- DeepSeek LLM Provider v0.1：**PASS**，已在迁移后完成真实联网验证。
- 当前 AIP integration：本机三 Agent 已通过，正式 Literature 单任务 Leader 已通过；正式 Reader/Repository 和全链仍待联调。
- 当前 Planner integration：`run_research.py` 提供直接检索，`run_aip_research.py` 提供本机三阶段链；正式平台自动发现与编排尚未接通。

张的 API 实测、赵的业务适配及本地 Mock 分别保留验收记录；它们不代表已经完成真实平台发现或 AIP 科研链路。

Literature Agent 的冻结边界为 `literature.search` / `literature_search.v0.1` /
`paper_records.v1`，见 [Wrapper 交接契约](docs/INTERFACE_CONTRACT_v0.1.md) 和
[AIP 交接说明](docs/AIP_HANDOFF.md)。这些交接文档中的 `requiredCapability` / `code_search` 属于旧 Leader 业务约定；当前赵分支以 [v0.2 接口](docs/InterfaceContract_v0.2.md) 为准，由 `literature_adapter.py` 转换到张已冻结的 Wrapper 输入与输出。业务版本不同不表示 AIP 协议版本不同，全组仍需确认统一的 Leader 契约。

公共 LLM Provider 已提供同步 `chat` / `generate_json`；当前 Planner 仍使用已验证的异步规划适配层，尚未切换到这个公共模块。

## 自然语言规划与真实检索（新入口）

在 VS Code 打开项目目录，终端运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_research.py
```

看到“请输入研究问题：”后输入一句话并按回车，不用写 JSON。例如：

> 只检索 2023—2025 年人工智能用于微生物研究的论文，最多 5 篇，不阅读全文，不分析代码。

也可以一次运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_research.py --query "只检索 2023—2025 年人工智能用于微生物研究的论文，最多 5 篇，不阅读全文，不分析代码。"
```

前提：已有虚拟环境、模型依赖和 `requirements-literature.txt` 中的检索依赖。
本机 `.env` 中配置已有 DeepSeek（或现有 Provider）Key 和 `SEMANTIC_SCHOLAR_API_KEY`；不要把 Key 上传 GitHub。
Wrapper 默认从仓库的 `agents/literature` 加载，模型最多请求 2 次（格式修正最多一次）。
所有当前 Provider 的真实 Semantic Scholar HTTP 请求通过 `semantic_http.py` 的共享 gate：同进程串行保护，开始时间最小间隔 1.2 秒，包含不同 task、Provider、线程和 event loop。
遇到可重试的 `RATE_LIMITED` 时，优先按实际 `Retry-After` 等待；未提供或无效则等待 2、4、8 秒，每次另加 0.1—0.3 秒 jitter，最多重试三次；重试期间不再次调用模型。
服务端要求等待超过 120 秒时，本轮停止并保存计划，不提前重试绕过限制。长等待分段以保持可取消。
SDK 和接入时 Provider 的内层重试仍关闭，避免重复重试叠加。
其他错误、空检索结果不会触发这套限流重试；不能保证服务端一定解除限流。
两类配置和本机 Wrapper 先预检，再调用模型；真实模型调用会消耗额度。

执行流程：自然语言 → 真实 Planner → v0.2 计划校验 → 检索业务适配 → 张的 Literature Wrapper → Semantic Scholar → 统一结果与报告。
这是本地直接调用，不走 ADP 或 AIP，不是赛事平台互联证明；没有修改张的 Agent 源码。
当前只接受一个无依赖的 `literature_search` 任务，不会悄悄截取复合任务中的检索部分。
模糊需求只追问；阅读或仓库分析需求会明确拒绝执行；接口失败不回放旧样例或伪造论文。

每次保存到独立的 `outputs/research-live-时间戳/`：`plan.json`、`planner_metadata.json`、
`outcome.json`、`metadata.json`、`pipeline.json`、`reader_inputs.json`（有结果时）与 `report.md`。
在 VS Code 右键 `report.md`，选择“打开预览”即可阅读。
Planner 阶段元数据仍说明该阶段未执行 Agent；整条链路的实际执行情况以 `metadata.json` 为准。
澄清或失败也保存可用的计划和诊断记录；预检失败/空输入不联网。
澄清目前不自动续接会话，需要补全研究问题后重新运行。

计划通过校验后立即保存检查点，再开始检索。因此限流恢复耗尽或按 Ctrl+C 中断后，不必重新输入问题。
终端会给出本次计划的续跑命令，例如（替换为你实际的记录目录）：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_research.py --resume "outputs/research-live-实际时间戳"
```

`--resume` 也接受 `plan.json` 文件路径；只需要检索 Key，不要求模型 Key，不调用模型、不提示输入问题。
每次续跑生成独立的新记录目录，不覆盖此前失败或成功的历史。
限流恢复次数和各次结果在 `outcome.json` 的 `diagnostics.rateLimitRecovery` 中查看；
续跑记录的 `metadata.llmUsed=false`，不能把旧计划的模型调用算作本轮调用。

### Semantic Scholar 定向限流诊断

冷启动只调用一次，不调用 Planner，不重试：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/diagnose_semantic_scholar_rate_limit.py
```

脚本读取本机 `.env`，创建一次 Provider，先等待 2 秒，搜索 `world model`、`limit=1`；
输出到 `outputs/semantic-cold-时间戳/diagnostics.json`。
HTTP 诊断只记录 Key 是否配置/附带、实例 ID、端点、请求顺序、时间、状态码、gate 等待、白名单限流头、脱敏错误说明及非敏感搜索参数，不记录 Key/Authorization、Cookie 或 traceback。
完整流程的同类记录在 `outcome.json` 的 `diagnostics.httpRequests` / `processHttpRequests`；所有业务 Schema 和 `paper_records.v1` 均不变。
排查与实测指标见 [定向诊断记录](docs/semantic_rate_diagnosis_20261008.md)。

同一个 Semantic Scholar Key 的配额会被使用它的实例共享；本机 gate 不能协调其他电脑或其他进程。
正式联调建议 Planner/AIP → 唯一部署的 Literature Agent → Semantic Scholar；组员通过该 Agent 调用，避免多台机器直接使用同 Key 并行测试。
本轮继续使用 relevance `/paper/search`，并在单次请求传递 query、year 范围和 limit。匹配总数不是下载量。
未来可评估 `searchMode=relevance|bulk`，仅列入 backlog，本轮未实现或切换。

> 2026-10-04 新增：运行 `.\.venv\Scripts\python.exe -X utf8 run_wu_aip.py`，用已保存的真实计划对接吴的本地 AIP Partner。只验证文献检索任务传输，不执行真实科研或平台发现。见 [本地联调说明](docs/wu_local_integration.md)。

> 2026-10-02 第二周更新：DeepSeek 已真实接通（不是待办）；新增统一 Provider、严格 skill/input 校验、论文数据绑定、真实规划 HTTP 入口、官方 SDK 完成确认组件及新问题验收。详见 [第二周进展](docs/week2_progress.md) 和 [团队接口约定](docs/InterfaceContract_v0.1.md)。下文 `run_demo.py`、`api.py` 仍特指 Mock 模式。

当前可以运行“预写样例计划 → 按能力选择本地模拟执行者 → 按依赖执行 → 汇总结果”。
原模拟入口没有连接真实大模型、论文数据库、平台发现或 AIP。所有模拟科研数据和链接不能用作科研依据或赛事互联证明。
新增独立的 DeepSeek Planner 适配器，可配置本机 Key 后调用官方模型并校验计划；真实连通性和规划结果以实际运行记录为准。

## 一键演示

需要 Python 3.9 或更新版本，以及 Pydantic、FastAPI、Uvicorn。自动测试还需要 jsonschema。
当前电脑这些依赖已经可用，不需要 API Key；给其他组员安装时建议使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

虚拟环境中，把下文的 `python` 换成 `.\.venv\Scripts\python.exe` 即可。依赖版本为本机实际验证版本；未宣称测试过其他版本。

```powershell
cd "C:\Users\orika\Desktop\智创赛\ResearchMesh"
python -X utf8 run_demo.py
```

默认使用第一个预写样例。正常执行顺序：先 S1 检索，之后 S2 阅读与 S3 代码检索并发。
成功应显示 3 个任务成功。每次输出到新的 `outputs/mock-run-...` 目录，不覆盖旧结果：

- `plan.json`：原始样例计划。
- `research_result.json`：本地运行汇总，包含业务结果和内部执行记录；不是 AIP TaskResult。
- `run_log.json`：执行者、能力、耗时与错误。
- `report.md`：可阅读的模拟报告。

其他演示：

```powershell
python -X utf8 run_demo.py --fail-task S2
python -X utf8 run_demo.py --fail-task S1
python -X utf8 run_demo.py --case 2
python -X utf8 run_demo.py --case 3
```

分别验证：S2 失败但 S1/S3 结果保留；S1 失败则下游跳过；单篇论文阅读；需求模糊时只返回追问。
脚本成功退出只表示模拟闭环结束，不代表每项业务任务都成功，请查看执行记录。

## 自动测试

```powershell
python -X utf8 -m unittest discover -s tests -v
```

覆盖 Planner 解析、任务与结果格式、依赖、并发、能力缺失、替换执行者、故障、超时和结果保留。
安装可选模型依赖后，还测试 DeepSeek SDK 请求、配置、安全报错、资源关闭和文件保存。所有自动测试都使用假 Key / HTTP Mock，不访问真实 DeepSeek，不消耗额度；没有模型依赖时这些可选测试会跳过。

## DeepSeek 真实规划独立入口

详见 [DeepSeek 接入与验收步骤](docs/deepseek_setup.md)。保持 `run_demo.py` 和 `/plan` 的原有模拟行为，不自动将所有调用切换到付费模型。

```powershell
& "D:\Anaconda\python.exe" -m pip install -r requirements-llm.txt
```

只在本机 `.env` 中填写 `DEEPSEEK_API_KEY`，其他配置已提供默认值。`.env.example` 是无 Key 的共享模板。
不要把真实 Key 放进 Python 代码、聊天、截图或项目压缩包。

```powershell
& "D:\Anaconda\python.exe" -X utf8 test_deepseek_connection.py
& "D:\Anaconda\python.exe" -X utf8 run_deepseek_planner.py --query "调研 AlphaEvolve 的研究进展并分析复现难度" --max-attempts 1
```

第一条有 Key 时最多一次短请求；第二条生成计划并通过现有 Pydantic 校验，保存到 `outputs/live-planner-时间戳/`。
省略 `--query` 时，可在 VS Code 的终端输入研究问题。默认格式失败最多修正一次（最多两次调用），`--max-attempts 1` 用于控制首次验收调用次数。
API 调用会计费。缺少 Key 时两条命令都会明确报配置错误且不联网；提供商失败不会使用假计划兜底。

此入口仅完成研究需求规划，不检索论文、不阅读全文、不分析真实仓库、不执行 Mock Agent，也不是 AIP 互联证明。

## 本地 Planner 接口（模拟模式）

在项目目录启动：

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 9040
```

打开 <http://127.0.0.1:9040/docs> 查看接口，或 <http://127.0.0.1:9040/health> 检查服务。
自动文档页面的浏览器资源可能需要网络；即使该页面打不开，也可直接调用下列接口。
在另一个终端运行：

```powershell
$demoCases = Invoke-RestMethod 'http://127.0.0.1:9040/demo/cases'
$planBody = @{ userQuery = $demoCases[0].userQuery } | ConvertTo-Json
Invoke-RestMethod 'http://127.0.0.1:9040/plan' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($planBody))
```

`/plan` 只接受 `/demo/cases` 中的三个问题，返回 `demoOnly=true`、`plannerMode=fixture` 和计划。
新问题返回 409，而不是套用旧答案；空白/过长/错误类型输入返回 422。
该接口属于 Leader 本地规划入口，不是 AIP Partner 接口，也不会执行检索、阅读和代码任务。
完整模拟闭环仍用 `run_demo.py`。终端按 Ctrl+C 停止服务。

## 一键保存验收证据

```powershell
python -X utf8 verify_project.py
```

生成新的 `outputs/acceptance-时间戳/`：自动测试输出、实际 HTTP 响应、环境版本、五种场景的计划/结果/日志/报告。
测试服务使用随机回环端口并自动关闭，不访问外部模型、科研数据库或平台。
详见 [第一周验收与交接](docs/week1_acceptance.md)、[架构图](docs/architecture.png)（[可编辑 SVG](docs/architecture.svg)）。

## 各文件职责

| 文件 | 职责 |
|---|---|
| `planner.py` | 提示词、模型调用接口、JSON 解析与校验 |
| `deepseek_model.py` | DeepSeek 配置、安全异步 SDK 适配器和 JSON 请求 |
| `test_deepseek_connection.py` | 手动付费连通性检查，不属于自动测试 |
| `run_deepseek_planner.py` | 独立的真实规划入口及计划保存 |
| `demo_planner.py` | 固定响应的 ExampleModel；不处理任意新问题 |
| `planner_contract.py` / `result_contract.py` | 当前开发版 v0.2 任务与结果校验 |
| `compatibility.py` / `migrate_plan.py` | 显式迁移旧计划，缺少仓库地址时阻止迁移，不覆盖历史记录 |
| `common/models.py` | 唯一的 Pydantic 数据模型来源 |
| `export_schemas.py` / `schemas/` | 导出和保存内部计划、任务、业务任务、业务结果 Schema |
| `api.py` | 明确标注 fixture 模式的本地 /plan、/health、/demo/cases |
| `resolver/base.py` | 按能力找执行者的抽象接口 |
| `resolver/mock.py` | 本地测试注册表，不是平台发现 |
| `mock_agents.py` | 检索、阅读、代码三个模拟执行者 |
| `orchestrator.py` | 去掉内部字段、传递依赖结果、并发执行和故障处理 |
| `aggregator.py` | 汇总为带模拟警示的报告 |
| `run_demo.py` | 连接上述模块并保存演示结果 |
| `verify_project.py` | 一次性运行测试、演示、HTTP 检查并保存证据 |
| `docs/interface_notes.md` | 与吴和张后续接入时的接口边界 |

## 格式与 AIP 边界

v0.2 沿用 camelCase 字段风格，任务名和技能名见新的接口文档。
`requiredSkill`、`dependsOn` 属于 Leader 内部调度，不直接放进跨 Agent 的业务任务负载。
业务结果只包含 `schemaVersion/subtaskId/taskType/summary/items/warnings`，没有另一套 status。

模拟入口的 resolver 和 executor 是同一个进程里的函数调用，不是 AIP 证明。
新本地 AIP 入口使用官方 SDK 的 TaskCommand、TaskResult 和标准状态；业务字典是消息内的数据，不是另一套通信协议。
本机 AIP 文献检索和单篇全文 Reader 在2026-10-12已真实通过；2026-10-20本机三Agent静态链通过；2026-10-22正式平台Literature单任务Leader检查通过。正式Reader/Repository及ADP全链仍待验收，科研结论仍需人工核验。

Schema 导出和校验细节见 [数据模型说明](docs/schema_notes.md)。Planner 遇到格式/结构错误最多修正一次，
单次模型调用默认最多等 30 秒；两次格式错误或调用失败不会用虚构计划兜底。

## 历史待办与当前平台待核验

当前最终状态以 [`deliverables/final-acceptance-20261030/final_acceptance_report_20261030.md`](deliverables/final-acceptance-20261030/final_acceptance_report_20261030.md) 为准。剩余待办仅包括平台 Consumer 消费证明、Registry alive 关联、DSP对象级索引与正式 Agent 的 Discovery 可见性核验；历史进度文档仅描述当时状态。

前两周记录见 [2026-10-12会议进度](docs/meeting_progress_20261012.md) 和 [当时接入说明](docs/week2_integration_ready_20261012.md)，其中“尚未实现/验收”的文字仅描述当时状态。分支提交和 main 合并状态另以 Git 为准。
Mock 数据和格式检查不证明科研结论真实，也不证明平台发现或赛事互联已完成。

## 文献接入与早期交付记录

张的 Literature Wrapper 已增加独立适配与运行入口：`run_literature.py`。支持交付样例回放和显式实时调用，不等于 AIP/ADP 已接通。安装、字段映射、运行步骤及最新 152 项测试结果见 [文献接入说明](docs/literature_integration.md)。

张的模块已随 main 合入本分支，后续无需另取文献交付压缩包。以下检查使用仓库内 Wrapper 和离线替身，不读取 Key 或请求真实 API：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_literature_delivery.py --wrapper-root agents/literature
.\.venv\Scripts\python.exe -X utf8 -m unittest agents.literature.tests.test_researchmesh_literature common.llm.tests.test_researchmesh_llm -v
```

真实检索仍需本机 Semantic Scholar Key；调用时可将 `run_literature.py` 的 `--wrapper-root` 指定为 `agents/literature`。当前 `agents/paper_reader` 与 `agents/repository` 均含已选择性整合的业务实现，运行时分别需要对应依赖环境。

## 本地 AIP 检索与阅读进展（2026-10-12）

真实文献 AIP 已通过：`run_aip_literature.py`。单篇 Reader AIP 入口：`run_aip_reader.py`，默认只做免费预检，加 `--execute` 才会调用收费 Reader。复用成功的 AIP 检索结果与已核验 PDF 缓存；没有自动模型重试。2026-10-12使用的是张Wrapper独立副本，后续业务实现已迁入 `agents/paper_reader`，当前统一入口默认使用仓库内版本。

详情和本机命令见 [Reader AIP 接入说明](docs/aip_reader_live_20261012.md)、[文献 AIP 接入说明](docs/aip_literature_live_20261012.md)。382项是Reader接入阶段的历史离线测试数量；最新数量请看新验收记录，离线回归不代替真实 Reader 或平台验收。

## 单入口与可共享交付

`run_aip_research.py` 整合真实Planner（可选已有计划）→本地AIP检索→已知arXiv全文核验→本地AIP单篇阅读→可选单仓库分析→统一报告。默认不联网、不计费；`--execute`才启动，失败阶段不自动付费重跑。支持两阶段和三阶段计划，Reader/Repository各限1篇/1个；操作见 [当前统一入口说明](docs/three_agent_single_entry.md)。

操作、限制及换机说明见 [独立工作收尾与运行指南](docs/independent_completion_20261012.md)。`export_team_evidence.py`只导出经过复核的数量/耗时和文件哈希；`scripts/prepare_team_sync.py`只检查Git候选和泄密风险，不提交或推送。未经审核的PDF、`.env`、证书及`outputs`原始日志不应打包上传。

此次赵开发分支交付范围、425项回归与队友接收方式见 [GitHub交付说明](docs/GitHub_sync_20261012.md)。历史文档中的“尚未提交/推送”描述的是对应开发阶段，不代表本次交付已合入main；以远端分支的实际提交为准。
