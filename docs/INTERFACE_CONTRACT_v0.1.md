# ResearchMesh Interface Contract v0.1

本文只汇总当前已确认的团队边界，不扩展 Agent 能力或替代 AIP 官方协议。

## Skills

| taskType | requiredCapability | output schema | 状态 |
|---|---|---|---|
| `literature_search` | `literature.search` | `paper_records.v1` | 已实现并通过真实 API 验收 |
| `paper_read` | `paper.analyze` | `paper_notes.v1` | 技能名已确认，实现待对应成员接入 |
| `code_search` | `repository.analyze` | `code_records.v1` | 技能名已确认，实现待对应成员接入 |

`requiredCapability` 尚未正式改名，继续保留。它的值映射 ACS skills，不等于
AIP `capabilities` 技术字段。若平台 `skillId` 不同，由 Resolver/Adapter 映射。

## Task v0.1

ResearchMesh 业务任务字段：

```json
{
  "schemaVersion": "0.1",
  "subtaskId": "S1",
  "taskType": "literature_search",
  "goal": "检索 AlphaEvolve 相关论文",
  "input": {"query": "AlphaEvolve"},
  "constraints": {"maxResults": 5},
  "outputSchema": "paper_records.v1"
}
```

Planner 内部任务在上述字段外增加：

```json
{
  "requiredCapability": "literature.search",
  "dependsOn": []
}
```

`requiredCapability` 和 `dependsOn` 属于 Planner/Resolver 内部字段，不发给
Partner。AIP 的 `taskId`、`sessionId`、发送方与状态字段由 Adapter/SDK 承载，
不写入 ResearchMesh 业务 Task。

Literature Wrapper 的直接业务输入是已冻结的 `literature_search.v0.1`，完整
样例见 `agents/literature/examples/literature_success_input.json`。Planner 或 AIP
Adapter 负责将 Task 的 `input`/`constraints` 映射为该请求，不改变其 Schema。

## Result v0.1

共享业务结果外壳为：

```json
{
  "schemaVersion": "0.1",
  "subtaskId": "S1",
  "taskType": "literature_search",
  "summary": "检索完成",
  "items": [],
  "warnings": []
}
```

AIP 协议状态由 `TaskResult` 表达，不向该业务外壳增加 `status`。Literature
Wrapper 本身返回冻结的 `paper_records.v1`；Adapter 负责封装到 Product /
StructuredDataItem 或团队 Result 外壳，不能改写论文事实字段。

## PaperRef

```json
{
  "paperId": "stable-paper-id",
  "title": null,
  "doi": null,
  "url": "https://arxiv.org/abs/1706.03762",
  "pdfUrl": null,
  "repositoryUrls": []
}
```

`doi`、`url`、`pdfUrl` 至少一个可定位字段存在。`paperId` 不是 AIP taskId 或
artifactId。Literature Agent 的 `pdfUrl` 只来自 Semantic Scholar
`openAccessPdf.url`，缺失时保持 `null`。

## Evidence

```json
{
  "sourceId": "P1",
  "url": "https://arxiv.org/abs/1706.03762",
  "locator": "第 3 节 / 页码 / 文件路径与行号",
  "supports": "该出处支持的具体结论"
}
```

四个字段固定为 `sourceId`、`url`、`locator`、`supports`。定位和结论必须来自
实际读取内容；结构校验不能替代真实性核验。
