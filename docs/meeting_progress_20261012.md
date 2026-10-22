# ResearchMesh：赵的前两周进度与明日对齐清单

核对日期：2026-10-12。依据本机第一周任务分配表、第二周任务规划逐项复核；“已完成”区分代码、离线测试、真实本机调用和真实平台验收。没有仅凭队友选型链接或 Mock 判完成。

Git交付补充：以下“未提交/未推送”是对应开发时点的历史记录。本次已整理赵分支交付说明docs/GitHub_sync_20261012.md，实际上传状态以远端zhao/planner-integration提交为准；不是main或全组协议已冻结。

## 后续独立收尾更新（本机未提交）

已新增 run_aip_research.py 单入口（真实Planner可选/已有计划→本机AIP检索→来源核验→本机AIP单篇阅读→报告）、阶段保存与防付费重放、脱敏摘要导出和Git候选只读检查。默认不执行收费或网络任务；单入口仅离线替身验收，尚未重新做付费整链。最新425项自动测试、5模拟场景、本地HTTP通过，新增21项。下文404项是上一轮历史数量。运行与换机说明见 docs/independent_completion_20261012.md。

README与Reader文档已更正“收费阅读尚未执行”的过时描述；真实单篇Reader已有成功记录。没有新增平台、Repository验收、commit或push。当前并非“所有能做的工作都完成”：仍需要你人工验收、明确授权后的整链实测、队友交付和Git同步。

## 一句话结论

赵的第一周基础工作已基本完成。第二周已实现真实 Planner、真实检索和全文阅读，并分别通过官方本机 AIP；但还没有正式 ADP 平台发现、S3 仓库 Agent 和包含平台的单入口全链验收，因此第二周不能整体划为完成。

## 一、第一周赵的任务

| 任务表要求 | 目前结论 | 可查看的交付 |
|---|---|---|
| 总体架构 | 已完成基础架构；Leader内部与跨Agent通信边界明确 | docs/architecture.svg（历史Mock图）、新接入说明、Planner/Resolver/调度/汇总代码 |
| 最小Planner | 已完成，后来已接DeepSeek、升级业务v0.2 | planner.py、run_planner.py、live_api.py |
| 科研问题拆成结构化任务 | 已完成；当前仓库任务名为repository_analyze，不是旧code_search | common/models.py、schemas、planner_contract.py |
| 统一任务/结果JSON初稿 | 已完成；全组当前v0.2正式冻结仍需会中确认 | docs/InterfaceContract_v0.2.md |
| 预留自动发现接口、Planner不写死地址 | 已完成接口，本轮进一步补了ADP Resolver；真实发现另验收 | resolver/base.py、resolver/adp.py |

第一周的团队目标还包含吴的平台与张的选型，不能代他们判完成。第一周进度同步中“赵尚未接真实LLM/Agent”的描述已被后续真实运行更新。

## 二、第二周赵的任务逐项核对

| 要求 | 状态 | 缺口 / 原因 |
|---|---|---|
| 冻结接口、统一requiredSkill | 赵侧v0.2实现、Schema和接入已就绪；未全组冻结 | 需三方确认字段/版本、吴的平台skill映射和合main方式 |
| 接统一LLM Provider、可切换 | 原异步DeepSeek已实测；本轮补张公共Provider适配并离线通过 | team可选路径尚未收费实测；未擅自替换当前默认，Reader/PaperQA仍用现有方案 |
| 至少5个新问题及人工验收 | 历史v0.1五例已人工通过；v0.2六例真实记录重新校验通过 | v0.2的人工作业仍待签收，不跨版本套用；见会前复核表 |
| 合理任务类型、skill、依赖与约束 | 已实现并有离线回归和真实计划 | 不承诺自动找仓库；本轮补提示词避免宣称智能代表论文筛选 |
| S1→S2→S3真实输入绑定逻辑 | 逻辑已实现，S1→S2已用真实数据；S2→S3离线通过 | 真实S3仍缺可调用仓库Wrapper和实际仓库地址；缺地址明确停止，不编造 |
| 替换Mock为真实ADP Resolver | 本轮完成可注入吴接口的ADP组件与官方模型校验 | 吴现有交付只有发现样例/接口形状；缺正式可运行平台send、认证、AIC/ACS/endpoint配置 |
| 正确AIP完成确认 | 真实本机Literature和Reader均通过 | 已验证working/get/awaiting-completion/complete/completed；不是平台身份认证 |
| 异常、超时、输入不足、部分成功 | 已实现并通过离线回归 | 有界重试仅用于已实现检索策略；Reader不自动收费重试；不做复杂自动重规划 |
| 真实多步骤闭环与统一日志 | 本地直接检索→阅读曾真实通过；两次真实AIP已通过保存记录串联，本轮补统一报告 | 新通用ADP+AIP调度仅离线验证；还未从一次新问题自动运行到平台检索/阅读/仓库完整闭环 |

本轮最终全项目验收：404项自动测试、0跳过、5个模拟场景、本地HTTP通过。不是404次真实科研请求，也不证明梧桐平台已接通。

## 三、本轮实际补完了什么

