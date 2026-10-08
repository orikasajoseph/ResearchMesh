# ResearchMesh Planner 联调交接

## 最终验收状态

- Literature Agent v0.1：**DONE / FROZEN**。
- Semantic Scholar real API：**PASS**。最新 `AlphaEvolve` 回归返回 5 篇，
  `latencyMs=2750.866`，`retryCount=0`；随机查询零结果路径通过。
- DeepSeek LLM Provider v0.1：**PASS**，迁移后真实联网验证通过。
- Planner integration：待 Planner 负责人完成真实联调。

上述状态同步不改变既有 `literature.search`、`literature_search.v0.1` 或
`paper_records.v1` 协议。

## 已确认技能

- `literature.search`：已实现，真实 Semantic Scholar API 已验收。
- `paper.analyze`：接口名已确认，实现由其他成员接入。
- `repository.analyze`：接口名已确认，实现由其他成员接入。

字段名 `requiredCapability` 当前保留，其值映射 ACS skills；它不等同于 AIP
`capabilities`。

## Literature Agent

```python
from agents.literature.researchmesh_literature import (
    LiteratureAgent,
    LiteratureSearchRequest,
)

request = LiteratureSearchRequest(
    requestId="planner-001",
    query="AlphaEvolve",
    limit=5,
)
response = await LiteratureAgent().literature_search(request)
result = response.model_dump(mode="json")
```

输入为 `literature_search.v0.1`，输出为 `paper_records.v1`。真实样例：

- `agents/literature/examples/literature_success_input.json`
- `agents/literature/examples/literature_success_output.json`
- `agents/literature/examples/literature_empty_input.json`
- `agents/literature/examples/literature_empty_output.json`

Agent 是纯检索组件，不执行查询改写、LLM rerank、论文阅读或仓库分析。

## 公共 LLM Provider

```python
from common.llm.researchmesh_llm import LLMProvider

llm = LLMProvider()
chat = llm.chat([
    {"role": "system", "content": "You are a research assistant."},
    {"role": "user", "content": "请用一句话解释什么是强化学习。"},
])

plan_data = llm.generate_json([
    {"role": "user", "content": "只输出 AlphaEvolve 调研任务的 JSON。"}
])
```

v0.1 只提供 `chat` 和 `generate_json`。DeepSeek 真实 chat 与 JSON 解析已验收；
Literature Agent 不依赖该 Provider。手动 live test：

```powershell
python common/llm/scripts/live_test_deepseek.py
```

脚本运行后将样例写入 `common/llm/examples/`。

## 环境变量

- `SEMANTIC_SCHOLAR_API_KEY`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `LITERATURE_REQUEST_TIMEOUT`
- `LITERATURE_RATE_LIMIT_SECONDS`
- `LITERATURE_MAX_RETRIES`
- `LITERATURE_BACKOFF_INITIAL_SECONDS`
- `LITERATURE_BACKOFF_MAX_SECONDS`
- `LITERATURE_CACHE_TTL_SECONDS`
- `LITERATURE_CACHE_MAX_ENTRIES`
- `LLM_REQUEST_TIMEOUT`
- `LLM_MAX_RETRIES`

所有值从共享仓库根目录本地 `.env` 加载，仓库只提交无凭据的
`.env.example`。
