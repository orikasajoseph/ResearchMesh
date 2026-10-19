# 给吴：三 Agent 梧桐平台接入准备

本交接说明以 2026-10-19 本机官方 AIP 三阶段 PASS 为基础。本轮 fetch 后，吴分支为 `origin/wu/planner-aip-integration@ada04bc`。**正式梧桐 ADP 发现、AMQP/Group MQ 消息、平台身份/进程管理均未验收**。吴负责平台通信适配，本说明不要求直接覆盖其 `aip/` 适配或建立另一套 Agent 业务实现。

## 可执行入口与运行环境

| Agent | 业务代码与 Partner factory | Python / 依赖 | Health |
| --- | --- | --- | --- |
| Literature | `agents/literature/researchmesh_literature/`、`aip_literature_partner:create_live_app` | 本机主项目 Python 3.12；`agents/literature/requirements.txt` 的 Semantic Scholar SDK、httpx、pydantic、dotenv | 本机 Partner `GET /health` |
| Reader | `agents/paper_reader/researchmesh_paper_reader/`、`aip_reader_partner:create_live_app`，Task 到达后启动 `reader_aip_worker.py` | Partner 主项目 Python 3.12；Worker **独立 Python 3.13**，PaperQA 固定 Git commit、pypdf、SentenceTransformer 缓存；不要合并进 CodeWiki venv | 本机 Partner `GET /health`，无预置 `reader_input.json` 也能启动 |
| Repository | `agents/repository/researchmesh_repository/`、`aip_repository_partner:create_live_app` | 独立 Python 3.12 venv；CodeWiki 固定 Git commit、Node/npm、共享 DeepSeek provider | 本机 Partner `GET /health` |

本机进程由 `run_aip_literature.live_partner()` 拉起 `uvicorn <factory> --factory --host 127.0.0.1 --port <临时端口>`；20 秒内轮询 `/health`，AIP RPC 路径为 `/rpc`。端口是动态分配的 loopback 端口，**没有固定平台端口合同**；平台进程管理与网络暴露方式由吴确认。本机 AIP 使用 `RESEARCHMESH_LOCAL_PARTNER_AIC`、`RESEARCHMESH_AIP_WRAPPER_ROOT`、`RESEARCHMESH_AIP_RECORD_DIR`，记录目录放在被 Git 忽略的 `outputs/`。不要将个人机器绝对路径、原始运行日志或缓存打包部署。

## 技能注册与业务边界

注册 `literature.search`、`paper.analyze`、`repository.analyze`。对应 Task 类型分别为 `literature_search`、`paper_read`、`repository_analyze`；业务输出分别为 `paper_records.v1`、`paper_notes.v1`、`code_records.v1`。Partner Task 只有七个顶层业务字段；Leader 内部的 `requiredSkill`、`dependsOn` 不发送给 Partner。`TaskCommand` / `TaskResult` 与 `complete→completed` 本机已验证；正式平台发现、AIC 身份、AMQP 消息、断线重连及跨主机超时策略待平台联调。

S1 使用 Semantic Scholar Key；遇到规定的暂时性失败可回退 OpenAlex Key。Resource Resolver 可能访问 Semantic Scholar、OpenAlex、arXiv 和 GitHub；网络出口/代理、DNS、TLS 和外部限流要在部署环境验证。S2 下载或复用经身份校验的 PDF，需允许访问论文 PDF 站点，具有本地私有缓存和写入权限；embedding 权重通过运行环境配置的缓存路径提供，不入 Git。S3 对显式 GitHub URL 做有界浅克隆并静态分析；需要安全的临时工作目录和有限网络/进程预算，不运行仓库代码。

配置名称按现有代码：`SEMANTIC_SCHOLAR_API_KEY`、`OPENALEX_API_KEY`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`；Literature 可用 `LITERATURE_SEARCH_PROVIDER`、`LITERATURE_OPENALEX_FALLBACK`、`LITERATURE_OPENALEX_TRUST_ENV`、`LITERATURE_RESOURCE_TRUST_ENV`、`LITERATURE_RESOLVE_RESOURCES`、`LITERATURE_REQUEST_TIMEOUT`、`LITERATURE_OPENALEX_TIMEOUT` 等控制 Provider/资源请求。Reader 本机适配使用 `RESEARCHMESH_READER_DYNAMIC_INPUT`、`RESEARCHMESH_READER_PYTHON`、`RESEARCHMESH_READER_ROOT`、`RESEARCHMESH_READER_ENV_FILE`、`RESEARCHMESH_READER_RUNTIME_HOME`。密钥使用部署系统的私密配置通道，不能写入镜像、日志或仓库。

## 移机准备与验证

在目标机器分别安装主项目 Python 3.12 依赖、独立 Reader Python 3.13 的 PaperQA 依赖、Repository Python 3.12 的 CodeWiki/Node 依赖；准备合法的模型缓存路径、PDF/仓库临时目录、文件权限及外部网络出口。Reader 默认测试入口会寻找仓库旁的 `paper-qa-clean/.venv`；跨机器部署须通过可配置 `--reader-python`/`RESEARCHMESH_READER_PYTHON` 明确指定，不依赖个人电脑上的外部目录。Repository 本机默认 `.venv` 也须由目标环境重新建立。先执行离线独立测试和 `run_aip_three_agent.py --health`（仅本机健康验收），随后与平台团队验证 ADP/AMQP；**不要把本机 `--execute` 成功直接记作正式平台成功**。

## 与吴现有代码的最小接缝

吴的 `aip/wu_delivery/` 负责技能映射、ADP candidate 解析、平台任务适配；其 `partner.py` 当前默认是本机 transport-only 演示，`create_local_app()` 默认后端预算 20 秒且不承担平台注册。本机 QLoRA S2/S3 实际耗时超过该默认值，不能把演示 Partner 原样用作真实 Agent 后端。建议由吴在平台边缘保留其身份验证、发现与通信层，将收到的业务七字段 Task 映射到张现有 `aip_literature_partner.py`、`aip_reader_partner.py`、`aip_repository_partner.py` 所使用的同一业务执行函数/Wrapper；`TaskResult` 原样保留三种 schema 与 evidence ID。平台超时、取消、重试和重复消息处理要与三个 Agent 的任务隔离规则核对，禁止重复运行收费 S2/S3。

吴分支与当前 `agents/paper_reader/` 内容一致；`agents/repository/` 有 README、`agent.py`、`enrichment.py`、`schemas.py` 四个内容差异，后者包含已验收的 `paperNotes`、双侧 evidence 关联，不应被旧副本覆盖。赵的新 Repository 静态 Worker 是并行本机执行模式，也不应替代当前真实 S3 enrichment。吴新增的 AMP heartbeat 配置和 Group Literature 适配属于平台侧文件；与当前三个 Agent 目录无直接同路径修改。正式平台接入应通过接口对接，避免复制 Wrapper、Resolver 或 Planner。

本机 QLoRA 验收总耗时 206.96 秒：S1 8.71 秒、S2 117.54 秒、S3 72.47 秒。平台 deadline 应按真实延迟设计，Reader PaperQA 的实际收费请求次数/token/费用尚未被完整记录。部署环境仍需验证逐 Agent 健康、技能发现、任务送达、结果回传、失败阻断与脱敏日志。
