# ResearchMesh：本地模拟闭环

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
| `planner_contract.py` / `result_contract.py` | 共享 v0.1 任务与结果校验 |
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

沿用共享 v0.1 的 camelCase 字段，不照搬旧个人流程中的 task_id/task_type/status。
`requiredCapability`、`dependsOn` 属于 Leader 内部调度，不直接放进跨 Agent 的业务任务负载。
业务结果只包含 `schemaVersion/subtaskId/taskType/summary/items/warnings`，没有另一套 status。

当前 resolver 和 executor 是同一个进程里的函数调用，并未声称符合完整 AIP 通信流程。
后续真实发现/AIP 适配器应使用官方 SDK 的 TaskCommand、TaskResult 和标准状态，不能把这些本地字典直接当作协议消息。
阅读和代码任务的真实输入格式仍需全组与具体 Agent 核对，详见 `docs/interface_notes.md`。

Schema 导出和校验细节见 [数据模型说明](docs/schema_notes.md)。Planner 遇到格式/结构错误最多修正一次，
单次模型调用默认最多等 30 秒；两次格式错误或调用失败不会用虚构计划兜底。

## 尚未完成

DeepSeek 的本机 Key 配置、真实连通性与新问题规划验收；真实平台发现与 AIP 生命周期；真实科研 Agent；真实端到端联调。
Mock 演示与测试通过仅证明这一步的本地框架有效，不代表第一周所有交付或赛事要求均已完成。
