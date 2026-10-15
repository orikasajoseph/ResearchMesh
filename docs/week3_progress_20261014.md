# 第三周赵侧进度与联调交接

> 历史快照：以下为 2026-10-14 的实现时状态；后续进展与发布交接请看 `docs/team_handoff_20261015.md`。

更新：2026-10-14。依据第三周任务表逐项核对；本轮只完成赵侧支持和免费验证。没有请求模型、检索API、PDF、正式平台ADP或MQ，没有修改平台注册/在线状态，没有提交或推送。本机新增改动不能当作已公布的新Git基线。

## 三方基线

已用git ls-remote/fetch核对：赵3ba828e0755f91e789a829c42f7d5df5f4045034；张be99b31b3c8411ea88a96d08b0337d6049882046；吴58e4e5d884bd4304e7ab3dd8399c1185c7b428eb。远端未看到新增integration分支；张若有尚未推送的接线代码，应先提供路径/提交再协调，不能断言其本机没有开发。

两份队友提交提取成独立审查副本，原代码未修改，也未盲目merge旧Planner/Schema。verify_week3_support.py 对副本源码逐文件匹配固定Git对象，保留源哈希与版本。

## 本轮已实现

- repository_adapter.py：把已经绑定的S3任务转换成张的严格请求，去掉Leader字段；原生repository_record转换为共享repository结果，不改共享Schema。校验任务/仓库关联、真实格式SHA、证据ID、路径和行号；证据supports输出具体结论字符串或明确静态源片段说明，不把分类词数组冒充结论。
- aip_business_partner.py：显式Repository模式、任务白名单、单仓库限制、技能健康检查和结果来源核对。旧双Agent行为维持；普通fixture模式不自动开放S3。
- run_aip_literature.py：现有invoke_business显式支持repository_analyze，未新增HTTP消息协议；默认真实调用仍拒绝Mock成果。
- aip_record_paths.py：沿用同一目录保护并允许单入口repository阶段，不允许根、缓存、越界和不符合格式的嵌套路径。
- resolver/local.py：LocalAipResolver，只接收身份对应的AipAgentExecutor；能力缺失或歧义拒绝，不自动fallback，不冒充正式ADP发现。
- examples/repository_s3_week3.json：固定pypa/sampleproject的静态S3样例，不执行目标代码、不搜索仓库。
- verify_repository_seam.py：真实张Wrapper+假克隆/静态Provider+官方SDK往返；明确标为离线，业务真实验收=false。
- verify_week3_support.py：三方固定源码、冻结接口及离线回归检查；原始日志只留本机outputs。

## 已执行验证

赵侧完整回归471项、0跳过，含原451项与新增20项；5个模拟场景及本地HTTP通过。独立记录 outputs/acceptance-20261014-232225-101573。

张原be99b31的RepositoryAgentUnitTests：21/21通过，不运行CodeWiki、不调用模型。第22项TinyRepositoryCodeWikiRegressionTests尚未复测，本机当前未准备CodeWiki运行时；不要写成22/22已由赵复测。

吴原58e4e5d的test_group_literature与test_three_agent_aip_v02：6+9=15项通过；仅Fake客户端/本机SDK，不是正式MQ测试。

真实张Wrapper离线S3：protocolCompleted=true，businessReady=false，CodeWiki/model/platform执行均false；记录 outputs/repository-seam-offline-20261014-231819-143068。

联合回归记录 outputs/week3-support-20261014-232541-196902；最终重复运行以最新目录为准。任何测试次数都不是等量真实科研请求。

## A1到A10对照

| 项目 | 赵侧当前状态 | 未完成原因/下一步 |
|---|---|---|
| A1 Planner基线 | 已复测，原451项保持通过；新增后471项 | 本轮改动尚需独立提交与团队确认，不能倒退至旧基线 |
| A2 Repository基线 | 21项纯单元复测通过；任务表报告张已22项及live通过 | 第22项需CodeWiki运行时；还未重跑真实克隆/增强 |
| A3 Literature基线 | 吴6+9项离线复测通过；赵已有真实双Agent记录 | 没有本轮重跑吴的真实Literature链，不能把15项称全部真实A3验收 |
| A4 S3真实官方本机AIP | 输入/输出/白名单/目录/SDK离线支持完成 | 尚缺张integration接线版本或协调确认、隔离CodeWiki运行时和批准的真实增强验收 |
| A5 S2到S3 | Leader已有输入绑定；已明确发现并保护原生接口缺口 | be99b31不接收papers/paperNotes，不能静默丢弃。需张提供消费论文上下文的方案与真实仓库URL，再做实测 |
| A6 真ADP发现 | 任务表报告吴已成功；本轮未连平台 | 需吴的新发现时间点/脱敏记录；不改其发现/身份代码 |
| A7 正式online | 未验收 | 任务表记alive=unknown，需吴完成Heartbeat/Monitor并提供online或平台认可的等价证明 |
| A8 正式Group MQ | 吴handler离线测试通过；真实链未验收 | 需真实双方AIC、证书/MQ本机配置与可运行Leader入口；由吴完成正式平台交付，不用离线替代 |
| A9 三方整体验收 | 固定三方源码的联合离线回归通过 | S3、S2到S3和正式MQ的真实路径缺证据，不能整体标完成 |
| A10 交付材料 | 本说明、最小样例、命令、源码哈希及日志已准备 | 待协调后形成独立提交、最终共同基线及剩余问题清单 |

## 必须与张对齐的接口缺口

原生be99b31的RepositoryTaskInput只有repositoryUrls，constraints只有maxRepositories与executeCode。当前共享S3包含papers、paperNotes及requirePaperNotes。

本轮只在明确独立静态分析时移除经过校验的false控制标志。只要输入有论文/笔记，或requirePaperNotes=true，to_repository_request返回REPOSITORY_PAPER_CONTEXT_NOT_SUPPORTED；不会吞掉上游论文结论后伪称S2到S3完成。请张确认采用Wrapper/enrichment上下文扩展还是其他明确方案；共享Schema未获三方同意不能修改。

请张同时确认是否已有未推送的integration版本。本轮是赵侧支持，不接管或改写他的核心Wrapper、CodeWiki upstream或吴的冻结交付。建议先固定sampleproject做真实S3，再提供与真实论文明确关联的仓库做S2到S3。

## VS Code复测命令

项目根目录先运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_project.py
```

本机已准备的固定副本可这样复测（其他机器自行替换副本路径）：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_repository_seam.py --wrapper-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\week3-upstream-20261014\zhang-be99b31\agents\repository"
.\.venv\Scripts\python.exe -X utf8 verify_week3_support.py --zhang-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\week3-upstream-20261014\zhang-be99b31" --wu-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\week3-upstream-20261014\wu-58e4e5d"
```

这些命令不调用收费模型，也不连接梧桐。Wrapper副本不在赵仓库里，clone赵分支不能自动获得它；应从指定队友提交准备，不能把只有README的agents/repository当作完整实现。

## 尚需的信息与许可

1. 张：integration分支/commit或本机目录、paperNotes消费方案、CodeWiki隔离环境与clean install说明。
2. 吴：最新正式online证据、Group Leader发起入口、正式双方AIC、证书在本机的配置方式及脱敏TaskCommand/TaskResult事件。不要在聊天发Key或私钥。
3. 用户确认是否允许一次sampleproject真实S3增强验收。当前本轮没有新增模型费用，已发出可选确认；原生运行时尚未准备，费用和环境就绪后才能记录A4真实通过。
4. 需要人工核验规划与科研证据，不能由助手代签“humanAccepted”。
