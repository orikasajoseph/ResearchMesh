# ResearchMesh Literature Agent AIP 联调交接

## 最终验收状态

- Literature Agent v0.1：**DONE / FROZEN**。
- Semantic Scholar real API：**PASS**。
- DeepSeek LLM Provider v0.1：**PASS**，独立验证且 Literature v0.1 不依赖它。
- AIP integration：待团队 AIP 负责人完成真实联调。

## 已冻结边界

- skill: `literature.search`
- input schema: `literature_search.v0.1`
- output schema: `paper_records.v1`
- Python entry point:
  `agents.literature.researchmesh_literature.LiteratureAgent`
- method: `async literature_search(request: LiteratureSearchRequest) -> PaperRecordsResponse`

`literature.search` 是 ResearchMesh 业务 skill，应映射到 ACS skills，不是 AIP
`capabilities` 技术字段。当前 Wrapper 不依赖 AIP SDK。

## 最小调用

```python
from agents.literature.researchmesh_literature import (
    LiteratureAgent,
    LiteratureSearchRequest,
)

request = LiteratureSearchRequest.model_validate(business_json)
response = await LiteratureAgent().literature_search(request)
paper_records = response.model_dump(mode="json")
```

输入完整字段为 `schemaVersion`、`requestId`、`query`、`filters`、`limit`。
`filters` 支持 `year`、`publicationDateOrYear`、`publicationTypes`、`venues`、
`fieldsOfStudy`、`openAccessOnly`、`minCitationCount`。

输出完整字段为 `schemaVersion`、`requestId`、`status`、`papers`、`total`、
`provider`、`metrics`、`error`。每篇 paper 包含 `paperId`、`title`、
`authors`、`year`、`abstract`、`doi`、`url`、`pdfUrl`、
`repositoryUrls`、`citationCount`。`pdfUrl` 只映射 Semantic Scholar
`openAccessPdf.url`；API 未返回时保持 `null`，不会自行构造。

`requiredCapability`、`dependsOn` 是 Planner 内部字段，不应发送给 Partner；AIP
的 `taskId`、`sessionId`、`senderId`、状态也不应塞入 Literature Agent 业务
Schema。

## Adapter 职责

```text
ResearchMesh business JSON
  -> StructuredDataItem
  -> TaskCommand
  -> LiteratureAgent.literature_search()
  -> paper_records.v1
  -> Product / StructuredDataItem
  -> TaskResult
```

建议由 AIP 层映射状态：有效执行结果进入 Product；输入非法映射为 rejected；
缺少必需输入映射为 awaiting-input；429、timeout、认证或网络执行异常映射为
failed。最终状态名和 SDK 字段以 AIP 负责人使用的官方 SDK 为准，不修改
Literature Agent Schema。

当前 Agent 的规范化业务结果包括：成功论文、`NO_RESULTS`、`INVALID_REQUEST`、
`RATE_LIMITED` 和 `PROVIDER_UNAVAILABLE`。其中 timeout、认证/权限及网络错误会
按当前 SDK 异常类别落入相应规范化失败，不向 AIP 暴露 API Key 或请求头。

## 联调样例与运行

- 成功输入：`agents/literature/examples/literature_success_input.json`
- 成功输出：`agents/literature/examples/literature_success_output.json`
- 空结果输入：`agents/literature/examples/literature_empty_input.json`
- 空结果输出：`agents/literature/examples/literature_empty_output.json`

```powershell
python -m pip install -r agents/literature/requirements.txt
python agents/literature/scripts/live_test_literature.py
```

根目录本地 `.env` 只需配置 `SEMANTIC_SCHOLAR_API_KEY`；不得提交该文件。

最近一次真实 smoke test：`AlphaEvolve`、`maxResults=5`、返回 5 篇，
`latencyMs=2750.866`、`cacheHit=false`、`retryCount=0`、
`retryEvents=[]`；第一篇有 DOI，`pdfUrl=null`。这是单次 smoke test，不代表
正式性能统计。随机查询的零结果路径也已验证。

联调成功标准不是 HTTP 200 或本地 Mock，而是：AIP Client 发出 TaskCommand，
Partner 调用真实 Literature Agent 和 Semantic Scholar，最终客户端从
TaskResult Product 中拿到真实 `paper_records.v1`。
