# ResearchMesh 完赛路线图

更新时间：2026-10-26

## 当前里程碑

| 里程碑 | 状态 | 证据边界 |
|---|---|---|
| Literature 正式平台单任务 | **PASS** | 正式平台证据已保存 |
| Reader 正式平台单任务 | **PASS** | `reader_formal_acceptance_20261026.md` |
| Repository 正式平台单任务 | **PASS** | `repository_formal_acceptance_20261026.md` |
| 三 Agent 本地连续协作 | **PASS（本地范围）** | 本地统一入口和真实 LoRA 三阶段记录；不等同正式平台 |
| 三 Agent 正式平台连续协作 | **待完成** | 必须验证真实数据绑定、三段状态、聚合与清理 |
| Leader 项目级编排与异常恢复 | **本地已实现，正式待验收** | 正式 `run_stage`、ADP/路由、幂等和失败阻断待验证 |
| 竞赛最终文档、部署、演示与提交 | **待完成** | 本机未找到权威官方提交规则，需团队确认 |

## 第一阶段：三 Agent 正式连续业务链路（完赛必需）

- [x] 三个 Agent 各自正式单任务 PASS。
- [ ] 冻结三个 Partner 的 commit、AIC、环境和启动命令。
- [ ] 同时启动并核对 Literature、Reader、Repository 三个正式 Partner。
- [ ] 赵提供正式三阶段 Leader runner/命令和 ADP/AIC 路由策略。
- [ ] 选定一篇有可靠 PDF 和明确仓库 URL 的最小正式样例。
- [ ] S1 `paper_records.v1` 真实绑定 S2 PaperRef。
- [ ] S2 `paper_notes.v1` fulltext note 通过既有 `paperNotes` 绑定 S3。
- [ ] S3 保持 static、无 DeepSeek、无目标代码执行，输出 `code_records.v1`。
- [ ] 三阶段各使用全新且不同的 Group、Session、Task，只发送一次 Start。
- [ ] 保存 Partner/Leader journal、SQLite、Worker、Result 哈希、绑定输入和清理证据。

## 第二阶段：项目级编排、聚合与异常恢复（完赛必需）

- [ ] 验证 Leader 严格按依赖启动下游，不将三个独立成功冒充连续协作。
- [ ] 验证最终聚合包含三阶段 Result、来源 AIC、协议 ID、输入/结果哈希和状态。
- [ ] 验证失败/uncertain 会保留上游证据并阻断下游，不自动重试或降级为本地结果。
- [ ] 验证相同 Start 的 Partner 去重及相同 Result message ID 的 Leader 幂等。
- [ ] 验证每个 Group 完整清理且无残留 Worker、消费者或临时业务资源。
- [ ] 形成项目级正式验收结论和证据索引。

## 第三阶段：冻结与演示材料（完赛必需）

- [ ] 冻结可复现 Git commit、依赖版本、非敏感配置模板和部署步骤。
- [ ] 整理三个单任务、连续链路及项目级验收的最终证据包。
- [ ] 编写最终技术说明、架构/数据流、部署说明、故障恢复和安全边界。
- [ ] 准备演示脚本并彩排，避免控制台 QuickEdit、旧 ID 或重复 Start。
- [ ] 保存演示使用的确定性输入和不含秘密的结果摘要。

## 第四阶段：竞赛提交（完赛必需，规则待确认）

- [ ] 从官方渠道确认截止时间、提交入口、文件格式、大小限制、演示视频和开源要求。
- [ ] 将官方要求映射到现有文档和证据，补齐缺口。
- [ ] 完成作品提交并保存回执。

## 重要但不直接阻塞最小完赛闭环

- [ ] 增加 Provider 独立 HTTP 请求审计，弥补内部模型计数边界。
- [ ] 评估跨进程/重启的持久 Result outbox；当前仍需 Leader 对稳定 message ID 幂等。
- [ ] 统一 formal journal 文件名前缀。
- [ ] 建立不含秘密值的部署配置检查表。

## 可选优化

- [ ] Repository DeepSeek enrichment。
- [ ] 多论文智能排序、多仓库搜索或动态执行。
- [ ] 更复杂的并发、长时间断网和性能测试。
- [ ] 桌面或网页交互界面。

可选优化不得延误正式连续链路、项目级验收和竞赛提交。

## 当前分工

- 吴：归档 Partner 证据，核对/启动三套 Partner，确认唯一入组与运行状态，维护冻结版本清单。
- 赵：提供正式三阶段 Leader runner，创建全新 IDs，执行顺序编排、结果聚合、幂等和 Leader 侧证据保存。
- 张：确认现有 Repository Wrapper 与 static paperNotes 适配不变；仅在真实契约差异出现时修改，不扩展 Schema。
