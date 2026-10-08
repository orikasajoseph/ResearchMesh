# ResearchMesh LLM Provider v0.1

ResearchMesh 公共 OpenAI-compatible LLM Provider，当前通过 DeepSeek 真实 API
验收。它与 Literature Agent 相互独立。

## 安装与调用

```powershell
python -m pip install -r common/llm/requirements.txt
```

```python
from common.llm.researchmesh_llm import LLMProvider

provider = LLMProvider()
response = provider.chat([
    {"role": "user", "content": "请用一句话解释强化学习。"}
])

data = provider.generate_json([
    {"role": "user", "content": "只用 JSON 返回 AlphaEvolve 的检索关键词。"}
])
```

配置统一从仓库根目录 `.env` 加载：`DEEPSEEK_API_KEY`、
`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`LLM_REQUEST_TIMEOUT`、
`LLM_MAX_RETRIES`。模型可通过环境变量替换。

```powershell
python -m unittest common.llm.tests.test_researchmesh_llm
python common/llm/scripts/live_test_deepseek.py
```

live test 没有 Key 时只提示 `DEEPSEEK_API_KEY is not configured` 并正常退出。
