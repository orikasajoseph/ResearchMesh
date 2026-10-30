# ResearchMesh 正式验收报告

验收日期：2026-10-30（Asia/Shanghai）  
验收范围：正式平台 S1 → S2 → S3 连续演示。Leader 未调用 Planner；每阶段仅执行一次；自动重试次数为 0。

## 验收结论

**PASS。** 总体状态为 `completed`，三个阶段均完成业务 Result 校验、关联离组回执确认和 MQ/ACL 清理。

| 阶段 | 状态 | Group | Session | Task | Result SHA-256 |
|---|---|---|---|---|---|
| S1 Literature | completed | `rm-literature-e495e36bf2e9446284dccbe54c29f607` | `session-65fc4981aa6e474d8aab2b6a79a7b97d` | `task-f31b36aa6f014408b5145081847fba38` | `e30fb8f614a06f18a3c940209217c756d1b75ac338f0a42695454d2b7fbc48de` |
| S2 Reader | completed | `rm-reader-e2aba6103d2242f4939fa46b5a9ff207` | `session-9130fd24dd7b48cc9c5627a4b305c4b9` | `task-f588de1f5f6e4ffda08c7001694e15b2` | `2535314805f6055332173e1a1358aad642a1908dfc295463685937b7bcbd32b9` |
| S3 Repository | completed | `rm-repository-chain-e6f096562e0b4fa0b9b79135bd640161` | `session-6a54d8ac3f4c46dd865497ca20ebd62e` | `task-88307e0012dc40feb5514f307bbee3dd` | `1e4dbf3fa60799c595ebee6aeb37c0b0a07d441d85518059ece8b6b80604cf79` |

## 运行与清理

| 阶段 | 时间（Asia/Shanghai） | 离组回执 | Leader 接收时间 | 清理 |
|---|---|---|---|---|
| S1 | 08:07:59–08:08:23 | `leave-receipt-ccfcf39e20a710c8ab1688a83663833a` | 08:08:19 | 成功 |
| S2 | 08:08:26–08:10:00 | `leave-receipt-95a86ef914506c057cb8d9f501dd11eb` | 08:09:56 | 成功 |
| S3 | 08:10:00–08:11:21 | `leave-receipt-85d1fce6db1dbc6f5eb9b44a1561fb42` | 08:11:17 | 成功 |

每个回执均关联本阶段 Group、Session 与原始 `disbandCommandId`。详见各阶段 `record.json`。

## 业务结果

- S1：返回 1 篇可定位 LoRA 论文，Schema `0.2 / literature_search`。
- S2：完成全文阅读，Schema `0.2 / paper_read`，返回 1 条 full-text paper note。
- S3：完成 `https://github.com/microsoft/LoRA` 静态分析，Schema `0.2 / repository_analyze`；实际仓库 commit 为 `c4593f060e6a368d7bb5af5273b8e42810cdef90`，结果包含 33 个组件、24 条依赖边和 35 条证据。

## 运行版本

| 组件 | 版本 |
|---|---|
| Leader | `05d6365f19c8816408be73d68c19799d7f25f6b0` |
| Literature Partner | `de3af4d9509019f312189fbc92dbcd87e32cbcf7` |
| Reader Partner | `de3af4d9509019f312189fbc92dbcd87e32cbcf7` |
| Repository 基线 | `32b24af5360b050b928e989ca78b5410a8aab2d3` |
| Repository 最终运行 commit | **待冻结：本次运行含未提交局部修改，不能将基线 SHA 冒充为最终运行提交。** |

Python：Leader/Literature/Reader 使用 Python 3.13.14；Repository runtime 使用 Python 3.12.10。关键依赖：`acps-sdk 2.2.0`、`aio-pika 10.1.1`、`aiormq 7.2.2`、`pamqp 4.0.1`、`pydantic 2.13.4`、`httpx 0.28.1`、`pypdf 6.20.0`；Repository runtime 使用 `codewiki 2.0.0`。

## 可复核证据

- [总体 state.json](../../../evidence/2026-10-30/state.json)
- [Leader 报告](../../../evidence/2026-10-30/report.md)
- S1：[record](../../../evidence/2026-10-30/stages/literature_search/record.json)，[result](../../../evidence/2026-10-30/stages/literature_search/result.json)
- S2：[record](../../../evidence/2026-10-30/stages/paper_read/record.json)，[result](../../../evidence/2026-10-30/stages/paper_read/result.json)
- S3：[record](../../../evidence/2026-10-30/stages/repository_analyze/record.json)，[result](../../../evidence/2026-10-30/stages/repository_analyze/result.json)
- Partner journals：[Literature](../../../evidence/2026-10-30/journals/literature.jsonl)，[Reader](../../../evidence/2026-10-30/journals/reader.jsonl)，[Repository](../../../evidence/2026-10-30/journals/repository.jsonl)
- Partner logs：[Literature](../../../evidence/2026-10-30/logs/literature.stdout.log)，[Reader](../../../evidence/2026-10-30/logs/reader.stdout.log)，[Repository](../../../evidence/2026-10-30/logs/repository.stdout.log)

原始 `state.json`、`report.md` 及三个阶段的 `record.json`/`result.json` 已逐个比较 SHA-256，归档副本与成功运行输出一致。

## 附录 E

| 项目 | 内容 |
|---|---|
| 源码仓库 | https://github.com/orikasajoseph/ResearchMesh |
| 本次材料归档分支 | `submission/acceptance-evidence-20261030` |
| 最终提交 commit | **待补：需先冻结并提交 Repository 运行态局部修改。** |
| 平台验收日期 | 2026-10-30，08:07:59–08:11:21（Asia/Shanghai） |
| 三 Agent 结果 | S1、S2、S3 均 completed；三次离组回执与清理均成功 |
| 视频文件名 | **待补：未归档视频。建议 `ResearchMesh_S1-S2-S3_Demo_20261030.mp4`。** |

## 通用启动命令

从仓库根目录、在已经配置正式凭据与环境变量的隔离虚拟环境中执行：

```powershell
python run_group_literature.py --serve
python run_group_reader.py --env-file <formal-env> --serve
python run_group_repository.py --env-file <formal-env> --serve
python run_platform_three_agent.py --execute --partners-ready --confirm-each-stage <required-options>
```

不得提交 `.env`、私钥、证书私密内容、Token、模型缓存或运行时缓存。

## 尚缺材料

1. Repository 最终运行变更的审查、冻结与提交 SHA。
2. 3–5 分钟演示视频文件或可访问链接。
3. 平台后台的注册、发现、互联、任务终态、回执与清理截图。
