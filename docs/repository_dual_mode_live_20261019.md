# Repository 真实 DeepSeek 增强验收（复用已有 Reader）

本文件保留独立副本中真实收费验收的历史记录。后续代码已选择性迁回桌面项目；当前整合状态见 [桌面项目整合说明](repository_dual_mode_integration_20261019.md)。原始 outputs 仍保留在独立验收副本，不打包进 Git，也不重新运行收费阶段。

日期：2026-10-19。本轮按用户授权只执行一次真实 Repository 流程；没有重复规划、文献检索或论文阅读。

## 结果：通过，但不是零警告

- 复用论文：LoRA: Low-Rank Adaptation of Large Language Models。
- 原 Reader AIP taskId：`task-28ca2a91-e192-49a0-aca7-d8984cf524dd`。
- 仓库：`https://github.com/microsoft/LoRA`。
- 本次 clone commit：`c4593f060e6a368d7bb5af5273b8e42810cdef90`。
- 本地官方 AIP `completed`、Leader 业务结果匹配及增强验收均通过。
- 原 Reader、检索记录、PDF 与证明文件均按 manifest 复核，执行前后保持不变。
- 真实 DeepSeek：`deepseek-flash`，调用 1 次、成功响应 1 次；没有重做收费阶段。

| 输出 | 数量 |
| --- | ---: |
| 静态组件 | 33 |
| 静态依赖边 | 24 |
| 仓库代码/文件证据 | 35 |
| 复用的论文证据 | 5 |
| 通过双侧证据校验的论文代码关联 | 3 |
| 有代码证据引用的复现障碍提示 | 5 |

概述为空，结果含 `UNSUPPORTED_LLM_CLAIM` 警告：部分模型内容未被采纳。未因此再次调用模型，也没有把无支持内容补写进报告。保留下来的三条关联涉及低秩适配与 loralib、论文实验与 README 的示例/基准说明、可训练 LoRA 参数与 checkpoint API。

障碍提示包括 PyTorch 环境边界、预训练 checkpoint 获取、loralib 安装及 checkpoint 加载设置等；其中部分是使用要求而非严重障碍。证据引用通过只表示引用能关联到输入，不证明推断绝对正确，也不等于已验证算法复现。许可证仍为 `LICENSE_NOT_VERIFIED`，论文/仓库关联仍未被独立认证。

## 费用与耗时记录

- prompt tokens：10,058。
- completion tokens：3,207。
- total tokens：13,265。
- clone：9.465 秒。
- CodeWiki 静态分析：0.535 秒。
- DeepSeek 增强：17.663 秒。
- Repository backend 总计：27.730 秒；不包含旧 Reader 的历史耗时，不是一次全新三阶段总耗时。

本轮没有另行查询价格或估算金额，以平台账户账单为准。Key 只从原项目本机 `.env` 读取，没有复制或输出密钥，也没有上传 GitHub。

## 实际记录

父记录：`outputs/aip-research-handoff-20261019-230234-fc7b6cd5/`。

- `report.md`：旧论文笔记与新仓库增强的汇总报告。
- `acceptance.json`：`passed=true`、`readerReused=true`、`readerSourceUnchanged=true`、`newModelCalls=1`，Planner/Search/Reader 新调用均为 0。
- `reader_checkpoint.json`：来源与指纹。
- `repository/acceptance.json`：`protocolCompleted=true`、`businessReady=true`、`enhancementSucceeded=true`。
- `repository/outcome.json`：AIP 消息及产品。
- `repository/backend_outcome.json`：静态结果、模型 usage、状态与警告。
- `repository/static_checkpoint.json`：可选模型阶段开始前的静态检查点。

原始 outputs 保留在本机并由 Git 忽略。

## 顺带修正与免费回归

为了从独立集成副本复用桌面原项目记录，`run_reader_repository.py` 增加 `--reader-cache` 显式指定原 PDF 缓存根目录，仍校验原文件和指纹，不猜测路径、不下载文件。增强模式汇总标题与说明也已修正，避免误显示“关闭 DeepSeek”。新增相关离线回归后：**555/555 自动测试、5 个模拟场景及本地 HTTP 检查通过**，记录为 `outputs/acceptance-20261019-230417-666838/`；该回归使用假 Provider，没有再次收费。

## 本轮没有完成的内容

没有执行目标代码、安装目标依赖、重跑 Reader、重跑检索或声称复现成功。本轮是“旧 S2 → 新 S3”，不是一次全新的 S1→S2→S3，也没有验证正式梧桐 ADP/AMQP/Group。代码仍位于独立集成副本，未回写桌面原项目、未提交、未推送 GitHub。
