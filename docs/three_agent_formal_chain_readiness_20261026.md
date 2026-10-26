# 三 Agent 正式连续协作准备清单（2026-10-26）

## 当前结论

Literature、Reader、Repository 已分别完成正式平台单任务验收。统一编排、依赖绑定和结果聚合已有本地实现及测试证据，但尚未通过一次正式平台 `Literature -> Reader -> Repository` 连续运行，因此项目尚未完成正式端到端验收。

## 能力与证据分层

### 已有实现且有正式证据

- 三个正式 AIC 均已用各自证书和 Inbox 完成单任务：
  - Literature：`1.2.156.3088.1.BUPT.RECKYJ.4K8V3F.0113BD.00TL`
  - Reader：`1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN`
  - Repository：`1.2.156.3088.1.BUPT.RECKYJ.13NQBJ.YM4WBJ.0E0X`
- 七字段 Partner Task、六字段业务 Result、SQLite 持久去重、formal journal、Result 持久化和完整 Group cleanup 已分别获得正式证据。
- Reader 与 Repository 使用包含 MQ 断线容错的共享 Group 生命周期；Repository 正式任务已验证当前 commit 的正常完成和清理。
- 三个单任务均使用正式 Leader AIC `1.2.156.3088.1.BUPT.RECKYJ.Q77J9R.52YOLZ.11P6`。

### 已有实现但只有本地测试或本地真实链路证据

- `group_three_agent_orchestrator.py` 已实现三阶段顺序编排、阶段超时、失败阻断、无自动重试和新鲜 ID 证据检查。
- `three_agent_dispatch_v02.py`、`input_binding.py` 已实现依赖解析和 S1/S2/S3 输入绑定。
- 本地统一入口已有一次真实 LoRA 三阶段记录；它不是正式 ACS/AMQPS/Group 证据。
- Leader 的最终报告和阶段记录聚合已有本地实现。
- S2 `paper_notes.v1` 到 S3 的上下文传递已有既有接口：S3 的七字段 Task 中使用 `input.papers`、`input.paperNotes`，并设置 `constraints.requirePaperNotes=true`。Repository 的 `repository_paper_context.py` 会校验一篇真实 fulltext note，再通过现有 static Worker 做确定性词面对照；不增加新 Schema，不启用 DeepSeek。

### 仍需完成的平台集成

- 赵侧把正式 Group Leader 执行函数接入 `dispatch_group_plan(..., run_stage=...)`；当前工作区只有可注入边界，没有赵侧生产 Leader 命令。
- 正式运行中为三个阶段分别创建全新的 Group、Session、Task，并保存每段 invitation、唯一 Start、Result、Complete 和清理证据。
- 验证 S1 Result 实际绑定为 S2 PaperRef，S2 fulltext paper_note 实际绑定为 S3 `paperNotes`，而不是三个彼此独立的成功任务。
- 验证正式 Repository 的 `requirePaperNotes=true` 路径和 `paperComparison`；该路径已有本地测试，尚无正式平台证据。
- 核对平台 ADP 自动发现。三个 Agent 的 ACS 身份、mTLS 和直接 Inbox 调用已由单任务证明，但不能据此推断赵侧 ADP 自动发现已经验收。
- 生成项目级最终聚合结果，关联三个阶段的 Group/Session/Task、Partner AIC、输入/结果哈希、状态与清理证据。

## 当前运行状态

- 当前只检测到 Repository Partner 正在运行，commit 为当前工作树 `2564a13767eb9db7e8d10ac8ee752ece5b224008` 对应部署。
- Literature、Reader Partner 当前未检测到运行进程。正式连续联调前需要按已验收配置重新启动，并核对三者均使用冻结 commit、各自 AIC、独立 journal、单一消费者和稳定 Inbox。
- 不应复用三个单任务验收的任何 Group、Session 或 Task。

## 正式连续链路建议契约

1. S1：`literature_search / literature.search / paper_records.v1`。
2. Leader 从 S1 结果绑定一篇带可靠 PDF 来源的 PaperRef 到 S2。
3. S2：`paper_read / paper.analyze / paper_notes.v1`，`maxPapers=1`，必须产生 fulltext note。
4. Leader 将同一论文的 `papers` 与 `paperNotes` 绑定到 S3，并提供一个明确仓库 URL。
5. S3：`repository_analyze / repository.analyze / code_records.v1`，`maxRepositories=1`、`executeCode=false`、`allowRepositorySearch=false`、`requirePaperNotes=true`。
6. S3 保持 static、DeepSeek 关闭；`paperComparison` 只表示确定性词面对照，不声称算法等价或复现成功。

`requiredSkill` 和 `dependsOn` 始终留在 Leader 内部；Partner 只接收七个业务字段。AIP TaskResult 是平台包装，不与六字段业务 Result 或 `code_records.v1` 混淆。

## 可靠性与失败处理

- 每阶段唯一 Start，不自动重试或自动重派；状态不确定时先查 Partner journal、SQLite 和 Worker。
- 下游只在上游 `completed`、业务 Result 校验和输入绑定成功后启动。
- 相同 Start 依赖 Partner SQLite 去重；相同 Result message ID 的重复投递仍要求 Leader 幂等。
- 任一阶段失败或 uncertain，保留已完成上游证据，停止依赖阶段，不以本地 fallback 冒充正式成功。
- 每阶段完成后验证 `DISBAND_RECEIVED`、`GROUP_LEFT`、`CLEANUP_SUCCEEDED`、`LEAVE_FINISHED`，再进入下一阶段。
- 项目级 PASS 必须同时满足三段业务成功、真实数据绑定、最终聚合成功、三个 Group 完整清理和无残留 Worker。

## 最短执行清单

1. 吴：确认三套工作树/运行环境均指向冻结 commit，准备三套独立启动命令和窗口标识。
2. 吴：启动缺失的 Literature、Reader Partner；核对三个 Partner 的 AIC、journal、AMQPS、Inbox、单实例和空闲状态。
3. 赵：提供正式三阶段 Leader runner/命令，确认 ADP 或明确的 AIC 路由、阶段等待、Result 幂等和聚合输出格式。
4. 张：只确认现有 Repository Wrapper 与 static paperNotes 适配保持不变；无需扩展 Schema 或启用 enrichment。
5. 全员：选定一篇可正式检索、可靠获取 PDF、且有明确仓库地址的最小样例；提前固定安全约束。
6. 赵按阶段创建全新 IDs；每阶段 invitation 后由吴确认唯一 `GROUP_JOINED`，再放行唯一 Start。
7. 吴和赵分别保存 Partner/Leader 证据；S3 完成后核对最终聚合、失败语义和全部清理。
8. 项目级验收通过后冻结版本、配置清单和证据索引，进入演示与竞赛提交材料阶段。
