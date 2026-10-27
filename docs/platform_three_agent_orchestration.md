# 正式平台三 Agent 连续编排核心

`platform_three_agent_orchestrator.py` 是正式连续协作的本地、协议无关核心。它不连接平台，也不直接
调用论文 API、PaperQA、Git、CodeWiki 或模型；正式运行时由调用方注入 Literature、Reader、
Repository 三个已验收 Group runner。

首版采用三个独立 Group 顺序串联，以复用已经通过的单任务生命周期：

1. `literature_search` 返回一篇可定位论文；
2. Leader 将真实 PaperRef 写入单篇 `paper_read`；
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

下一步由正式 CLI 适配三个 Group runner，并在每阶段启动前保留人工确认。首轮正式连续验收仍使用
固定 LoRA 链，不接自由 Planner；固定链通过后再接回真实 Planner。

正式适配入口为 `run_platform_three_agent.py`。默认只校验计划，不连接平台；正式执行必须显式提供
三个独立 AIC、三个已确认运行的 Partner commit，并同时指定：

```text
--execute --partners-ready --confirm-each-stage
```

每个阶段会创建新 Group，在收到 `GROUP_JOINED` 后打印 Group/Session/Task 并等待人工输入 `SEND`。
某阶段失败或不确定后，`--resume` 不会自动重发该阶段；必须先根据 Partner SQLite/formal audit
人工处理。
