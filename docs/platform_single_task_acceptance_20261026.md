# ResearchMesh 三 Agent 正式平台单任务验收汇总

日期：2026-10-26

## 结论

Literature、Paper Reader、Repository 三个独立 AIC 的正式平台**单任务**验收均已通过。
每项都完成了正式 Group invitation、唯一 Start、业务 Result、Complete/completed 和清理，并由
Leader 本地记录与吴侧 Partner formal audit 共同核对。

本结论只覆盖三个独立单任务，不代表“一个新问题经正式平台自动连续执行 S1→S2→S3”已经通过。
正式连续协作仍是下一验收门槛。

## 验收矩阵

| Agent | 技能 | AIC | Result SHA-256 | 结论 |
|---|---|---|---|---|
| Literature | `literature.search` | `1.2.156.3088.1.BUPT.RECKYJ.4K8V3F.0113BD.00TL` | `059822c40f2e6b659eb688931437a96adcf659b937e0792b96e7ca2bb430f418` | PASS |
| Reader | `paper.analyze` | `1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN` | `a658ed719b3c8110c6eae091a42cd10a746e5b211420fd13d5e7b34713998640` | PASS |
| Repository | `repository.analyze` | `1.2.156.3088.1.BUPT.RECKYJ.13NQBJ.YM4WBJ.0E0X` | `f741f66984b0f50e380ff01a39b63b932f19f6b9732bf23bbe2c0580f36d6f80` | PASS |

哈希统一按以下 canonical JSON 口径计算：

```python
sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":")).encode("utf-8")).hexdigest()
```

机器可读清单见 `docs/evidence/platform_single_task_acceptance_20261026.json`。

## Literature

- Group：`rm-literature-b7212baac82c471ea39dfa61f8fb02d0`
- Session：`session-1aa2275c9a9b4f0c8775382b8a8ff48d`
- Task：`task-a3091bc91f4d4b1e9e5d4cf3cc1b2a7f`
- Partner commit：`479fefdd980b7388f529ad84f617f89002b8042e`
- Leader：协议完成、业务可用、Result 校验和清理均通过；重复 Result 为0。
- Partner：真实 Semantic Scholar HTTP=1、业务执行=1、SQLite completed、首次清理成功且无残留。

## Paper Reader

- Group：`rm-reader-14165cc0f0b04e9f9b718aa3ee54d7d6`
- Session：`session-1be9e09f11ea422e8ddc356e43114d23`
- Task：`task-aa97db4972914d8bace1ef6201f92bc4`
- Partner commit：`2564a13767eb9db7e8d10ac8ee752ece5b224008`
- Leader：PaperQA 六字段 Result 校验、Complete/completed 和清理通过；重复 Result 为0。
- Partner：Worker 正常完成，SQLite completed，无重复执行或资源残留。
- 最初哈希差异仅来自 JSON 字段排序；canonical JSON 哈希已确认一致。

## Repository

- Group：`rm-repository-5c7a20e6197c45b7bb75734ba30a32b0`
- Session：`session-12fa5ac490ea4ed386da3a3c07689e47`
- Task：`task-1df25892b76b4009bb8c941b4db86487`
- Partner commit：`2564a13767eb9db7e8d10ac8ee752ece5b224008`
- 目标：`https://github.com/pypa/sampleproject`
- 静态结果：commit `621e4974ca25ce531773def586ba3ed8e736b3fc`；12个组件、7条依赖边、16条证据。
- Partner：真实浅克隆与 CodeWiki 静态分析成功；SQLite completed；DeepSeek=0；未执行目标代码；无残留。

## 尚需归档

Leader 原始记录位于本机 `outputs/platform-*`，默认不提交大体量运行产物。吴侧 Partner formal
acceptance/journal 也在其运行环境。作品提交前需从双方各导出一次脱敏、可校验的证据包，至少包含：

- 三项 Leader `record.json`、`report.md` 和 `result.json`；
- 三项 Partner acceptance、formal journal、SQLite只读状态与清理结论；
- 文件 SHA-256 清单；
- 不包含证书、私钥、API Key、`.env`、代理凭据或完整敏感日志。

## 下一门槛：正式连续协作

建议先使用固定 LoRA 链路，不重新引入自由规划变量：

1. Literature 返回一篇 LoRA 论文及可访问 PDF/仓库资源；
2. Leader 将真实 PaperRef 动态绑定到 Reader 单篇任务；
3. Leader 将 Reader paperNote 与明确 Repository URL 绑定到 Repository 任务；
4. 三个任务分别使用独立 AIC、全新 Task ID 和持久化去重；
5. 任一阶段失败即保存已完成结果，不自动重跑收费阶段；
6. 最终生成一份跨阶段报告，并核对三侧审计、Result 哈希与全部清理状态。

固定链通过后，再将真实 Planner 接回单入口，验证“用户问题→规划→S1→S2→S3→报告”。
