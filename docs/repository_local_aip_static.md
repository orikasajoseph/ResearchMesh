# 指定仓库 → Leader → 本地 AIP → Repository Agent（静态专用）

更新：2026-10-15。实现阶段的验证说明；当前发布交接见 `docs/team_handoff_20261015.md`，版本以 Git 提交为准。

## 现在可以做什么

输入一个明确的 GitHub 仓库 HTTPS URL，Leader 创建一个确定性的 Task v0.2
独立任务，通过现有 `AipOrchestrator`、`LocalAipResolver`、`AipAgentExecutor`
调用官方 ACPs SDK 2.2.0 的本地 AIP Partner。
Partner 在独立 Python 3.12 环境中调用张的原始 Repository Wrapper，
执行 shallow clone 与真实 CodeWiki 静态分析，返回 `code_records.v1`。
最后保存中文 Markdown 报告和原始 JSON。

直接 URL 模式构造指定仓库任务，不调用 LLM Planner，不是自然语言理解演示。
LocalAipResolver 是显式本地能力注册，不是梧桐 ADP 自动发现。

后续同日新增：可复用已完成 Reader 笔记做无模型词面对照，见
`docs/reader_repository_handoff.md`；不是语义复现难度分析。

## 在本机运行（VS Code 终端）

先进入 ResearchMesh 文件夹。环境已准备好，不必重装，不用填写 API Key。

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --execute
```

也可省略 `--repository`，运行后只粘贴链接，不需要手写 JSON：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --execute
```

省略 `--execute` 只做无网络预检，不 clone、不调用 AIP 任务或模型。
也支持 `--plan examples/repository_s3_week3.json --execute`；只能提供单独、无依赖的 Repository 任务。
不要直接把包含 S1/S2/S3 的整条科研计划传进来，本入口不会静默忽略其他任务。

成功时显示 `protocolCompleted: True`、`businessReady: True` 和报告路径。
在 VS Code 打开输出目录中的 `report.md`，按 Ctrl+Shift+V 预览。
每次生成新目录 `outputs/aip-repository-*`，不会覆盖历史记录。

报告提供文件/组件、静态依赖边、声明的入口与命令、带 commit 的源码证据链接。
依赖边不是完整的软件包安装清单，声明命令并没有执行；空字段不证明不存在相关能力。

## 安全与能力边界

- DeepSeek 增强固定关闭，`enrichment_enabled=False`。不读取项目 `.env`，不要求模型 Key。
- Worker 去除继承的 API Key 等凭据并设置 `PYTHON_DOTENV_DISABLED=1`。
- Worker 的 Python socket 连接被禁用，误入模型/API 网络路径会失败；Git 子进程只用于 clone。
- 不执行目标仓库代码、安装目标仓库依赖、执行 README/CI 中的命令或验证复现。
- 不搜索仓库、不从论文猜仓库。原生 Wrapper 不消费 paperNotes，原生 adapter 仍拒绝非空上下文；
  新复合适配层可显式消费已验证的 fulltext 笔记并做词面对照，不能冒充语义理解或复现验证。
- 一个明确仓库；本轮验证对象是小型公开仓库，不承诺大仓库、私有仓库或所有语言均可分析。
- 仅监听随机的 127.0.0.1 端口，临时服务结束后停止。无平台身份绑定、ADP、MQ 或公网部署。
- GitHub 证据链接由 clone 的实际 commit 与文件位置构造，未逐条联网检查。
- 不生成模型概述和复现难度结论，许可证及论文/代码关联不作核验声明。

## 结构与记录

- `run_aip_repository.py`：输入、确定性计划、预检、现有 Leader 调度、验收与报告。
- `resolver/local.py`：按 `repository.analyze` 匹配本地注册的 AIP 执行者。
- `aip_repository_partner.py`：官方 AIP handler 后台运行并等待隔离 Worker。
- `repository_aip_worker.py`：静态环境校验、克隆与分析、模型/网络保护、结果落盘。
- `repository_adapter.py`：业务七字段映射、结果关联与证据验证，转换 `repository_record` 为共享 `repository`。
- `agents/repository/researchmesh_repository/*.py`：原样引入张的八个源码文件，来源 commit
  `be99b31b3c8411ea88a96d08b0337d6049882046`，没有修改张的原分支。

Leader 内部 `requiredSkill`/`dependsOn` 不进入 Partner 任务。
Leader 先绑定输入（会补齐空的 papers/paperNotes），七个业务字段才通过 AIP 发送。
Partner 的专用 adapter 检查空上下文后转换为张的严格原生输入，未另造协议。
状态沿用 `working → awaiting-completion → completed`，通过 get 查询并 complete 确认。

记录含 `plan.json`、`repository_input.json`、`repository_preflight.json`、
`environment.json`、`backend_outcome.json`、`outcome.json`、`acceptance.json`、
`report.md` 和阶段日志。失败会保留记录，不自动重试。
输出、隔离环境、Key 不入 Git。不要将单独的协议完成误报为静态分析业务成功。

## 新电脑准备

Leader 环境依照项目原有步骤安装好后，用 Python 3.12 运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/setup_repository_static.py --execute
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject
```

需要 Git 和下载依赖的网络。脚本只创建 `.venv-repository`，不改现有 Leader/Reader 环境。
最小静态依赖固定在 `agents/repository/requirements-static.txt`；CodeWiki 用 `--no-deps`
固定安装 Git commit `f0975ea19b17568ceb55cd5422206e1a1ae13c0b`，运行时核验安装来源。
分词词表预热到隔离环境自己的 `runtime-cache/tiktoken`，不是模型请求。
这个精简环境故意不安装 CodeWiki 的完整 LLM/文档生成依赖，不能用来跑整个 CodeWiki CLI，
也不宣称完整发行包的 `pip check` 通过。无需 WSL、Docker 或模型 Key。
本机安装脚本重复执行已验证；未冒充第二台干净电脑安装验收。

## 本轮已验证

- 全项目：485 项自动测试，0 跳过；5 个模拟场景及本地 HTTP 检查通过。
- 真实 sampleproject：官方本地 AIP completed，backend 与 AIP 产物一致；12 组件、7 依赖边、16 证据。
- 本次 backend 总耗时约 2.12 秒（clone 约 1.75 秒、静态分析约 0.32 秒），不代表所有仓库速度。
- 模型调用 0；Python 外连尝试 0；目标代码未执行、目标依赖未安装。
- 八个引入的张源码文件与固定提交快照逐文件比对一致。
- 完整回归记录：`outputs/acceptance-20261015-181817-309867`。
- 真实链路记录：`outputs/aip-repository-20261015-181829-7c5fed73`。

这完成的是“指定仓库静态分析的本地 AIP 链路”，不等于第三周全部目标完成。
后续仍需论文上下文 S2→S3、模型增强（如团队需要）、正式平台 ADP/在线/MQ 验收。
