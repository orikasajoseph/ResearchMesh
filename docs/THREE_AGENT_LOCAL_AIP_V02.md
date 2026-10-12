# ResearchMesh 三 Agent AIP 本地依赖联调 — v0.2

本交付 **不会** 修改 `run_research.py` / `run_planner.py`、Wu 原有 AIP、张的业务 Wrapper 或已经验证的 Reader/Repository Bridge。

## 范围

- 复用 Planner v0.2 的 `validate_plan()` 与 `bind_task_input()`。
- Leader 内部使用 `requiredSkill`、`dependsOn` 调度；跨 AIP 只传 7 个业务字段。
- S1（Literature）：明确标记为 **人工固定数据**，但确实经官方 SDK 的 AIP `Start→AwaitingCompletion→Complete` 进行通信；v0.2 请求转换为吴的固定 v0.1 业务信封，结果仅回填 `schemaVersion=0.2`。
- S2（Reader）：调用真实 Reader Bridge + ReaderAgent，但输入没有 `pdfUrl`，应得到 `FULL_TEXT_UNAVAILABLE`、无论文阅读条目。这是预期的**业务未完成**，不是 AIP 失败。
- S3（Repository）：调用真实 Repository Bridge + Zhang 的 RepositoryAgent，但使用 `FakeFetcher/FakeStaticProvider`，不克隆、不安装、不执行仓库，也不调用 LLM。它直接依赖 S1，**不依赖 S2**，从而在 S2 无全文时仍验证第三个 AIP 通信。
- 所有产出 `pipelineResearchSucceeded=false`。没有 ACS、ADP、MQ/AMP、平台 AIC 身份验证；不是平台互联验收。

## 安装（解压 ZIP 后，在仓库根目录）

请先确保 `aip/paper_reader_v02_bridge.py`、`aip/repository_v02_bridge.py`、`aip/wu_delivery/`、三个 Agent Wrapper 都在当前工作区，并且本机 `.venv` 已经能通过 Reader 10/10、Repository AIP 11/11 测试。

```powershell
cd D:\research\ResearchMesh
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p "test_three_agent_aip_v02.py" -v
.\.venv\Scripts\python.exe -X utf8 .\run_three_agent_aip_v02.py
```

预期 9 项测试（8 项不联网调度测试 + 1 项真实本地 ACPS SDK 往返测试），命令没有 `--live`、`--real` 或 API 请求参数。

演示结果应该包含：

```
S1 literature_search completed  protocolCompleted=True   dataUsable=True
S2 paper_read completed         protocolCompleted=True   dataUsable=False
S3 repository_analyze completed protocolCompleted=True   dataUsable=True
allProtocolCompleted: True
pipelineResearchSucceeded: False
platformConnected: False
```

这只说明 **3 次本地 AIP 握手 + 部分业务结果/绑定均按预期完成**，不是 S1→S2→S3 全部科研成功。演示 `S3.dependsOn=["S1"]`，非 S2。需要检验严格 S1→S2→S3 时，把 S3 依赖改为 `["S2"]` 并设置 `requirePaperNotes=true`；由于此演示无真实 PDF，S3 应被跳过，测试会验证这一点。

输出证据保存在 `outputs/three-agent-aip-*/report.json`，不会上传 API key；不要将测试输出作为真实科研成果。

## 明天团队联调尚需

1. 用真实 Literature 搜索结果代替 S1 假数据，不接受 `TRANSPORT_TEST_ONLY` 为科研成功。
2. 找到 **可实际下载并验证的同一论文 PDF**，再运行真实 Reader；无全文则保留失败和上游结果，不伪造来源。
3. Repository 的 `repositoryUrls` 必须为用户明确提供的 GitHub 仓库；CodeWiki 需 Python 3.12 环境和依赖。张的 Wrapper 只做静态分析、不运行仓库代码、不验证复现成功。
4. 平台注册、证书、发现、身份认证和 MQ/AMP 属于另一个验收层，当前没有进行。
