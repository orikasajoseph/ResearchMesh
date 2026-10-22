# Reader 调用适配离线验收

2026-10-11。当前 Zhao 分支为 `zhao/planner-integration`，HEAD 为 `ebd8050`。
工作区原先已有单篇、多篇和检索阅读串联的未提交实现，本轮保留这些改动，进行复核和验收，没有覆盖或提交它们。

## 本轮验证范围

- `reader_adapter.py`：剥离 Planner 内部字段、规范 PaperRef、校验论文及证据来源；缺 PDF、空结果、异常和超时不算成功。
- `reader_batch_adapter.py`：顺序拆成每次一篇 `maxPapers=1`，保留逐篇记录；全部成功、部分成功和全部失败分别判定。
- `research_reader_pipeline.py` 与 `run_research.py --with-reader`：绑定 Literature 结果，逐篇保存，显式续跑仅重试未完成论文；默认独立检索行为保留。
- 以上均为本地业务调用，不是 ADP 或 AIP 互联验收。

## Reader 源码区分

原独立副本 `reader-review-20261011` 当前存在 `codex/reader-claim-supports` 分支上的未提交修复，本轮未修改或合并它。
为了避免混淆，另建干净远端副本 `reader-upstream-validation-20261011`，验证的是张 GitHub 的 `d84924d14ecb02a8ae19779dad11f2488df6a9f5`。

本文的验收命令均使用这个干净副本。其他电脑需要替换路径，不能假定本机目录存在。

## 本轮最小修正

原单篇验收脚本要求 `supports` 中句号前的空格精确一致，导致张的原版返回具体结论时被误判。仅修改验收夹具比较：忽略标点前空格，仍要求三条完整夹具结论，不接受列表、空串、分类标签、缺失结论或残留引用 ID。

此修正没有改写 Reader 的 `supports` 业务内容，也没有修改张的源码。补充 `tests/test_reader_verification.py` 三项回归。

## 已保存记录

| 项目 | 结果 | outputs 子目录 |
|---|---|---|
| 单篇真实 Wrapper 与 FakeProvider | 5 项检查全部通过 | reader-single-offline-20261011-202926-573257 |
| 三篇阅读的成功与失败隔离 | 9 项检查全部通过 | reader-batch-offline-20261011-202823-471118 |
| 检索阅读链路及中断续跑 | 16 项检查全部通过，无外部请求尝试 | research-reader-offline-20261011-202823-300121 |
| 项目回归 | 265 项测试，0 跳过；5 个模拟场景；本地 HTTP 通过 | acceptance-20261011-202927-025000 |

最初单篇失败记录 `reader-single-offline-20261011-202820-556722` 保留，不覆盖。`git diff --check` 通过。

## 用户可以运行的无费用验收

在主项目 VS Code PowerShell 终端运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_reader_batch.py --wrapper-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\reader-upstream-validation-20261011\agents\paper_reader"
```

期望 `passed=true`，9 个 checks 均为 true。场景为全部成功 3/3、部分成功 2/3、全部失败 0/3。这些是合成数据，不是论文科研结论。

完整离线串联验收：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_research_reader.py --reader-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\reader-upstream-validation-20261011\agents\paper_reader"
```

## 尚未证明的事项

本轮没有读取 Key、使用 PaperQA、下载 PDF、调用 DeepSeek 或 Semantic Scholar，也没有运行付费 cold/warm 测试。真实全文质量、时延、模型费用和平台/AIP 链路仍需另行验证。

主项目已有隔离 Reader 运行时的路径记录，但本轮未验证其依赖、缓存或真实运行能力，不能据此声称真实阅读已经通过。下一步先检查运行时及一篇可用 PDF，再在明确费用范围后执行一次真实阅读。

本轮未提交、推送或合并代码，main 没有改变。共享前需区分赵工作区未提交实现与张远端源码，不直接把原独立副本的本地修复称为 GitHub 已更新。
