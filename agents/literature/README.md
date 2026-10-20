# ResearchMesh Literature Agent v0.1

Semantic Scholar 检索 Wrapper。业务协议已冻结为：

- skill: `literature.search`
- input: `literature_search.v0.1`
- output: `paper_records.v1`

该组件是异步 Python 调用，不依赖 AIP SDK，也不调用 LLM。

## 当前状态

- Literature Agent v0.1：**DONE / FROZEN**。
- Semantic Scholar real API：**PASS**。`AlphaEvolve` 返回 5 篇，
  `latencyMs=2750.866`，`retryCount=0`，第一篇 DOI 存在；API 未提供
  `openAccessPdf` 时 `pdfUrl=null`。
- 随机低相关查询：已验证零结果 warning，不编造论文。
- DeepSeek LLM Provider v0.1：**PASS**，但本 Agent 当前不依赖 LLM。
- AIP integration：待团队 AIP 负责人联调。
- Planner integration：待 Planner 负责人联调。

v0.1 的 skill、输入、输出和行为边界已冻结；新增检索能力统一进入 v0.2/backlog。

## 安装

在共享仓库根目录执行：

```powershell
python -m pip install -r agents/literature/requirements.txt
```

依赖中的 `semanticscholar==0.12.0` 来自 PyPI，因此不需要原
`semanticscholar-master` 源码。

## 调用

```python
import asyncio

from agents.literature.researchmesh_literature import (
    LiteratureAgent,
    LiteratureSearchRequest,
)


async def main():
    request = LiteratureSearchRequest(
        requestId="literature-demo-001",
        query="AlphaEvolve",
        limit=5,
    )
    response = await LiteratureAgent().literature_search(request)
    print(response.model_dump_json(indent=2))


asyncio.run(main())
```

`LiteratureAgent.literature_search(request)` 接收
`LiteratureSearchRequest`，异步返回 `PaperRecordsResponse`。需要字典时使用
`response.model_dump(mode="json")`。

## 配置与验证

配置统一从仓库根目录 `.env` 加载。真实 API 只要求
`SEMANTIC_SCHOLAR_API_KEY`；其余 timeout、rate limit、retry、backoff 和缓存项见
根目录 `.env.example`。

```powershell
python -m unittest agents.literature.tests.test_researchmesh_literature
python agents/literature/scripts/live_test_literature.py
```

## Optional paper resource verification

The local Literature live entry enables bounded resource verification by default;
set `LITERATURE_RESOLVE_RESOURCES=0` to use the original search-only behavior.
OpenAlex can bypass inherited system proxies with
`LITERATURE_OPENALEX_TRUST_ENV=0` (PowerShell: `$env:LITERATURE_OPENALEX_TRUST_ENV='0'`).
The default is `1`, preserving existing proxy behavior. TLS verification remains
enabled, and this setting does not affect Semantic Scholar or resource lookups.
It does not bypass OpenAlex HTTP 429 or exhausted API quota.
Resource verification has its own proxy setting:
`LITERATURE_RESOURCE_TRUST_ENV=0` uses a direct connection for arXiv and GitHub
without changing OpenAlex or Semantic Scholar. Its default is `1`. TLS
verification and the request budget remain enabled. Each actual resource
request records a safe purpose, host, path category, status or error class,
network phase, duration, proxy observation, and remaining budget in
`resourceResolution.requestEvents`; no credentials or query strings are stored.
Direct `LiteratureAgentConfig` use leaves this feature disabled unless
`resolve_resources=True` is passed.

The resolver checks the Semantic Scholar `openAccessPdf` URL first, then a
title-matched arXiv page and PDF, then a DOI- and title-matched OpenAlex OA
location. It accepts a GitHub repository only when an accessible README cites
the same arXiv ID or DOI and paper title. Explicit paper-page and abstract
links take priority over bounded GitHub repository search. A matching repository
name alone is never accepted.

At most three search results are enriched, with 12 additional HTTP requests
across the search and a five-second timeout per request. No retries are made
after a resource-service rate limit. Results use the existing `pdfUrl` and
`repositoryUrls` fields; sources, verification status, and failure reasons are
kept in `resourceResolution` backend diagnostics. Missing resources remain
missing. The `paper_records.v1` response shape is unchanged.

第二条是显式 live smoke test。没有 Key 时只提示
`SEMANTIC_SCHOLAR_API_KEY is not configured` 并正常退出，不回退到匿名 API。
