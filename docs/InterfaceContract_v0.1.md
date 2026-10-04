# ResearchMesh 接口约定 v0.1（2026-10-02 本地实现稿）

给赵、吴、张共用。本地代码已按本文实现；仍需全组确认再称为“团队冻结”，不要假定队友已同步。

## 1. 通信边界与兼容变更

Planner、数据绑定和调度在 Leader 内通过 Python 对象传递；只有 Leader ↔ Partner 使用官方 AIP SDK。`/plan` 是 Leader 的用户入口，不是 AIP Partner 接口。ADP 用于发现，不能用 AIP TaskCommand 冒充发现。

保留 `requiredCapability` 字段名以减少返工，但它的值明确是 **业务 skill**，不是 ACS/AIP 的技术 capabilities。旧 `literature_search` 等能力标签改成下面的点分名称；旧样例输入 `selection`、单独 `doi`、`paperRecords: "S1 输出"` 不再被新规划器接受。历史 outputs 不改写；旧计划必须按新格式迁移后再执行。仅 Mock 注册表兼容旧能力标签。

| taskType | requiredCapability（业务 skill） | outputSchema | items[].kind |
|---|---|---|---|
| literature_search | literature.search | paper_records.v1 | paper |
| paper_read | paper.analyze | paper_notes.v1 | paper_note |
| code_search | repository.analyze | code_records.v1 | repository |

平台实际 skillId 由吴的 ACS/ADP 注册信息确认。若不一致，由 Resolver 映射，Planner 不写 SemanticScholar/PaperQA/CodeWiki 或 Agent 地址。

## 2. Planner 任务和输入

每个任务字段：`schemaVersion, subtaskId, taskType, goal, input, constraints, outputSchema, requiredCapability, dependsOn`。前三种任务的 input 分别为：

```json
{"query": "retrieval augmented generation clinical question answering"}
```

```json
{"papers": []}
```

```json
{"papers": [], "paperNotes": [], "repositoryUrls": []}
```

规划阶段 papers 可空，但必须依赖提供论文的上游。发送前 Leader 的 `input_binding.py` 把真实结果填入数组，禁止把 S1 编号或中文说明当论文数据。显式指定论文优先；否则按上游结果顺序去重并限制篇数，不声称进行了智能代表性筛选。

统一 PaperRef：

```json
{
  "paperId": "本地稳定引用标识",
  "title": null,
  "doi": null,
  "url": "https://arxiv.org/abs/1706.03762",
  "pdfUrl": null,
  "repositoryUrls": []
}
```

`doi/url/pdfUrl` 至少有一个。paperId 缺省时由 Leader 对定位信息生成稳定哈希；它不是 AIP taskId，也不是平台 artifactId。仅有标题不能执行阅读。现在不依赖尚未搭建的 artifact 存储服务。不能获取全文时必须明确返回摘要分析或错误，不能假装已读全文。

constraints 约定：yearFrom/yearTo 为整数；maxResults/maxPapers/maxRepositories 为 1—100；executeCode 只能 false；allowRepositorySearch 决定是否可依据论文寻找仓库；requirePaperNotes=true 时要求依赖阅读任务并收到阅读结果。未给仓库地址时 Code Wrapper 可自行查找，但需要提供论文关联证据；发现结果不确定时说明，不伪造官方仓库。

复现链应为 S1 检索 → S2 阅读 → S3 静态分析。S3 可直接依赖 S2，因为 paper_note.source 必须保留可定位论文；也可依赖 S1 与 S2。仅查仓库而不需要阅读分析时，S2/S3 可在 S1 后并行。

## 3. 业务结果

外壳严格固定，不另加 status、agentId、latencyMs 等协议或日志字段：

```json
{
  "schemaVersion": "0.1", "subtaskId": "S1", "taskType": "literature_search",
  "summary": "本次检索结果摘要", "items": [], "warnings": []
}
```

- paper.data：至少 title 和可定位的 DOI/url/pdfUrl；建议保留 paperId、authors、year、abstract、repositoryUrls。无数据就返回空数组，不编造条目。
- paper_note.data：source 为 PaperRef，method/findings/limitations 为非空文本；未知写明未知；真实结果必须声明 analysisLevel=abstract 或 fulltext，并提供 evidence。
- repository.data：url、license（未知用 UNKNOWN）、relationEvidence（论文关联依据；无论文场景说明不适用），以及 evidence；建议补充 name、dependencies、reproductionDifficulty、limitations。当前只静态分析，不安装运行仓库代码。

证据格式：

```json
{"sourceId":"P1", "url":"https://arxiv.org/abs/1706.03762", "locator":"第 3 节 / 页码 / 文件路径与行号", "supports":"此处支持的具体结论"}
```

定位字段必须来自实际读取的内容。`result_contract.py` 检查结构与出处字段，但不能代替真实性核验。`isMock=true` 仅用于本地模拟；AIP 完成确认组件拒绝把此类条目当真实科研成果。

失败由 AIP TaskResult.status.state 和相应协议错误/诊断表达，不伪装成成功的空分析。部分数据缺失写 warnings，保留已验证条目。当前没有固定“召回率/完整性评分”，数量不足不自动等于协议失败；下游输入不足会明确报错。

## 4. AIP 接入（交给吴对齐）

执行器发送前去掉 requiredCapability/dependsOn，只把七个业务字段放入官方 StructuredDataItem.data，再装入 TaskCommand。TaskResult.products[].dataItems[] 返回同一 subtaskId 的业务结果。协议 taskId 每次调用单独生成，不复用 S1。

新增 `aip_lifecycle.py` 使用官方 SDK 2.2.0 类型与发送通道：accepted/working 可轮询；awaiting-completion 时校验产物；发 complete；必须收到 completed 才成功。直接 completed 在本团队的完成确认策略下会被拒绝，这不是宣称所有 AIP 场景都禁止直接完成。

本地测试覆盖协议序列、错误、超时和 SDK JSON-RPC 序列化。没有实际调用平台、没有检验证书，也没接 AMP。SDK 的 start_task 仅收文本，因此结构化发送集中使用 `_send_request` 适配点；固定版本，升级必须重新验证。

吴需要提供真实 ADP 调用示例、注册 skillId、ACS 的 Direct/RPC endpoint 选择规则、双方 AIC、证书在本机的配置方法、AMP 接入及可运行 Adapter。生产必须开启 SDK 身份绑定；测试中的关闭选项仅用于内存 HTTP 替身，不能带入真实部署。

## 5. 故障与执行记录

本地故障码包括 NO_MATCHING_AGENT、RESOLVE_TIMEOUT、RESOLVE_ERROR、INPUT_INSUFFICIENT、INVALID_RESULT、EXECUTION_TIMEOUT、EXECUTION_ERROR、DEPENDENCY_FAILED。独立成功结果保留；失败依赖下游跳过。Planner 格式修复最多一次；Agent 不自动重复 start，避免任务重复执行。

AIP 组件保留 taskId、agentAic、命令/状态事件、耗时；失败含脱敏错误，超时明确远程状态未知。自动 cancel、故障切换和动态重规划尚未实现，不要当作已完成。LocalOrchestrator 目前仍是 Mock 调度，不直接拼接到平台 Adapter，避免生成误导性的“真实互联”日志。

现有 schemas/ 四份 JSON Schema 校验外壳；skill/taskType 对应、依赖、input 以及具体结果由 Python 校验器补充，不能仅用 JSON Schema 就声称完整验收。
