# ResearchMesh 项目发布与验收状态（2026-10-23）

本页是当前发布判断入口。较早的周报和交接文件保留历史语境；出现冲突时，
以本页、实际 Git commit 和仓库外验收报告为准。

## 当前状态矩阵

| 范围 | 本地实现与离线测试 | GitHub | 正式平台验收 | 人工内容验收 |
| --- | --- | --- | --- | --- |
| Planner v0.2 | 已完成 | 已在赵集成分支 | 不单独作为科研 Agent 验收 | 六份 v0.2 计划仍需用户签收 |
| Literature Partner | 业务、SQLite 去重和正式审计已完成 | 已推送审计分支 | 第三次业务成功；五项审计须使用全新 Group/Session/Task 重验 | 新结果论文相关性需核对 |
| Reader Partner | 单篇全文、独立 Worker、审计及正式 Leader 入口已完成 | 已推送 | 尚未正式单任务验收 | 正式 PDF 结论需对照原文 |
| Repository Partner | 单仓库 static、独立 Worker、审计及正式 Leader 入口已完成 | 已推送 | 尚未正式单任务验收 | 代码证据和论文关系需核对 |
| 三阶段编排 | 本地科研链及注入式 Group 编排已完成 | 本分支 | 正式 S1→S2→S3 尚未运行 | 最终报告需人工签收 |
| ADP/AMP/MQ | ADP 注入组件、mTLS 预检、三 Agent AMP 配置已完成 | 本分支 | 在线发现、采集和跨端状态仍待正式窗口 | 不适用 |

## 本轮独立收尾

- `verify_project_acceptance.py` 读取三份独立 Partner journal 和 SQLite，调用现有单 Agent
  验收器，并要求三类 Task、Group、Session、Task 和 Agent 身份均各自唯一。报告使用
  独占新文件，不能覆盖历史证据。
- `aip/group_partner_service.py` 在 SDK 完成正常退群及资源检查后，关闭旧客户端并创建
  新客户端与 Inbox。清理失败时停止重建，避免带残留资源继续服务。没有修改安装的 SDK。
- Reader、Literature、Repository 均有独立 Fluent Bit heartbeat 配置；
  `scripts/validate_amp_configs.py` 只做离线结构检查。配置路径须按实际部署工作树确认。
- `run_platform_analysis.py` 为 Reader/Repository 提供默认离线的正式 Leader 单任务入口。
  `--execute` 还必须同时给出 `--confirm-after-join` 和 Partner 就绪确认。
- `group_three_agent_orchestrator.py` 负责严格依赖绑定、分能力预算和一次一阶段执行；
  平台调用由注入函数提供，模块本身不会连接网络或自动重试。
- `scripts/audit_release_history.py` 扫描所有可达 Git 文本 blob，只保存对象、路径、模式类别
  和行号，不保存匹配内容。发现项必须人工判断或清除，不能把扫描通过理解为第三方依赖审计。

## 正式验收顺序

1. 在代码审查和窗口确认后启动三个 Partner；不要启动同一 AIC 的重复实例。
2. Literature 使用全新 Group、Session、Task，执行严格单次 Semantic Scholar 验收。
3. Reader 使用另一组全新标识和一篇来源可验证的 PDF；模型 HTTP 次数不套用 Literature 标准。
4. Repository 使用另一组全新标识和明确 HTTPS GitHub URL，只运行 static 模式。
5. 每轮在 Result、Complete、SQLite completed 后发送 DISBAND，确认清理、GROUP_LEFT，
   再确认 `INBOX_RESTART_SUCCEEDED`，不得用进程存活代替 Inbox 可用性。
6. 分别运行单 Agent 验收器，再填写
   `examples/project_formal_acceptance_manifest.json` 并运行：

```powershell
& D:\research\ResearchMesh\.venv\Scripts\python.exe -X utf8 verify_project_acceptance.py `
  --manifest .\examples\project_formal_acceptance_manifest.json
```

联合报告 `accepted=true` 只证明三轮规定的协议、业务和资源证据完整。
它不证明论文结论、代码关系或模型输出已经获得人工科学验收。

Reader/Repository Leader 离线预检可分别使用
`examples/group_reader_single_task_v02.json` 和
`examples/group_repository_single_task_v02.json`。示例只是待发送任务候选；正式发送前仍须由
Leader 核对 PDF、仓库地址、目标 AIC、运行 commit 和时间窗口。默认不带 `--execute`，不会联网。

## GitHub 堆叠审查

为避免把跨成员历史一次性压成一个难以核对的大 PR，远端已准备三层分支：

1. `zhao/planner-integration` ← `wu/platform-integration-base`：基础平台与三 Agent 运行时合流。
2. `wu/platform-integration-base` ← `wu/platform-acceptance-stack`：Literature 持久化验收审计。
3. `wu/platform-acceptance-stack` ← `wu/project-release-readiness`：联合验收、Inbox 恢复、
   Reader/Repository Leader 入口、AMP 和发布扫描。

可直接检查或创建 PR：

- <https://github.com/orikasajoseph/ResearchMesh/compare/zhao/planner-integration...wu/platform-integration-base>
- <https://github.com/orikasajoseph/ResearchMesh/compare/wu/platform-integration-base...wu/platform-acceptance-stack>
- <https://github.com/orikasajoseph/ResearchMesh/compare/wu/platform-acceptance-stack...wu/project-release-readiness>

建议标题依次为：

- `Integrate Wu platform and three-agent runtime baseline`
- `Add persisted Literature Group acceptance audit`
- `Complete project release readiness tooling`

当前 GitHub App 对创建 PR 返回 403；分支推送和远端 SHA 已独立验证。该限制不影响比较，
但需要具备仓库 PR 写权限的账号完成最后创建动作。

## 仍依赖协作或正式环境

- Leader 路由、ACL、目标 AIC、Reader 超时余量和正式运行窗口确认。
- 三次正式单任务和随后正式三阶段编排。
- ADP 实际发现、AMP Kafka 可见性、远端 broker 资源盘点和跨电脑行为。
- PR 审查、合并目标、接口冻结及赛事最终提交。
- 六份 v0.2 规划、Reader 全文结论与 Repository 关系证据的人工签收。
