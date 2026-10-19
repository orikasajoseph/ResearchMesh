# 本地 Literature → Reader 入口

`run_research.py --with-reader` 显式启用检索后全文阅读。默认不带此标志时，保留原来的独立检索行为。支持一个独立 `literature_search`，随后一个直接依赖它的 `paper_read`；其他计划整体验证后拒绝，不先执行其中一部分。不修改 Planner、共享 schema、Literature Wrapper 或 AIP。

## 当前实现

- 使用现有 `bind_task_input` 绑定：显式 papers 优先，否则按检索结果原顺序、去重及 `maxPapers` 选篇。报告记录返回数和选中数，不声称智能筛选。
- Reader 批次拆成单篇调用，Partner 每项 `maxPapers=1`，移除内部 `requiredSkill/dependsOn`。
- 无 pdfUrl 不推断全文地址。某篇失败后保留其他成功笔记；全部失败返回空笔记。部分失败不算整批完成。
- 阅读开始前保存计划、检索结果及绑定任务。每篇完成立即保存，取消向上传播，并保留已经完成的论文。
- 显式 `--resume` 跳过 Planner，复用校验过的检索和成功 Reader 记录；失败或未完成项再次尝试。检查点与当前计划/绑定任务须一致，结果重新校验论文来源、证据和子任务 ID。不会自动用其他历史运行填补失败。
- 续跑建立新目录，保留原记录。`pipeline.json` 使用单文件原子替换，最后更新，作为续跑权威记录；其他 JSON 是可查看的阶段文件。

## 离线验收（无费用）

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_research_reader.py --reader-root "agents/paper_reader"
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

验收程序加载实际 Literature/Reader Wrapper，注入合成 Provider；不读取 Key、不下载 PDF、不使用 PaperQA，不发真实模型/检索请求。输出显式标注合成数据，覆盖全部成功、部分失败、全部失败、中断、两种续跑及已完成任务的零重复调用。

## 真实入口（显式运行才产生费用）

需要已准备好的 Reader 隔离 Python、PaperQA/embedding 依赖和完整离线缓存，以及主项目 `.env` 中 Semantic Scholar Key 和 `DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL/DEEPSEEK_MODEL`。程序不安装依赖或改 conda；不会自动联网下载 embedding。Key 从主项目配置显式读取，环境变量可覆盖，不读取独立 Wrapper 仓库的 `.env`。

本机已验收环境的示例命令：

```powershell
cd "<PROJECT_ROOT>"
& "<READER_PYTHON>" -X utf8 run_research.py --query "检索与主题相关的论文并阅读全文一篇，不分析代码仓库" --with-reader --reader-root "agents/paper_reader" --reader-timeout 600
```

Planner 最多两次生成请求；Reader 一篇可能产生多次模型请求。600 秒是每篇超时，不是调用次数或费用上限。Leader 不对 Reader 自动重试；底层库行为按其配置。超时或取消无法保证提供商撤回已接受的请求，显式续跑失败项可能再次计费。本次工程验收没有运行此付费命令。

续跑使用同一 Python、相同 Wrapper 和缓存，传入 `--resume "上次运行目录" --with-reader --reader-root "原 Reader 根目录"`。控制台会提供包含运行时和 Wrapper 参数的命令。只有 `plan.json` 而没有 `pipeline.json` 时，仅复用计划，会重新检索；已有完整检查点损坏时拒绝续跑，不悄悄忽略它。

## 记录与边界

新增阶段文件：`reader_task.json`、`reader_progress.json`、`reader_outcome.json`。报告保留成功笔记、evidence 的具体 supports 字符串、页码、来源、警告和失败原因。中断报告区分已保存笔记数与整批完成。

这只完成本地业务串联。AIP Partner 业务接入、平台注册/自动发现/跨端访问及后台佐证尚待验证。当前实际 Reader 源码包括本地 `codex/reader-claim-supports` 修复，尚未提交或推送；不能将 GitHub 原提交等同于已修复版本。平台需重建其运行环境，不能直接上传 Windows 虚拟环境。
