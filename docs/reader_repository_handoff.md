# 已完成的论文阅读 → 新 Repository 本地 AIP 对照

2026-10-15。实现阶段的验证说明；当前发布交接见 `docs/team_handoff_20261015.md`，版本以 Git 提交为准。

## 本次完成的范围

复核此前真实本地 AIP 的检索/阅读/PDF记录，复用完成的单篇 Reader 笔记，
用现有 `bind_task_input` 将其绑定给 S3，然后通过现有 Leader、LocalAipResolver、
官方 AIP 和真实 Repository Wrapper 执行指定仓库静态分析。

原生张 Wrapper 只接受 repositoryUrls，因此没有直接把不支持的 paperNotes 丢给它：
新增复合适配层 `repository_paper_context.py` 消费原论文笔记，
先调用原样的 CodeWiki Wrapper 获取静态证据，再逐项定位笔记与仓库摘录的词面线索和论文标识符。
数据同时包含原笔记、笔记哈希、原论文证据定位及匹配的仓库 evidence ID。
改变输入笔记会改变对照产物；仅回显一个哈希而没有实际对照不能通过验收。

**这是上下文传递与确定性证据对照，不是语义层面的复现难度分析。**
同词、同标识符出现不证明方法实现正确、实验一致、作者官方仓库或论文性能成立。
没有匹配也不证明仓库缺少某项功能；只检查了 Wrapper 提取的证据范围。
没有新增 DeepSeek 调用，也没有运行目标仓库代码、安装目标依赖、下载权重或数据集。

## 本机演示命令

在 VS Code 的 ResearchMesh 项目终端运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_reader_repository.py --reader-source outputs/aip-research-20261013-142647-675300 --repository https://github.com/microsoft/LoRA --execute
```

这里复用本机此前真实成功的 LoRA 全文笔记；仓库 URL 明确指定，
没有自动搜索，也不从 Reader 的自由文本中猜仓库地址。
该仓库 README 明确引用论文 arXiv:2106.09685；本轮还在真实 clone 的 README 静态证据中再次定位到该标识符。
这只是“仓库摘录引用了论文”的证据，不单独认证作者身份或完整实现关系。

`--reader-source` 可以是原检索→阅读父目录，也可以直接是其 reader 子目录。
其他电脑必须提供自己有效的源记录目录，不要复制本机路径当通用配置。
省略 `--execute` 仅做无网络预检，不发送新 AIP 任务、不 clone。
`--wrapper-root` 和 `--repository-python` 可显式指定，但通常保留默认即可。

成功后打开 `outputs/aip-research-handoff-*/report.md`，Ctrl+Shift+V 预览。
其中包含旧论文笔记、新仓库结构/依赖/声明命令、两侧出处及待人工检查的对照项。
不要执行报告中列出的命令；它们只是从源码提取的声明。

## 任务与结果仍使用 v0.2

- 新任务仍是 `repository_analyze` / `repository.analyze` / `code_records.v1`。
- Partner 仍只收到七个顶层业务字段，paperNotes 放在既有的 input 中。
- 未改共享 Pydantic 模型、导出 Schema、Planner 提示词、官方 AIP 协议或张的八个核心源码文件。
- 现有 `repository_adapter.to_repository_request` 仍明确拒绝原生 Wrapper 不支持的论文上下文。
- 新复合适配层先严格校验单篇真实 fulltext、非 Mock、有证据、来源一致、大小不超过 160 KB。
- 对照结果是共享结果 `items[].data` 中的可选扩展 `paperComparison`，不增加顶层通信字段或状态。
  该扩展目前属于赵侧本地适配产物；团队合并前应让张/吴知晓，不视为全组已经正式冻结。

## 为什么没有重跑 Reader

记录保存原来 S2 的 AIP taskId 与来源目录。`handoff.json` 保留 S3 的原依赖、绑定前后输入。
S2 已复核完成，所以只将绑定好的 S3 作为新独立任务提交，不伪造一次新的 S2 调用。
复核原 AIP products 与保存的 Reader 结果一致、原 PDF 校验、检索→阅读关联、源文件哈希。
执行前后重新核对源记录哈希；原文件不会被修改，发生变动会停止验收。

本次运行不是三个 Agent 一次全新端到端运行：

- `readerReused=true`
- `newReaderCalls=0`，`newModelCalls=0`
- `paperContextCompared=true`
- `semanticReproductionAnalysis=false`，`reproductionVerified=false`
- `freshThreeAgentEndToEnd=false`，`platformDiscoveryUsed=false`

单独 `protocolCompleted=true` 不够，静态结果与 AIP 产物、原输入和完整对照产物均一致才算本轮通过。

## 本轮验证

- 502 项自动测试通过，0 跳过；5 个 Mock 场景与本地 HTTP 检查通过。
- 新增 17 项测试覆盖：上下文影响输出、Mock/摘要/缺证据拒绝、来源/哈希/产品篡改拒绝、
  七字段 AIP、词面匹配不冒充语义验证、失败与安全约束。
- 真实 LoRA 仓库：33 个组件、24 条依赖边、35 条证据，原 README 证据中出现论文标识符。
- 真实后端约 10.82 秒（clone 约 10.31 秒、静态分析约 0.45 秒），会受网络和仓库规模影响。
- 真实记录：`outputs/aip-research-handoff-20261015-183755-fcad3f6b`。
- 全量回归：`outputs/acceptance-20261015-184042-087049`。

## 还没完成

带证据的语义实现/实验对应关系、真正的复现难度评估，需要单独设计和验证，
如果使用 DeepSeek，应先明确请求次数、费用与失败降级；不能用当前词面匹配代替。
新自然语言规划→检索→阅读→仓库的一次全新三 Agent 流程，以及梧桐 ADP/在线/MQ 仍未验收。
