# 给张、吴的对齐回复（待发送）

收到，我已核对你的交接和GitHub最新分支。明确：这是三Agent已验收完整快照，不是要求重新开发或重新提交Agent，也不会整体合并到main。

1. 集成基线

本轮集成起点是 `zhao/planner-integration@d4bb9d8485f1e52873074e2a255d02027a246218`，不再是 `4a39ba2`。后续 `d4bb9d8` 已选择性纳入Repository增强；本轮统一业务入口增量已形成独立本地提交 `e3586d5`。对外发送本回复前仍须用远端分支核实该提交已可拉取，不能把本地commit直接写成队友已经拿到。

2. Repository整合方式

保留张的同一套Wrapper/CodeWiki/RepositoryEnricher和团队共享LLM，不用静态Worker覆盖增强。

- `run_aip_research.py`：新问题的唯一通用编排入口。
- `run_aip_repository.py`：统一单阶段预检/运行入口。
- `aip_repository_partner.py`：保留一个本机AIP Partner，以显式配置选择隔离Worker。
- 默认static：真实静态分析，零模型；显式enhanced：静态事实加DeepSeek论文代码双侧证据增强，需单独允许计费。

静态不是Mock，增强也不是仅供测试；二者共用Task v0.2、七业务字段和code_records.v1，不新增模式协议字段。增强失败保留静态，分别报告businessReady和enhancementSucceeded，不声称已经复现。

3. 同路径改动与分工建议

13个业务同路径文件中，8个Repository Python文件现已与你22863c0完全一致，直接保留，不重新改写。剩余5项是Repository README、aip_business_partner.py、aip_repository_partner.py、run_aip_literature.py、run_aip_repository.py；另有根README需要归并。

建议由我负责逐文件整合，你复核Agent实现、增强与证据语义，吴复核ACS/ADP/AMQP和身份/群组边界。确认后再准备独立integration分支/PR，不整枝覆盖，也不建设第二个Planner或生产Resolver。

我已继续补齐OpenAlex Provider、Resource Resolver配置和动态Reader Partner，保留已有PDF来源校验；新增联网能力默认关闭，显式配置才启用。Reader业务包沿用你的实现。动态预检不会下载PDF，缺少来源不冒充全文。

供吴调用的稳定入口为 `business_execution.execute_business_task(task, skill=..., backend=..., allow_execution=True, ...)`，默认拒绝执行，返回六字段Result，不掺协议status。Literature复用make_literature_executor，Reader/Repository已有可直接创建的动态业务工厂；Reader另提供复用长期Agent/embedding的执行器。超时、取消不自动重做收费操作，持久化平台去重仍由吴负责。详细签名与示例见docs/delivery/platform_business_entry_v1.md。

通用入口显式选择增强用 `--repository-analysis-mode enhanced --execute`；平台工厂的analysis_mode与业务入口的repository_analysis_mode也须一致配置为enhanced，不能由goal文字暗中触发计费。

当前工作区为684项免费测试、5个模拟场景和本机HTTP检查通过；其中不含Group工具的业务提交隔离副本另有652项通过。此前另已完成一次LoRA新问题→真实Planner→检索→阅读→仓库静态分析→报告，记录aip-research-20261020-135700-393904。这条真实静态链、你的QLoRA真实定向链、本轮新增能力离线验收、正式梧桐验收分别记录。本轮没有重复收费测试；OpenAlex实际联网和新平台入口线上接线仍待验收。发送给队友时以远端可拉取commit为准。

另请平台接缝注意：Wrapper原生repository_record要经过现有repository_adapter转换为Leader标准repository；requiredSkill/dependsOn不传Partner，PaperRef仍为六字段，evidence.supports仍是具体结论字符串，避免各自新造一套。
