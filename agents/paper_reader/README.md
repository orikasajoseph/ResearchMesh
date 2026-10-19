# ResearchMesh Paper Reader Agent v0.1

基于 PaperQA commit `57e89f7223b0960d5ee5ea048c69e3c47e088572` 的单篇论文
全文分析业务 Wrapper。

## 业务边界

- skill：`paper.analyze`
- 目标 Task：`schemaVersion="0.2"`、`taskType="paper_read"`
- output schema：`paper_notes.v1`
- Partner payload 不包含 `requiredSkill` 或 `dependsOn`
- 结果外壳没有 `status`；AIP 状态由外部 Adapter 管理
- 不导入当前 main 的共享 v0.1 `common.models`

PaperRef 严格使用：`paperId/title/doi/url/pdfUrl/repositoryUrls`。当前 v0.1 最多处理
一篇论文。没有 `pdfUrl` 时不会根据 DOI、URL 或标题拼造全文地址，而是返回
`FULL_TEXT_UNAVAILABLE`。

## PaperQA 配置

- embedding：`st-multi-qa-MiniLM-L6-cos-v1`
- generation / summary：`.env` 中配置的 DeepSeek
- `settings.answer.evidence_k = 6`
- `settings.answer.max_concurrent_requests = 4`
- Provider 初始化时加载一次 embedding model，后续任务复用该内存实例
- 每篇论文新建 `Docs`，只执行一次综合 `aquery`

证据以 `session.contexts` 为主。若回答残留 `pqac-*`，业务结果不会暴露这些 ID，
但会保留 Context evidence，并增加 `PAPERQA_CITATION_FORMATTING_INCOMPLETE`。

## Python 调用

```python
from agents.paper_reader.researchmesh_paper_reader import PaperReaderAgent

agent = PaperReaderAgent()  # 长期复用该实例
result = await agent.run(task_payload)
payload = result.model_dump(mode="json")
```

该入口只处理业务 JSON，不构造 AIP `TaskCommand`、`TaskResult` 或状态生命周期。

## 测试

```powershell
python -m unittest agents.paper_reader.tests.test_researchmesh_paper_reader -v
python agents/paper_reader/scripts/live_test_paper_reader.py
```

第一条为离线单测，不调用 DeepSeek。第二条使用仓库旁的 PaperQA 真实测试 PDF，启动
本地 HTTP 文件服务，并在同一 Agent 实例中执行 cold/warm 两次完整全文分析。
