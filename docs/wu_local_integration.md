# 吴交付 v0.2：本地 AIP 联调

> 2026-10-04 更新：赵使用业务接口 v0.2（与“吴交付包 v0.2”不是同一版本概念）。默认计划改为仓库中的 examples/aip_transport_plan_v02.json；仍支持 --plan 选择历史计划。仅独立文献任务经明确兼容入口发成吴包所需的业务 v0.1。两个版本写入 environment.json，仍仅验证传输。下文历史默认 outputs 路径已被替换。

更新：2026-10-04。已新增独立入口，不改变 run_demo.py、run_planner.py、live_api.py 的含义。

## 一条命令运行

在 ResearchMesh 项目根目录的 VS Code PowerShell 终端执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_wu_aip.py
```

不用开第二个终端、不用先启动 Partner、不用填写 Key。该脚本要求 Python>=3.11（吴的组件使用 asyncio.timeout）及 ACPs SDK 2.2.0；当前项目 .venv 已满足。

默认读取 `outputs/live-planner-20261003-124233-600820/plan.json`，即赵 10 月 3 日实际生成的计划。仅执行其中 S1 的文献检索**传输**，不会执行 S2/S3。没有重新请求 DeepSeek，没有连接 Semantic Scholar、ADP 或梧桐，没有产生模型额度消耗。

## 本次做了什么

1. 校验保存的计划，只允许选择无上游依赖的 literature_search。
2. 去掉 requiredCapability/dependsOn，保留七个业务字段。
3. 自动在随机回环端口启动吴的默认传输测试 Partner。
4. 复用吴的 ResearchClient 与 invoke_for_planner 发送官方 SDK 结构化 AIP 消息。
5. 核对 taskId/sessionId/返回身份，记录 start → awaiting-completion → complete → completed。
6. 单独判断协议完成与科研有效性，生成报告和日志。
7. 无论正常结束或调用异常，都清理自己启动的 Partner；不停止你已运行的其他服务。

## 正常输出

```text
通信验收：通过
protocolCompleted：True
businessReady：False
科研执行：未执行；本轮只验证传输。
临时 Partner 已停止。记录：……
```

businessReady=false 是当前预期结果，不是联调失败。吴的默认 Partner 明确返回 TRANSPORT_TEST_ONLY；它不代表已经查询论文，也不代表真实检索无结果。只有本地传输测试通过时脚本返回退出码 0；这不等于科研任务成功。

## 日志位置

每次新建 `outputs/wu-aip-local-时间戳/`，不覆盖历史文件：

- plan.json：原始已保存计划。
- request.json：本次发送的业务任务，不是完整 AIP 信封。
- outcome.json：吴的适配器结果，含协议 taskId、耗时、warnings 和业务结果。
- aip_events.json：实际命令/状态事件、消息 ID、会话 ID、时间及本地测试身份。
- acceptance.json：协议通过与科研未执行的独立标识，未执行的子任务清单。
- environment.json：解释器、SDK、输入路径、吴代码文件哈希，便于复现；不包含 Key。
- report.md：可读结论；partner.log：本地服务诊断。失败时另有 failure.json。

## 文件夹或计划更换

默认依赖同级目录 `../wu_delivery(1)/ResearchMesh_wu_delivery_v0.2_local`，未复制、修改吴的源码。不要删除或移动它；如果改了位置，通过参数指定：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_wu_aip.py --wu-root "吴交付包中包含wu_delivery文件夹的目录" --plan "已保存的plan.json完整路径" --subtask S1
```

这里 --plan 需要本项目内部完整 Plan JSON，不是 Swagger 的 {plan, metadata} 外壳。不支持将未统一的 paper_read/code_search 自动转换；指定这些任务会明确拒绝。

## 边界和接续工作

这是独立、可复现的本机 HTTP/AIP 联调入口，尚未整合为 live_api.py 的真实科研执行接口。使用固定的本地测试 Partner，不使用 ADP 自动发现。身份字段一致性检查不等于证书身份认证；关闭 SDK 身份绑定仅限回环环境，禁止直接发布公网。

吴的 businessReady 不负责完整 data/evidence 语义校验，因此入口仍调用赵侧 result_contract。当前入口只能验收默认传输测试；将来接真实后端时必须新增真实执行模式与相应验收，不能删除 warnings 就宣称完成。

原 aip_lifecycle.py 的科研确认组件现在明确拒绝 TRANSPORT_TEST_ONLY；本入口则允许完成传输确认，但永远标记 researchExecuted=false、researchSucceeded=false。两者用途不同，没有新增业务 status 或自造 AIP 状态。

下一步：吴完成 Semantic Scholar 探针后，还需把真实查询封装为 execute(task) 并注入 Partner，保留来源和限制，再做真实检索联调。阅读/代码接口差异和真实平台发现另行统一。