1. **ADP接入组件**：官方DiscoveryRequest/Response、active/skills.id过滤、显式业务skill映射、候选排序去重、ACS身份/能力验证、保留security；不猜平台地址、不降级证书。
2. **通用官方AIP执行与调度**：新增AipAgentExecutor/AipOrchestrator，绑定后发送任务，正确确认完成，记录协议任务号和证据，失败保留上游成功结果，无Mock兜底。
3. **张的公共LLM Provider桥接**：可选 --llm-adapter team，使用其真实源码、公有chat接口、既有配置；关掉额外重试以避免叠加计费；同步请求取消后不发送替代调用。默认真实路径未改变。
4. **两项真实任务的统一证据包**：复核已有AIP结果、来源及缓存哈希，保留原始任务号、身份、耗时、warnings/error/evidence，生成汇总科研报告和仓库接续输入。
5. **修正日志时间口径**：旧Partner回包sentAt沿用创建时间，汇总改用Leader收到completed的观测时间；原始回包未修改。两任务耗时之和不当作网络通信时延或单次端到端延迟。
6. **补回归与历史规划复核**：22项新增测试；v0.2六例重校验并整理人工待确认表，不改历史输出/验收标记。

这些组件的新增联调用合成ADP/Provider和本地SDK验证。此轮没有新增LLM调用、Semantic Scholar查询、PDF下载或平台请求；之前真实Reader运行产生的费用不算本轮新增。

## 四、最有说服力的真实证据

| 实测 | 结果 | 证据位置 |
|---|---|---|
| 真实本机AIP文献检索 | 3篇；Semantic Scholar HTTP200；协议/业务通过 | outputs/aip-literature-20261012-190244-092760 |
| 真实本机AIP全文阅读 | 1篇全文、5条证据；协议/业务通过 | outputs/aip-reader-20261012-194858-350390 |
| 统一真实日志及报告 | 复核上述两次调用，不重复计费 | outputs/aip-evidence-20261012-201417-544808 |
| 最新全项目离线回归 | 404测试、5模拟场景、本地HTTP | outputs/acceptance-20261012-201717-883881（之后若复测，以最新目录为准） |

原任务耗时：检索约1.07秒，Reader约60.65秒。Reader耗时含模型、检索和轮询等，不是纯AIP网络延迟。这是两个成功样本，不应据此写普遍成功率或平台访问次数。

AIC当前为本机测试标识，不是平台审批分配的正式身份；身份绑定/TLS未验证。模型笔记与证据对应关系仍需人工对照原文。

## 五、明天必须从组员那里拿到什么

### 给吴

“赵侧官方ADP模型、skill过滤/映射、Resolver和AIP调度接口已补齐并离线通过。请提供正式ADP发送方法或最小可运行代码、脱敏响应、已注册skillId、ACS/AIC、可访问endpoint与Direct/AMP选择规则、启用身份绑定的SDK client配置方式。还需要平台注册/发现/调用截图和访问记录。证书私钥/Key只放本机，不发共享文档。”

验收顺序：先在赵机器按literature.search发现正式Agent，再发一次固定真实文献任务；确认完成后保存原始响应、耗时和平台证据，再扩阅读。不要把离线discovery-response.local.json当平台响应。

### 给张

“Literature和Reader已在赵侧分别真实AIP跑通，Reader每次maxPapers=1、supports为具体结论字符串。请给Repository Wrapper当前分支/提交、依赖与启动方式、输入/输出成功及失败样例，确认repository_analyze/repository.analyze、明确GitHub URL输入和静态分析边界。另外请确认是否统一启用公共Provider桥接，以及Reader可移植安装方式。”

验收顺序：先指定一个真实仓库单独静态分析，再把真实paper_note和明确repositoryUrls绑定到S3；未执行代码就不能叫“复现成功”。

### 三人共同确认

- 业务schemaVersion=0.2；Planner内部requiredSkill；paper_read、repository_analyze；技能literature.search/paper.analyze/repository.analyze。
- AIP SDK=2.2.0、ACS协议字段02.02、Wrapper v0.1和业务v0.2是不同层的版本号。
- seven BusinessTask字段和six ResearchResult字段不变；业务结果不加status；evidence.supports为非空结论字符串。
- 明确v0.2人工验收、何时合main、谁审批PR；不能把本机未提交代码当已经在GitHub同步。

## 六、剩余工作按顺序

1. 赵人工复核v0.2六例，确认备注；全组确认业务v0.2及Provider方案。
2. 吴交付正式ADP/证书/endpoint配置后，在赵机器做一次真实发现+Literature AIP验收。
3. 张交付Repository Wrapper后，先独立真实仓库测试，再接S2→S3。
4. 用一次研究问题跑真实Planner→ADP→科研Agent→AIP确认→统一报告，补跨电脑、平台记录和失败样本。
5. 单独安排平台证据/性能统计、最终用户界面、部署与比赛材料；这些不是第一周最小Planner原型已完成就自动具备的功能。

## 可以直接口头汇报

“我的第一周架构、Planner、任务结果格式和发现接口基础已完成。第二周已用DeepSeek规划新问题，也把真实检索和全文阅读分别接到官方本机AIP，读出1篇全文和5条证据。今天补了ADP接入组件、公共Provider桥接、通用AIP调度和统一真实日志。现在还缺正式平台发现配置、Repository Wrapper、v0.2的人工签收与团队冻结，以及平台完整链路验收。已有本机测试和真实记录，但不会把它们算成梧桐平台完成。”

## 保存与同步边界

本轮没有commit、push或合main；既有dirty改动均保留。上述补齐代码仍在赵本机。outputs默认被.gitignore排除，队友不会自动收到本机验收目录。正式共享可选择脱敏日志摘要和此文档；不得直接上传.env、私钥、整个虚拟环境或未经确认的论文PDF。

操作细节：docs/week2_integration_ready_20261012.md。当前规划待确认表：docs/week2_v02_review_20261012.md。
