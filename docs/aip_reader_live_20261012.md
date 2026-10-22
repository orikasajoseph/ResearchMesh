# 本地 AIP 单篇全文阅读接入

2026-10-12 状态更新：真实本机 AIP Reader 已通过，记录为 outputs/aip-reader-20261012-194858-350390；协议和业务均通过，返回1篇全文笔记及5条证据。下文预检记录保留为开发过程证据，不再表示“尚未执行”。ADP/梧桐发现、证书认证和科学结论人工核验仍未完成。

## 此次接入范围

已成功的 AIP 文献记录 → 绑定计划里的 S2 单篇阅读 → 校验已有 PDF 缓存 → 官方 AIP start/working/get/awaiting-completion/complete/completed → 全文笔记和证据。

业务任务仍为 v0.2，跨 Agent 只发送七个业务字段；requiredSkill、dependsOn 留在 Leader 内部。官方 acps-sdk 固定 2.2.0；TaskResult.status 管理协议状态，业务结果不另设 status。此入口不是 ADP 自动发现，也不是梧桐或证书互联验收。

使用两套已存在的环境，不要求重装：项目 .venv 运行 Leader/官方 AIP Partner；Partner 的单篇 Reader 子进程使用现有 rmr-1011-70db 环境，加载张的原始 Wrapper。源码没有复制进主仓库，也没有修改张的 Wrapper。其他机器使用 --reader-python 和 --reader-root 指定自己的环境及代码位置。

输入固定为已成功检索记录中的第一篇，并与已核验 arXiv PDF 证明匹配。只接受 maxPapers=1；PDF 哈希、来源 ID、标题和页数均在模型调用前复查。不重新搜索、下载或调用 Planner。不把标题搜索到的相似论文当作指定全文。

## 操作

在 ResearchMesh 根目录的 PowerShell 运行。以下本机路径已有免费预检记录：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_reader.py --source "outputs/aip-literature-20261012-190244-092760" --pdf-proof "outputs/research-live-20261012-164848-827095/pdf_resolution.json" --reader-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\reader-upstream-validation-20261011\agents\paper_reader"
```

不加 --execute 只做本机校验，不创建模型 Provider、不运行 AIP Reader 任务。核验缓存会读取本机 PDF，但不下载文件。

明确允许真实阅读时，在同一命令最后添加 **--execute**。Reader 一篇可能多次调用 DeepSeek，具体请求数及费用目前没有准确计量；不得写成“一次 API 调用”或“免费”。执行成功预期 protocolCompleted=True、businessReady=True，输出 1 篇全文笔记及证据。

## 防止重复付费和恢复边界

- 一次任务快速返回 working，后台阅读，Leader 只查询 get，不重复 start 来轮询。Reader 600 秒、Partner 660 秒、Leader 675 秒的有界等待不等于超时不收费。
- 同一已绑定任务的重复请求不重复启动 Worker。若后台已完成，返回缓存结果；尚在执行或失败则不自动再阅读。Wrapper/PaperQA 内部可能有自己的模型重试，Leader 不增加自动阅读重试。
- reader_journal.json 在阅读启动前记录 running，成功结果在 AIP 返回前原子保存。只允许明确 completed、任务一致、PDF 哈希一致且全文结果有效的旧记录加 --resume 取回；会新建输出目录，不覆盖旧记录。running/failed/interrupted 不能自动重试模型。
- Windows 退出只清理本入口 Popen 创建的 PID 及其子进程，不按程序名字关闭其他 Python。终止本地进程不能撤销已经发送给提供商的请求或费用。
- 暂时不是生产级任务持久化服务：重启不恢复内存里的原 AIP taskId。--resume 会以新的 AIP taskId 传回已保存的科研结果，不冒充恢复旧协议任务。

## 验收记录

- 用户已执行真实Reader：outputs/aip-reader-20261012-194858-350390，protocolCompleted/businessReady均为true，耗时约60.65秒；非缓存阅读结果复用，不自动模型重试。
- 检索及阅读的统一复核记录：outputs/aip-evidence-20261012-201417-544808；它组合两次已有执行，不是一轮新的自动平台任务。
- 新单入口 run_aip_research.py 见 docs/independent_completion_20261012.md，目前只离线测试，未新增收费实测。

- 23 项新增离线单元测试：官方 SDK + ASGI、后台查询、重复/并发任务、失败无重试、检查点复用、源信息/证据校验、假结果不能宣称真实完成、缓存篡改、默认不执行收费任务、进程清理。
- 实际张 Reader Wrapper + FakeProvider + 官方 AIP 离线联调 10 项通过：outputs/reader-aip-offline-20261012-193725-098571/acceptance.json。没有读取真实论文，也没有模型调用。
- 本机免费预检通过：outputs/aip-reader-20261012-193821-826248/reader_preflight.json，缓存全文哈希/标题核验、单篇任务、依赖及配置检查通过；未调用模型。
- 本机 Reader AIP 服务健康检查/退出通过：outputs/reader-aip-startup-20261012-193925-344203/acceptance.json；没有发送 AIP start，没有启动 Reader Worker。
- 全项目 382 项自动测试、0 跳过，5 个模拟场景及本地 HTTP 通过：outputs/acceptance-20261012-193958-354545。
- 此前用户真实 AIP 检索已通过：outputs/aip-literature-20261012-190244-092760，3 篇、Semantic Scholar HTTP200、协议完成及业务结果一致。

真实 Reader 执行后保存 reader_input、environment、reader_journal、outcome（完整官方消息）、acceptance、report 和本机日志。Key 不经过命令行或 AIP。缺失后台记录时模型是否启动记为未知，不能误报未收费。全文结论和 evidence.supports 的关联仍需人工对照原文；技术验收不代表科学事实已经验证。

此阶段是两次本机 AIP 调用通过保存记录串联，不是从用户输入到所有 Agent 的单入口自动化平台演示。平台发现、证书认证、跨电脑联调、Repository Agent 和最终交互界面仍需后续完成。本轮代码没有提交、推送或合 main。
