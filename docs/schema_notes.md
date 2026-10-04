# 数据模型与 Schema 使用说明

统一来源为 `common/models.py`。不要在其他模块另写一套字段或业务状态。

| 文件 | 适用位置 |
|---|---|
| `schemas/plan.schema.json` | Planner 的完整内部计划 |
| `schemas/task.schema.json` | 当前 v0.2 内部子任务，包含 requiredSkill、dependsOn |
| `schemas/business_task.schema.json` | 发给执行适配器的业务内容，不包含上述内部字段 |
| `schemas/result.schema.json` | Agent 结果的业务外壳，不包含 status |

这些文件都是 JSON Schema Draft 2020-12。它们不是 AIP 的 TaskCommand/TaskResult Schema，也不能代替官方 SDK。
现有 `outputSchema` 名称是团队输出约定标识；并不表示已经为各类 `items[].data` 定义了独立、完整的领域 Schema。

## 当前能校验什么

版本必须为 0.1；任务类型为三种已约定类型；顶层未知字段被拒绝；字符串不得为空白；研究问题最长 6000 字符。
Pydantic 不把字符串 `"false"` 偷偷转换成布尔值。需要澄清时 tasks 必须为空且提供追问，否则至少一个任务且追问为 null。

JSON Schema 校验结构；代码还会校验唯一任务编号、无重复依赖、依赖只能引用前面已定义任务。
结果与请求的 subtaskId/taskType 必须匹配，这部分由 `result_contract.py` 校验，不靠单个结果的 Schema。
Schema 本身不能保证科研结论真实或 API 返回的论文来源可靠。

`input`、`constraints`、`items[].data` 目前保留可扩展对象结构，以免擅自替张决定真实 Agent 输入。
后续共同确定 Semantic Scholar、PDF 阅读、代码检索的必填业务字段，再收紧领域校验。
字段里不要放 API Key、令牌或其他认证信息。

## 修改后的验收

先修改唯一来源 `common/models.py`，再运行：

```powershell
python -X utf8 export_schemas.py
python -X utf8 -m unittest discover -s tests -v
```

任务和结果通过 `BusinessTask.model_validate(...)`、`ResearchResult.model_validate(...)` 转换校验。
向伙伴发送之前必须由真实 AIP 适配器封装；当前工作不涉及该适配器。

参考：[Pydantic 模型](https://docs.pydantic.dev/latest/concepts/models/)、[JSON Schema 导出](https://docs.pydantic.dev/latest/concepts/json_schema/)。
