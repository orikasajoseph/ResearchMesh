# 本地 AIP 接入真实文献检索

日期：2026-10-12。更新：用户已真实运行 outputs/aip-literature-20261012-190244-092760，返回3篇，后台观测 Semantic Scholar HTTP200；protocolCompleted/businessReady 均为 True。下文开发轮验收记录保留当时状态。下一阶段 Reader 接入见 aip_reader_live_20261012.md。

## 做了什么

run_aip_literature.py 读取已有 v0.2 Plan，只选无依赖的 literature_search（默认 S1）；剥离 requiredSkill / dependsOn，七字段业务负载放入官方 StructuredDataItem / TaskCommand。

本机临时 Partner 通过 aip_literature_partner.py 的执行回调复用现有 execute_live_task、Literature Adapter、张的真实 LiteratureAgent 和 SemanticScholarProvider。外层任务/结果仍为0.2；Wrapper 自己使用 literature_search.v0.1 / paper_records.v1，不代表协议降级。没有修改张或吴源码，不使用模型规划/DeepSeek、Reader、PDF、Repository Agent、ADP 或梧桐。

新增后台执行选项：start 快速返回 working，Leader 定期 get；后台完成后 awaiting-completion，Leader 校验 Product、发送 complete 并核对 completed。避免 SDK2.2.0固定单次HTTP30秒限制与检索退避等待相冲突。测试入口默认同步模式不变。

真实检索沿用现有每进程1.2秒请求间隔和有界429恢复，不增加第二层重试，不重发start、不重复规划。Partner执行最多120秒，Leader整体最多135秒；上限耗尽或取消不表示API从未收到请求。此版本任务状态仍在内存中，不是持久化生产服务，也不直接开启真实Reader。

## 操作

在 ResearchMesh 项目根目录的 VS Code PowerShell 终端执行，使用项目 .venv（包含固定官方SDK）：

仅预检（本机Key存在性与Wrapper导入，不联网，不创建Provider）：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_literature.py --plan "outputs/research-live-20261012-164848-827095/plan.json"
```

明确允许一次真实 AIP 文献检索：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_literature.py --plan "outputs/research-live-20261012-164848-827095/plan.json" --execute
```

上述计划原本还包含阅读；本次只执行S1，environment.json保留未执行子任务清单。不重复读取上次检索结果：本次会由Partner重新查询Semantic Scholar。模型额度不会消耗，但会使用Semantic Scholar的API配额，团队共用Key仍可能被429限流。

计划目录更换时使用 --plan；Wrapper位置更换时使用 --wrapper-root；默认 agents/literature。只支持当前0.2独立文献任务，不自动迁移旧版或执行依赖任务。

## 结果和真实性

正常有论文时 protocolCompleted=True、businessReady=True；需同时满足：官方AIP确认完成、后端业务结果与AIP Product逐项一致、已观察到实际Semantic Scholar HTTP200、不是模拟结果、存在可定位论文。

空结果可能 protocolCompleted=True，但 businessReady=False、emptyResult=True；不得虚构论文或称已提供科研材料。API/校验/超时失败不会变为正常产物。realSourceRequested=null 表示缺少HTTP观测，不能断言未请求过API。缺少观测或只有Mock不能通过真实检索验收。

本地记录保存在 outputs/aip-literature-时间戳：plan.json、payload.json、environment.json、backend_outcome.json、outcome.json（含官方RPC命令/响应）、acceptance.json、report.md、partner.log。后端在返回Product前立即保存规范化结果和安全HTTP诊断。没有明文Key或请求头，异常只记录白名单代码。API Key仅从本机根目录.env或环境变量读取，不经命令行/AIP传输。

脚本自动启动/关闭自己创建的随机回环端口Partner，未修改其他服务。身份绑定关闭仅限本机测试；消息身份/会话一致性不是证书认证，不能直接部署公网。

## 开发验收证据

- 22项新增离线测试通过：真实张Wrapper+FakeProvider、官方SDK+内存ASGI HTTP、后台working/get、幂等、错误、取消和关闭清理、空结果、默认预检不联网、观测不足不得误报真实完成。没有真实API调用。
- 全项目359项自动测试、0跳过，5个模拟场景及本地HTTP通过：outputs/acceptance-20261012-185526-497786。
- 原模拟AIP入口实际本机HTTP复测通过：outputs/aip-business-fixture-20261012-185527-991965。
- 本机真实配置预检通过：outputs/aip-literature-20261012-185524-730461/preflight.json，networkAttempted=false。
- 本轮未自动运行 --execute；真实AIP文献结果等待用户首次运行验证。

所有当前变更未提交、推送或合并main。后续真实Reader仍需检查长任务/恢复与付费边界，梧桐发现、证书及平台侧证据另行完成。
