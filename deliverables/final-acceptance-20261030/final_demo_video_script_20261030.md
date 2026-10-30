# ResearchMesh 最终演示视频脚本（3–5分钟）

## 0:00–0:25 范围

展示本目录及最终报告，说明本次演示覆盖显式AIC三阶段业务链、Kafka单次heartbeat和Discovery对照；不宣称平台Consumer或自动Discovery已通过。

## 0:25–1:00 身份与架构

展示最终报告中的三Agent AIC、Skill与职责表，说明Leader顺序编排 Literature→Reader→Repository。

## 1:00–2:30 S1/S2/S3

依次打开 `evidence/aip-three-stage/stages/*/result.json` 与 `state.json`，展示三阶段 `completed`、每阶段 attempts=1、automaticRetries=0及Result SHA-256。强调Repository执行静态分析，不执行目标代码。

## 2:30–3:10 Kafka Heartbeat

展示 `evidence/heartbeat/`：Literature offset 41976683、Reader 41985932、Repository 41985943。说明三者单次Broker ACK与指定offset回读一致；Literature原始ACK输出缺失限制必须口播。

## 3:10–3:50 Discovery

展示公开Agent相同请求在四种证书下均可发现；再展示三个正式AIC的9次aic-only查询均HTTP 200但ACS/alive为空。结论为平台可见性/索引链待核验，不称为平台Bug。

## 3:50–4:20 收口

展示 `MANIFEST.md`、`SHA256SUMS.txt` 和平台待核验清单。最终口播：显式AIC业务链PASS，Partner→Kafka Broker PASS；平台Consumer、Registry alive、DSP/Discovery尚无完成证据。
