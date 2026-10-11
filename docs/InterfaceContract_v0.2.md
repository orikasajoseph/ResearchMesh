# ResearchMesh 业务接口 v0.2

状态：赵开发分支实现稿，待张、吴确认与对接；不是 AIP 协议版本，不代表 ACS 已完成注册。本文优先于历史 v0.1 文档。

## 任务与技能

| taskType | requiredSkill | outputSchema | items[].kind |
|---|---|---|---|
| literature_search | literature.search | paper_records.v1 | paper |
| paper_read | paper.analyze | paper_notes.v1 | paper_note |
| repository_analyze | repository.analyze | code_records.v1 | repository |

requiredSkill 是业务技能 ID，用于发现匹配的 Agent；不表示 ACS/AIP 技术 capabilities，不含工具、供应商或服务地址。正式 ACS 注册由吴实施，当前本地测试仍用 Mock 注册表。

Plan 字段：schemaVersion="0.2"、userQuery、needsClarification、clarificationQuestion、tasks。需要澄清时 tasks=[]。仅要分析仓库但缺少地址时，应追问 GitHub URL。

每个内部任务字段：schemaVersion="0.2"、subtaskId、taskType、goal、input、constraints、outputSchema、requiredSkill、dependsOn。新入口拒绝 requiredCapability 和 code_search；旧格式必须显式迁移。

## 输入与执行条件

文献检索 input 为 `{ "query": "研究关键词" }`；阅读 input 为 `{ "papers": [] }`；仓库分析 input 为 `{ "papers": [], "paperNotes": [], "repositoryUrls": [] }`。

PaperRef 保留 paperId、title、doi、url、pdfUrl、repositoryUrls；论文必须有 DOI、url 或 pdfUrl 中至少一个定位依据。论文来源 URL 允许 HTTP(S)，仓库执行地址首期限定为 `https://github.com/owner/repository` 根地址（可带 .git 或末尾斜杠），不接受凭据、查询参数、分支页面或其他主机。

仓库任务必须显式设置 executeCode=false、allowRepositorySearch=false。可选 maxRepositories 限制数量；requirePaperNotes=true 时还需实际阅读结果，且无显式笔记时直接依赖阅读任务。

规划阶段有上游依赖时，repositoryUrls 可以暂空。此时 goal 说明有条件执行：仅在上游实际提供 URL 后分析，否则提示缺少地址。发送前必须完成真实数据绑定、校验地址；缺少地址报 INPUT_INSUFFICIENT，不调用仓库 Agent，已经成功的检索/阅读结果保留。

显式 repositoryUrls 优先，不因上游其他地址而扩充或替换；未显式提供时从已绑定 PaperRef.repositoryUrls 提取，去重并按 maxRepositories 截取。这只是传递已取得的地址，不是自动搜索。URL 格式有效不代表仓库存在或与论文匹配，来源关系仍需证据。

Planner 输出另有来源检查：计划内仓库地址须能在用户问题中找到（允许规范化 .git、末尾斜杠和大小写），包括 PaperRef 和阅读笔记来源中的地址。上游真实结果中的新地址由执行阶段绑定，不能让未调用检索工具的 Planner 自行填入。

独立任务可并行，依赖任务等待上游。失败依赖下游跳过；自动失败重试、重规划和故障切换仍未实现。论文精读是全文还是摘要，必须由结果声明。

## 结果与 AIP 边界

业务结果字段固定为 schemaVersion="0.2"、subtaskId、taskType、summary、items、warnings。subtaskId/taskType/schemaVersion 必须匹配请求，不添加业务 status。具体产物仍使用 v1 outputSchema，本次仅调整信封版本及命名。

- paper.data：标题及可定位 DOI/url/pdfUrl，可附 repositoryUrls 等已验证信息。
- paper_note.data：source 为 PaperRef，method/findings/limitations 非空；真实阅读必须有 analysisLevel=abstract/fulltext 和 evidence。
- repository.data：url、license（未知写 UNKNOWN）、relationEvidence、evidence；其他结构统计、依赖和复现难度按实际证据提供，未知不编造。代码结构统计不等于完整复现评估。

evidence 条目含 sourceId、url、locator、supports，定位信息来自实际读取内容。Wrapper 读取本地文件并提取结果，或给出 Leader 可访问的产物 URI，不能只提供另一台电脑的磁盘路径。当前没有统一远程 artifact 存储服务。

Leader 内部模块通过对象传递数据；requiredSkill、dependsOn 留在 Leader。发送时仅提取七个 BusinessTask 字段，经官方 StructuredDataItem 放入 TaskCommand；Partner 经 TaskResult 返回产品和状态。subtaskId 不等于协议 taskId。CodeWiki MCP 是 Partner 内部调用工具的方式，不替代 Leader 与 Partner 之间的 AIP。

## 兼容范围

migrate_plan.py 读取 v0.1 完整计划，保存新的 v0.2 文件和迁移说明，不改原记录。新旧技能字段冲突时拒绝。旧 code_search 必须已明确提供 repositoryUrls；否则要求补充地址，不能通过改名获得执行能力。已有地址的旧任务收紧为禁止搜索、禁止执行，并明确记录目标调整，迁移后须人工核对。

run_wu_aip.py 是独立传输测试：只选择无依赖的文献任务，内部转换为 v0.2，发给吴当前交付包的业务信封仍为 v0.1。它检查旧版回包，再以当前结果模型校验文献内容。日志同时记录两个版本，永远不将传输替身标成科研成功。它不兼容旧阅读/代码业务，也不是生产自动降级策略。

版本迁移、业务校验、Mock/AIP 单测不能替代真实科研与平台联调。真实 ACS/ADP、Wrapper、证书和 AMP 待组员代码与配置齐备后对接。
