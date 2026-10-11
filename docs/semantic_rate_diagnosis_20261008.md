# Semantic Scholar 定向限流诊断与修复（2026-10-08）

## 范围与未改变的契约

保留 relevance `/graph/v1/paper/search`，skill 仍为 `literature.search`。
没有修改 literature_search 业务字段、PaperRef、paper_records.v1、Planner/AIP Schema、安装的上游 SDK 文件。
新增字段全部在 diagnostics/log 中，未加入 LiteratureAgent 的 PaperRecordsResponse。
本次允许对 Wrapper Provider 的客户端创建路径做定向改动，接入共享 HTTP gate；不是 Agent 功能重构。
SDK 版本继续为 semanticscholar 0.12.0；接入层仍设置 Provider `max_retries=0`，SDK 内层重试关闭。

## 实现位置

- `semantic_http.py`：请求级诊断、进程共享 rate gate、SDK requester 实例适配。
- `agents/literature/researchmesh_literature/provider.py`：默认真实客户端创建时使用上述适配；离线注入的测试替身不联网。
- `run_literature.py`：诊断加入接入层 outcome，不加入原业务结果。
- `search_recovery.py`：读取真实 Retry-After；无有效头时 2/4/8 秒，加 0.1—0.3 秒 jitter，最多三次恢复。
- `scripts/diagnose_semantic_scholar_rate_limit.py`：独立新进程等待两秒，仅搜索一次 world model、limit=1，无模型、无重试。

gate 紧贴 HTTP send 前，所有当前真实 Provider 客户端端点共用同一状态，开始时间最小间隔 1.2 秒。
asyncio.Lock 串行同一 event loop；一个极短 threading.Lock 保护跨线程/loop 的进程状态，不在它内部 sleep 或等待网络。不是两套独立限速器。
HTTP 请求期间保持串行保护，取消时 finally 释放；SDK 分页若后续实际发生请求，也走同一个 requester。
本轮 Provider 仅读取 results.items，不遍历下一页；总数估计不等于实际下载量。

每个真实 HTTP 事件记录 requestSequence、endpoint、起止时间、状态码、来源、可用 subtaskId、instanceId、Key 是否配置/附带、gate 等待、前次/实际请求时间。
只保留白名单 Retry-After/rate-limit 响应头和脱敏服务端错误说明，不记录完整请求头、Authorization、Cookie、Key 或 traceback。
SDK/client 的实际 Request.headers 上检查 `x-api-key` 是否非空，而不是仅判断 .env 是否存在。

Retry-After 支持秒数及 HTTP-date；有效头优先，另加 jitter。
服务端建议等待超过 120 秒时停止本轮并保留计划，不缩短服务端要求、也不继续盲目重试。
无效或不存在的头才使用 fallback；记录 retryAfterHeader、selectedWaitSeconds、waitSource、jitterSeconds。
超过 30 秒的等待分段，可按 Ctrl+C 取消。重试始终只执行检索，Planner 不重复调用。

## 测试 A：独立冷启动

记录：`outputs/semantic-cold-20261008-192807-518232/diagnostics.json`。

- 独立进程，只创建一个 Provider，先等待两秒。
- apiKeyConfigured=true，apiKeyAttached=true。
- 只有一条 HTTP 记录，/graph/v1/paper/search，query=world model，limit=1。
- 首次 httpStatus=200，providerReturned=1，retryCount=0。
- 单次 Provider 调用 durationMs=1621.848，HTTP 网络部分 durationMs=1578.0。
- 此响应没有 Retry-After 或白名单 rate-limit 头。

## 测试 B：真实 Planner 完整流程

问题：只检索 2024—2026 年世界模型的论文，最多 3 篇，不阅读全文、不分析代码。
记录：`outputs/research-live-20261008-192828-881932/`。

- Planner 仅一个 literature_search，模型调用一次，plannerLatencyMs=1747。
- 请求保持 query=world model、year=2024:2026、limit=3；年份区间按 SDK 官方格式在同一次请求发送。
- 实际 HTTP 共两次：requestSequence=1 返回 429，sequence=2 返回 200。
- 两次都实际带 x-api-key；同一个非敏感 instanceId、subtaskId=S1、requestSource=run_research。
- 429 没有 Retry-After，也没有白名单 rate-limit 头。服务端说明为通用“请求过多，请稍后重试或申请更高额度”，没有提供具体配额原因。
- retryCount=1；waitSource=fallback；selectedWaitSeconds=2.164，其中 jitter=0.164。
- totalWaitSeconds=2.164；最后成功 Provider durationMs=1198.890；HTTP durationMs=1172.0。
- 整体 workflow latency=5677 ms，providerReturned=3。
- 当前进程的 httpRequests 和 processHttpRequests 一致，只有这两次相同端点/参数的调用，没有观测到额外 detail/batch/health/enrichment 查询或隐藏并发。

原历史样本约 75.283 秒，其中等待 70 秒；本次新样本约 5.677 秒。
这是两次不同时间的实际样本，不能推导成稳定性能保证，也不能证明仅靠 gate 已消除服务端 429。

## 结论与共享 Key 风险

已证明：Key 实际进入 SDK/client 和 HTTP request；冷启动首次成功；完整流程仍首次 429，但无需 10/20/40 固定等待即可本轮恢复。
未证明：Key 的独立账户认证/专属额度是否生效，以及发生 429 时其他机器的真实请求量。
用户已确认“共用 Key，可能同时测试”。这是重要风险因素，但不是此次 429 唯一根因的证明。
本机新进程不能查看其他机器流量；进程 gate 也不能跨机器或跨进程协调。

Semantic Scholar 官方说明 Key 的初始速率额度跨端点计算：[官方教程](https://webflow.semanticscholar.org/product/api/tutorial)。
正式联调建议 Planner/AIP → 唯一部署的 Literature Agent → Semantic Scholar，其他成员通过该 Agent 调用，不在多台电脑同时以同一个 Key 做高频直连测试。
下一次根因验证应协调一个短测试窗口，让其他使用者停止同 Key 请求，单独运行冷启动脚本；不要通过轮换 Key 或缩短服务端 Retry-After 绕过限制。

Bulk 仅留 backlog：未来可评估 searchMode=relevance|bulk，本轮未切换。

## 离线回归

199 项项目自动测试（零跳过）、5 个模拟场景、本地 HTTP 检查通过。
另有张的 2 项 Literature 测试、2 项公共 LLM 测试及交付包兼容检查通过。
记录：`outputs/acceptance-20261008-193043-777173`、`outputs/zhang-delivery-20261008-193049-277336`。
覆盖 Key 附带/脱敏、HTTP 429 响应头、跨 Provider/endpoint/线程 gate、取消释放、Retry-After 数值和日期优先、jitter、无头 fallback、重试上限和模型不重复调用。
离线测试不联网、不读取真实 Key；上述 A/B 是单独明确执行的真实请求。
