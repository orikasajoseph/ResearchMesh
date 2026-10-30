# ResearchMesh 终版演示视频脚本（3–5 分钟）

本脚本对应 2026-10-30 的真实成功链。录制时不得展示私钥、Token、完整环境文件或证书私密内容。

## 0:00–0:25｜标题与范围

画面：打开[正式验收报告](final_acceptance_report.md)，展示 `completed` 和三阶段表格。

解说：本视频展示正式平台上的 S1、S2、S3 连续协作。Leader 不调用 Planner，每阶段只执行一次，并在离组回执和清理确认后进入下一阶段。

## 0:25–0:55｜平台注册与在线状态

画面：平台后台的四个 Agent 注册状态与 AIC；随后展示三个 Partner [启动日志](../../../evidence/2026-10-30/logs)。

解说：Leader、Literature、Reader、Repository 已注册并在线。Partner 日志确认 AMQPS 已连接、Inbox 正在监听，并持续上报 Heartbeat。

待录制：平台后台注册状态截图。

## 0:55–1:20｜Agent 发现与互联

画面：平台后台的发现或 Group 页面；或展示 S1 [record](../../../evidence/2026-10-30/stages/literature_search/record.json) 中 invitation 与 `GROUP_JOINED_CONFIRMED` 事件。

解说：Leader 指定 Partner、授权 Group、发布 invitation，并只在 `GROUP_JOINED_CONFIRMED` 后发送 Start。

## 1:20–1:55｜S1 Literature

画面：S1 [result](../../../evidence/2026-10-30/stages/literature_search/result.json) 与 [record](../../../evidence/2026-10-30/stages/literature_search/record.json)。

解说：S1 使用全新 Group `rm-literature-e495e36bf2e9446284dccbe54c29f607` 检索 LoRA 论文。Result 校验通过后，Leader 收到关联离组回执并完成清理。

## 1:55–2:35｜S2 Reader

画面：S2 [result](../../../evidence/2026-10-30/stages/paper_read/result.json) 与 [record](../../../evidence/2026-10-30/stages/paper_read/record.json)。

解说：S2 使用全新 Group `rm-reader-e2aba6103d2242f4939fa46b5a9ff207` 完成全文阅读并返回结构化 paper note。Reader 的回执被 Leader 在窗口内确认，随后 MQ/ACL 清理成功。

## 2:35–3:20｜S3 Repository

画面：S3 [result](../../../evidence/2026-10-30/stages/repository_analyze/result.json)。

解说：S3 使用全新 Group `rm-repository-chain-e6f096562e0b4fa0b9b79135bd640161`，静态分析论文明确给出的 microsoft/LoRA 仓库，不执行目标代码。实际分析 commit 是 `c4593f060e6a368d7bb5af5273b8e42810cdef90`，输出 33 个组件、24 条依赖边和 35 条证据。

## 3:20–3:50｜完成与清理

画面：打开[总体状态](../../../evidence/2026-10-30/state.json)，展示三个阶段 `completed`；再展示三个 [Partner journal](../../../evidence/2026-10-30/journals)。

解说：三个阶段由不同 Group、Session 和 Task 执行。每阶段均完成业务 Result、关联离组回执与清理，最终状态为 completed。

待录制：平台后台任务终态、Group 清理与审计截图。

## 3:50–4:10｜结尾

画面：回到验收报告中的证据链接。

解说：所有可公开复核的验收记录、结果、journals 和启动日志已随仓库归档，评委可通过相对链接直接查阅。
