# v0.2 本地 AIP 业务接口联调

日期：2026-10-12。新增独立入口，不改变原 run_wu_aip.py 的传输测试含义。

## 当前成果

官方 acps-sdk 2.2.0 的 TaskCommand / TaskResult / Product / StructuredDataItem 通过本机真实 HTTP RPC 收发。固定测试计划包含 S1 literature_search 和依赖 S1 的 S2 paper_read。Leader 绑定检索产物的六字段 PaperRef，再通过 AIP 发送单篇阅读任务。两项能力由同一个临时测试 Partner 承接，不是两个独立科研 Agent 已部署。

七字段业务任务和六字段业务结果保持 schemaVersion=0.2；requiredSkill / dependsOn 仅用于 Leader，不发送给 Partner。subtaskId 与每次 AIP taskId 区分。每个任务完成 start / awaiting-completion → complete / completed 的双向确认。

Partner 以吴交付的 SDK handlers / executor 注入模式实现独立 v0.2 接入层，未修改或调用其旧版 v0.1 合约。没有修改张的 Wrapper。本轮注入的只是 fixture_execute 合成科研数据，不是张的真实 Wrapper，也不是重放已保存的真实论文结果。

所有合成条目有 isMock=true，summary / warnings 明确模拟。protocolCompleted=true 只代表本机协议完成；businessReady=false / realResearchExecuted=false 必须保留。没有调用 LLM、Semantic Scholar、PDF、ADP、ACS 注册或梧桐，也没有读取 .env。主项目 AipTaskRunner 默认仍拒绝模拟结果，只在这个明确的模拟测试入口显式 allow_mock_results=True。

## 一条命令

在 C:/Users/orika/Desktop/智创赛/ResearchMesh 的 VS Code PowerShell 终端执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_business.py
```

脚本自动启动随机回环端口的 Partner、核对唯一身份标识、执行两项 RPC、保存记录并清理自己启动的进程。不需要模型 Key，不需要第二个终端。不要使用 Reader 隔离环境代替项目 .venv，后者已经安装此次固定版本 SDK。

预期输出：接口联调通过，protocolCompleted=True，businessReady=False，真实科研未执行。

每次在 outputs/aip-business-fixture-时间戳 新建目录，保存 plan.json、payloads.json、executions.json、aipMessages.json、acceptance.json、environment.json、report.md、partner.log。失败时另存安全错误码，不打印原始 Provider 异常或密钥。失败记录的 partnerStopped=null 表示没有单独验证清理完成，不代表一定仍在运行。

## 验证

新增 30 项离线测试，使用真实官方 SDK 客户端/服务端和内存 ASGI HTTP，不访问外部网络。覆盖当前外壳、字段剥离、来源关联、错误和空阅读产物、数量超限、重复 start 幂等、跨会话/发送方关联、取消/超时、失败不调用下游、默认拒绝 Mock。其他既有测试保持通过。

完整验收：337 项自动测试，跳过 0 项，5 个模拟场景和本地 HTTP 检查通过：outputs/acceptance-20261012-184044-087116。

实际本机 HTTP AIP 联调：outputs/aip-business-fixture-20261012-184038-889195；全部 8 项验收检查通过。结束时临时 Partner 已停止。不能拿此记录当作真实科研或梧桐互联证明。

## 下一步与限制

1. 将真实 Literature Adapter 注入这一业务执行位置，再验证一次官方 AIP 收发真实论文结果。另设明确 live 模式和真实完成指标，不能只删除模拟标签。
2. Reader 每篇可能耗时几十秒、产生多次模型请求；SDK 此版本 HTTP 超时固定 30 秒。当前 Partner start 同步等待、内存存储，不可直接用于真实长时 Reader。先完善后台 working/get/complete、取消与检查点，再安排收费验证。
3. 多篇 Reader 仍由 Leader 顺序拆成 maxPapers=1 子任务，不能绕过张的单篇输入限制。
4. 身份绑定关闭仅限本机测试。消息身份/会话一致性检查不是证书认证；公网/梧桐必须交由吴的正式接入方案完成身份、发现、跨端和平台佐证。
5. Repository Agent 及科研证据精度另行验收，本轮不扩大范围。

代码尚未提交或推送 GitHub，没有改动组员远端分支。
