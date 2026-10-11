# 吴：ResearchMesh AIP / Literature v0.1 首轮整合（待 PR 审核）

## 变动范围与现状

- 本次只 **新增** `aip/__init__.py`、`aip/wu_delivery/`、`aip/literature_bridge.py`、`aip/local_literature_app.py`、`aip/demo_literature_local.py`、`tests/test_aip_literature_bridge.py` 及本文档；**不修改**赵的 Planner/Schema，也不改张的 Literature Wrapper。
- `aip/wu_delivery/` 是已验证旧 AIP 核心模块的 **8 个 Python 源码**（不是整包旧项目）。仅 `execution.py` 增加了对安全 `BackendFailure` 错误码的透传。
- Task 外层目前仍是主分支 **v0.1**；`literature_search -> literature.search -> paper_records.v1`，不宣称 v0.2 已合并。
- 真实平台 AIC、证书、ADP、MQ 及 AMP **均未在本次验证**。本地示例关闭身份绑定，禁止公网部署。

## 映射行为

- Planner 的 `requiredCapability`/`dependsOn` 不放入 AIP TaskCommand 的七字段业务 JSON。
- `input.query` -> LiteratureSearchRequest.query；`constraints.maxResults` -> limit（默认 5，1–100）；`yearFrom/yearTo` -> filters.year；`subtaskId` -> requestId（仅用于本地业务关联，AIP taskId 独立）。
- Literature 返回的每个可定位论文成为 `items[{kind:'paper',data:...}]`；AIP `status.state` 仍承载协议状态。
- Provider `NO_RESULTS` 映射为执行完成的空 `items` 和 `warnings:['NO_RESULTS']`。`RATE_LIMITED` 等规范化失败返回安全代码，AIP 应进入 failed，避免泄漏原始错误内容。
- 当前 Literature Wrapper 可能错误地将 arXiv/PDF 页面放入 `repositoryUrls`。此层临时只保留 GitHub 仓库形态链接并记录 warning；仍建议张修改源头及增加回归测试。
- 成功但缺少标题/定位信息时拒绝认作已验证的真实结果。

## 首先离线测试（项目根目录，Python >=3.11）

```powershell
python -m unittest tests.test_aip_literature_bridge
```

此测试不需要联网、不消耗 API 额度、也不需要 ACPs SDK；但需要项目 `pydantic` 等基础依赖。

## 真实 AIP 本地调用（在已装 ACPs SDK 2.2.0 + Semantic Scholar 依赖的项目 .venv 中）

配置根目录 `.env` 的 `SEMANTIC_SCHOLAR_API_KEY`（不要上传、不要粘贴到聊天）。

```powershell
python -m pip install -r agents/literature/requirements.txt
python -m aip.demo_literature_local
```

单命令示例在进程内使用 `httpx.ASGITransport` 路由官方 SDK AIP RPC，没有公网端口、没有 MQ/ADP。它是真实业务 API 调用，不是平台互联验收。要求最后看到 `protocolCompleted: true` 且 `businessReady: true` 和非空 `items`；若 `NO_RESULTS` 则以警告为准，不把它称为检索成功找到论文。

可选：启动供本机工具调用的独立 Partner（另一个终端）：

```powershell
python -m uvicorn aip.local_literature_app:app --host 127.0.0.1 --port 8011
```

注意：不要上传 `.env`、密钥、缓存、日志。本次用 `git status` 检查，并先与赵确认 `aip/wu_delivery` 的放置位置再 PR。

## 旧版 `run_wu_aip.py` 的处理

原入口默认指向仓库外目录；**本次不修改该文件**。本地旧传输测试可以显式传入 `--wu-root .\aip` 和 `--plan <保存的真实计划路径>`。该脚本只认旧版 v0.1 并仅验证 transport-only，不能拿它证明真实科研执行。
