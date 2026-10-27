# 正式平台三 Agent 连续编排核心

`platform_three_agent_orchestrator.py` 是正式连续协作的本地、协议无关核心。它不连接平台，也不直接
调用论文 API、PaperQA、Git、CodeWiki 或模型；正式运行时由调用方注入 Literature、Reader、
Repository 三个已验收 Group runner。

首版采用三个独立 Group 顺序串联，以复用已经通过的单任务生命周期：

1. `literature_search` 返回一篇可定位论文；
2. Leader 将真实 PaperRef 写入单篇 `paper_read`；若缺失PDF或仓库资源，复用已有
   arXiv全文核验与GitHub README论文身份核验，先保存独立资源证据再提交Reader；
3. Leader 将 Reader 的 paperNote、source PaperRef 和明确 GitHub URL 写入
   `repository_analyze`；
4. 每阶段使用不同 Group/Session/Task，Result 按 canonical JSON 计算 SHA-256；
5. 完成阶段不会再次调用 runner；失败或远端状态不确定阶段不会自动重放；
6. 任一保存文件被修改，续跑会在发送新任务前停止；
7. contextual static S3 必须返回与本轮 paperNote 和仓库 evidence 确定性一致的
   `paperComparison`；缺失、结构变化或内容篡改均拒绝完成。该字段只证明
   标识符与词面线索对照，不代表 DeepSeek 关联分析或代码复现。

固定计划见 `examples/platform_three_agent_chain_v02.json`。本地免费验收：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_platform_three_agent.py
```

验收使用合成 Result，不连接梧桐、不调用模型、不检索、不阅读、不克隆仓库，不能作为正式平台或
科研结果证明。它只证明编排、绑定、检查点、canonical哈希和禁止自动重放的逻辑。

正式 CLI 适配三个 Group runner，并在每阶段启动前保留人工确认。首轮正式连续验收仍使用
固定 LoRA 链，不接自由 Planner；固定链通过后再接回真实 Planner。

正式适配入口为 `run_platform_three_agent.py`。默认只校验计划，不连接平台；正式执行必须显式提供
三个独立 AIC、三个已确认运行的 Partner commit，并同时指定：

```text
--execute --partners-ready --confirm-each-stage
```

每个阶段会创建新 Group，在收到 `GROUP_JOINED` 后打印 Group/Session/Task 并等待人工输入 `SEND`。
某阶段失败或不确定后，`--resume` 不会自动重发该阶段；必须先根据 Partner SQLite/formal audit
人工处理。

## 缺失资源与未发送阶段的恢复（2026-10-27）

正式Leader环境安装 `requirements-platform-resources.txt`；它在MQ依赖上仅增加现有Reader环境
已验证的 `pypdf==6.20.0`，不需要在Leader加载PaperQA或embedding。

`platform_chain_resources.py` 在S2提交前处理S1缺失资源：保留S1原始Result及canonical哈希，
从S1已提供的arXiv来源核验论文标题、PDF链接及PDF首页，再从论文摘要/来源页面提取GitHub候选，
通过README中的论文标题和稳定标识核验关联。只有来源证据成立才补充PaperRef；不猜地址。
这些来源核验会访问arXiv/GitHub，不调用Planner、Semantic Scholar或模型。

S2目录的 `resource_attempt.json` 保存资源尝试；成功时另存 `input_resolution.json`，由状态文件
记录哈希。`task.json` 为真正发送的派生任务；S1的 `result.json` 与 `record.json` 不改动。
Reader仍独立核验PDF和论文身份。S2论文笔记中的source继续传给S3。

默认 `--resume` 仍拒绝失败阶段。只有显式 `--resume-unstarted` 才允许恢复以下有证据的情况：

- 新增资源准备失败，且持久记录证明Group runner从未开始；
- 旧版Reader因 `READER_REQUIRES_ACCESSIBLE_PDF_URL` 预检失败，且记录完整证明
  `networkAttempted=false`、未写ACL/建群/邀请、`startAttempted=false`、命令数为0、远端状态确定。
- invitation 已发出，但入组超时或人工放行超时/取消；持久记录必须证明未发送 Start、命令数为0，
  且清理完成：`cleanupSucceeded=true`、`aclDeleted=true`、`groupResourcesDeleted=true`、
  `connectionClosed=true`、`cleanupErrors=[]`。缺失、失败或不确定均拒绝续跑。

续跑先验证S1检查点及旧阶段文件哈希；只重建未执行阶段。旧文件保持原路径和原内容，新的任务
与协议记录写入 `stages/paper_read/unstarted-<uuid>/`，旧阶段行保留在 `state.json.unstartedHistory`。
新runner仍生成新的Group/Session/Task并等待人工放行。已发送Start或缺少证明的阶段即使指定
该选项也拒绝恢复。清理失败时保留原记录，请平台负责人核对并处理指定旧Group的ACL、队列和交换机；
不要手工改写原始记录或声称清理成功。本入口没有补偿清理命令，完成处理后需先提供可验证的
恢复方案和证据；不能仅凭口头确认绕过校验。

已发送Start的阶段若收到Partner明确 `failed`，只有在确认完整清理、无Result、恰好发送一次
Start、`remoteStateUnknown=false` 时，才可由操作人明确使用 `--resume-terminal-failed` 创建新
Group和新Task。重试可能重新调用付费服务，必须先与Partner核对旧Task终态、Worker是否退出和已有
Result，再确认其在线。该参数不适用于 `TASK_TIMEOUT_REMOTE_STATE_UNKNOWN`：等待Partner提供
SQLite、Worker及Result证据，并先确定安全的恢复方案，不能用此参数强行重跑。

资源准备失败、缺失仓库或PDF时停止下游，不产生Reader模型费用。完整阶段恢复时不再次解析资源
或执行S1；资源检查点篡改会在新任务发送前失败。

可在不创建Group的情况下单独核验当前S1资源：

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 verify_platform_chain_resources.py `
  --source "outputs/platform-three-agent/20261027-140948-baf3ef64" --execute
```

不带 `--execute` 时只读校验S1和哈希；带上后仅访问论文/仓库资源，不重跑检索或模型。
成功生成的 `input_resolution.json` 可以通过 `--resource-proof` 传给正式编排续跑，
证据必须与同一S1和原始绑定任务匹配，且PDF缓存哈希、大小、页数和标题需再次通过校验。
例如保留原有Leader/AIC/Partner commit参数，在正式运行命令后增加：

```text
--resume outputs/platform-three-agent/20261027-140948-baf3ef64
--resume-unstarted
--resource-proof outputs/platform-resource-checks/20261027-142425-8103962b/input_resolution.json
```

2026-10-27上述独立真实资源核验已通过；这只证明保存的LoRA资源可交接，不代表S2/S3已执行。
