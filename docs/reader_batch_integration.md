# Reader 多篇业务对接

2026-10-11。Leader 在 reader_batch_adapter.py 中把已绑定论文的 v0.2 任务拆成单篇请求，顺序调用已验收的 reader_adapter.call_reader，再以原 subtaskId 汇总成功笔记与逐篇结果。张的 Reader 继续保持 maxPapers=1。

父任务的 maxPapers 使用现有 Leader 默认值 3；若显式给定，则按该上限校验，不截取超限输入。不支持其他阅读约束。空论文、无效引用、重复 paperId 或同一主定位地址会在调用前拒绝；实际 Reader 的每个子任务模型也全部预检后才开始调用。上游绑定仍由已有 input_binding 完成，本模块不会自动执行检索。

子任务编号为“原编号/paper-001”等，七个业务字段、不含 requiredSkill/dependsOn。逐篇超时，默认 45 秒，不自动重试；同一个 Reader 实例顺序调用，避免并发修改其 last_provider_analysis。取消向上传播，不将中断报告为已完成验收。

结果的 items 按输入顺序保留成功 paper_note，原始证据与 supports 不改写。逐篇结果、警告与静态错误码在 paperOutcomes 保存；汇总警告标记子任务编号，能够对应具体论文。

| 汇总判断 | error | businessCompleted |
|---|---|---|
| 全部成功 success | null | true |
| 部分成功 partial | PARTIAL_READER_FAILURE | false |
| 全部失败 failed | ALL_READERS_FAILED | false |

部分成功仍保留成功笔记。全部失败保持空 items，不伪造阅读结果。上述判断与 paperOutcomes 均为 Leader 内部信息；共享业务结果仍只有 schemaVersion/subtaskId/taskType/summary/items/warnings，未新增业务或 AIP 状态字段。

在项目根目录验收：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_reader_batch.py --wrapper-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\reader-review-20261011\agents\paper_reader"
```

预期 passed=true、九项 checks 为 true。每次新建 outputs/reader-batch-offline-时间戳/，包含 acceptance.json（逐篇记录、请求、Provider 调用及源码哈希）与 report.md。其他电脑替换 Wrapper 路径；不要删除该独立副本。

这项验收使用张的实际 Wrapper 与 FakeProvider，三个场景各三篇：全部成功 3/3、部分成功 2/3、全部失败 0/3。全部失败分别模拟缺 PDF、无证据异常和无法提取结构化笔记。没有读取 Key、调用模型、下载 PDF、安装 PaperQA 或执行 ADP/AIP。

仅新增独立业务适配与离线入口。run_research.py 仍只支持独立检索，LocalOrchestrator 和真实 AIP Adapter 未接入多篇结果判定；不能把本步骤写成完整科研或梧桐验收。下一步可单独准备一篇可用 PDF 与隔离依赖，验证真实全文阅读。
