# 本地闭环与后续接入说明

> 历史说明：本文描述第一周 Mock 阶段，不代表当前全部进度。DeepSeek 已于第一周接入，第二周已补充真实问题验收和数据绑定。当前约定请以同目录 `InterfaceContract_v0.1.md`、`week2_progress.md` 为准；以下“真实模型未接入”等描述仅适用于当时的 Mock 入口。

## 当前阶段和边界

当前阶段验证：固定样例规划 → 本地能力匹配 → 按依赖执行模拟任务 → 校验业务结果 → 汇总报告。
不调用真实模型、Semantic Scholar、PaperQA、GitHub、平台发现服务或 AIP SDK。
所有模拟文献与仓库链接使用 `example.invalid`，不可作为科研出处。

## 哪些约定保持不变

使用全组共享 v0.1 的 camelCase 字段，不使用旧个人流程中的 `task_id`、`task_type` 或业务 `status`。

- Planner 内部任务包含 `requiredCapability` 和 `dependsOn`。
- 交给执行器的业务内容只包含 `schemaVersion`、`subtaskId`、`taskType`、`goal`、`input`、`constraints`、`outputSchema`。
- 业务结果外壳固定为 `schemaVersion`、`subtaskId`、`taskType`、`summary`、`items`、`warnings`。
- 模拟运行日志中的 `succeeded`、`skipped`、`error` 是 Leader 的本地诊断，不在业务结果里，不构成网络任务状态机。
- 真实接入后，任务协议状态以官方 `TaskResult.status.state` 为准，不能把这些本地诊断当作 AIP 状态。

## 能力解析接口（交给吴）

`AgentResolver.resolve(required_capability)` 是异步方法，返回 `AgentHandle`。

`AgentHandle` 包含本地标识 `agent_id`、能力列表 `capabilities`、执行适配器 `executor`。
当前 `mock-...` 标识不是真实 AIC。当前能力标签只是团队业务标签，不假定等于平台 ACS 的 skillId。
吴实现真实发现时需要提供业务能力与平台技能的映射，并为选中的 Agent 创建 AIP 执行适配器。
不存在已实现的真实平台 Resolver，也没有在 Planner 内写死服务 URL。

## 执行适配器（交给吴和张）

异步方法 `execute(payload, dependency_results)` 目前在同一个 Python 进程内调用。

- `payload` 是上述 v0.1 业务任务内容。
- `dependency_results` 是按上游 `subtaskId` 索引的已校验结果；它是 Leader 内部上下文，不是新增的 AIP 字段。
- 阅读与代码模拟执行器从这个上下文取得论文记录。没有修改 Planner 原始计划。
- 真实适配器接入前，全组应明确阅读任务的论文标识/PDF 输入和代码任务的论文信息输入。
- 然后由真实适配器把上下文转换为对应 Partner 支持的 `input` 内容，使用官方 SDK 的结构化数据项和 TaskCommand 发送。
- 标准 TaskResult 中的业务产物应转换成共享 v0.1 结果；只有协议生命周期确认成功的产物才可当作已完成结果使用。
- 不要将本地 `execute` 接口直接改成自定义 HTTP 消息协议，也不要把 `research_result.json` 当作 TaskResult。

## 调度与故障约定

同一批没有未完成依赖的任务使用 asyncio.gather 并发执行。
依赖任务未成功时，不调用下游执行者，记录 `DEPENDENCY_FAILED`。
能力缺失、异常、超时和结果格式不匹配不会丢掉其他独立任务的成功结果。
当前只是简单批次调度，不包含真实任务轮询、自动重试、故障切换或动态重规划。

## 后续仍需完成

真实模型适配与新问题验收；真实发现与 AIP 生命周期处理；开源 Agent 服务封装；平台访问证据。
真实模型和三类 Agent 没有完成接入，第一周的真实规划验收和赛事最终互联验收不能因为 Mock 测试通过就判定完成。
