# ResearchMesh 最终验收报告（2026-10-30）

## 1. 项目与架构

ResearchMesh 由 Leader 顺序编排 Literature、Reader、Repository 三个正式 Agent。业务链使用显式 AIC、独立 Group/Session/Task 和 AIP Result；可观测链使用 AMP heartbeat 写入 Kafka `amp.heartbeat`。Discovery 使用 HTTPS mTLS 查询平台 DSP。

## 2. 正式身份与职责

| Agent | 正式 AIC | 主要 Skill | 职责 |
|---|---|---|---|
| Literature | `1.2.156.3088.1.BUPT.RECKYJ.4K8V3F.0113BD.00TL` | `literature.search` | 文献检索与候选论文输出 |
| Reader | `1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN` | `paper.analyze` | 全文阅读与结构化 paper note |
| Repository | `1.2.156.3088.1.BUPT.RECKYJ.13NQBJ.YM4WBJ.0E0X` | `repository.analyze` | 受控静态仓库分析，不执行目标代码 |

## 3. S1/S2/S3 正式业务验收

| 阶段 | 结果 | Attempts | Result SHA-256 |
|---|---|---:|---|
| S1 Literature | PASS / completed | 1 | `e30fb8f614a06f18a3c940209217c756d1b75ac338f0a42695454d2b7fbc48de` |
| S2 Reader | PASS / completed | 1 | `2535314805f6055332173e1a1358aad642a1908dfc295463685937b7bcbd32b9` |
| S3 Repository | PASS / completed | 1 | `1e4dbf3fa60799c595ebee6aeb37c0b0a07d441d85518059ece8b6b80604cf79` |

三阶段连续编排最终 `state=completed`、`formalPlatformExecuted=true`、`automaticRetries=0`。每阶段均有 Result、record、journal、日志以及清理证据。完整包见 [`evidence/aip-three-stage/`](evidence/aip-three-stage/)。

显式 AIC 三阶段业务链路已 PASS。历史诊断报告中的 `NOT_READY`、`REMOTE_STATE_UNKNOWN` 和限制仍按原文保留，它们描述较早的受控验证阶段，不被后续 PASS 反向改写。

## 4. Kafka Heartbeat 验收

统一配置：Broker `wt.ioa.pub:19092`，Topic `amp.heartbeat`，Protocol `PLAINTEXT`。

| Agent | Broker ACK | Partition / Offset | 指定 offset 无组回读 | 证据状态 |
|---|---|---|---|---|
| Literature | PASS | `0 / 41976683` | Payload 一致 | 原始 JSONL已纳入；原始 ACK 输出未定位，元数据来源与限制已注明 |
| Reader | PASS | `0 / 41985932` | Payload 一致 | 原始脱敏 JSON完整 |
| Repository | PASS | `0 / 41985943` | Payload 一致 | 原始脱敏 JSON完整 |

这证明 Partner → Kafka Broker 单次发送与独立回读 PASS；不证明平台 Consumer 已消费，也不证明 Registry alive 或 DSP/Discovery 已更新。证据见 [`evidence/heartbeat/`](evidence/heartbeat/)。

## 5. Leader mTLS / Discovery 对照

- 同一公开 Agent、同一请求下，Leader 与 Literature/Reader/Repository 四种证书均获得非空 ACS、agentSkills、aliveMap，排除一般客户端身份权限差异。
- 三个正式 AIC 在 `aic-only` 精确查询下，三种 Partner 证书共 9 次均为 HTTP 200、TLS验证成功、`routes.status=ok`，但 ACS、agentSkills、aliveMap 全为空。
- skill 条件已被排除为主因；自动 Discovery 未通过。

严谨结论：问题集中在正式 Agent 对象的平台 Consumer、AIC/alive 关联、DSP快照/索引或可见性链路；现有证据不足以称为已确认的平台 Bug。证据见 [`evidence/discovery/`](evidence/discovery/)。

## 6. Reader 与 Repository 技术成果

Reader S2 正式验收包含单次真实模型 HTTP 请求、全文 paper note、Result canonical hash、Leader确认、协议终态和清理证据。Repository 最终 S3 对 `microsoft/LoRA` 完成受控静态分析；历史受控验证与传输诊断中的失败和限制保留在 [`reports/`](reports/)。

## 7. 已完成与平台待核验

已完成：显式 AIC 三阶段业务链 PASS；三 Partner 单次 Kafka Producer→Broker PASS；相同请求的 mTLS身份对照完成；Kafka-backed heartbeat代码与三入口接线测试通过。

平台待核验：Kafka Consumer 实际消费；heartbeat AIC 与 Registry alive关联；DSP对象级快照/索引；Discovery最终可见性。不得把这些项目描述为 PASS。

## 8. 代码版本

- 交付分支：`wu/final-delivery-20261030`
- Kafka heartbeat实现历史：`340392b`、`336f6c4`、`f6a2c0f`
- 依赖及三入口Mock验收提交：`0ed081e`
- 协作基线均在历史中：`origin/zhao/planner-integration@58ebbbf`、`origin/shared/s2-s3-leader-cleanup-618489f@32b24af`

## 9. 演示与证据索引

演示顺序：身份与架构 → S1/S2/S3状态及Result → Kafka三Agent矩阵 → Discovery公开正向对照与正式AIC空结果 → 平台待核验边界。演示脚本见 [`final_demo_video_script_20261030.md`](final_demo_video_script_20261030.md)，文件与SHA清单见 [`MANIFEST.md`](MANIFEST.md) 和 [`SHA256SUMS.txt`](SHA256SUMS.txt)。
