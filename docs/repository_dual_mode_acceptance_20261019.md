# Repository 双模式本次验收

本文件记录初次免费验收。后续用户另行授权的真实 DeepSeek 增强已通过，见 [真实增强验收](repository_dual_mode_live_20261019.md)；两轮范围与记录分开保留。

本文件后文的“未回写/未提交”指验收时状态。后续桌面项目整合见 [整合说明](repository_dual_mode_integration_20261019.md)，不要把副本的历史测试数量当作桌面项目当前总数。

日期：2026-10-19。基线：赵 `4a39ba29ab5ebbf16aac4a3443c929dc805518d0`。选择性引入张 `22863c04a8afab8457df3c1a82e46da2f6217e0c` 的三个 Repository Wrapper 文件，没有整体 merge。

## 实际结果

| 验收 | 结果 | 真实边界 |
| --- | --- | --- |
| 双模式新增自动测试 | 24/24 PASS | 真实 Wrapper/Enricher/AIP SDK，假 Provider；无外网 API |
| 整个集成副本自动测试 | 554/554 PASS，0 skip | 基于已提交基线；没有把桌面原目录未提交的 Group 邀请测试复制进来 |
| 五个模拟场景与本地 HTTP | PASS | 不代表真实科研或平台互联 |
| 真实 CodeWiki 本地 smoke | 10/10 checks PASS | 分析临时本地微型仓库，增强 Provider 为假；真实模型调用 0 |
| 静态模式 CLI 离线预检 | PASS | 不 clone、不发 Task、不读 Key |
| 增强模式 CLI 离线预检 | PASS | 验证 CodeWiki 与共享 LLM 依赖；不验证真实 Key，不收费 |

整体验收记录：`outputs/acceptance-20261019-225758-012165/acceptance.json`。

真实 CodeWiki smoke：`outputs/repository-dual-smoke-20261019-225757-ecbe87d0/acceptance.json`。

CLI 静态预检：`outputs/aip-repository-20261019-225608-70881bd6/`。

CLI 增强预检：`outputs/aip-repository-20261019-225610-6630f2e3/`。

这些原始记录保留在被 Git 忽略的本机 outputs，不随代码上传。早先中间 smoke 曾因把正常新增的 evidence.supports 模型说明也要求逐字不变而失败；已将断言收窄为结构、依赖、入口、证据 ID/定位/原始文本不变，最终记录如上。没有删除旧失败记录，也未把失败冒充通过。

## 未进行的验证

- 没有读取或复制原项目 `.env`，没有调用真实 DeepSeek、Semantic Scholar、Reader 或梧桐接口。
- 没有重新 clone 公开科研仓库来验证真实增强；静态 smoke 使用本地临时测试文件。
- 真实 DeepSeek 模型兼容性、额度、延迟、usage、推断质量仍需显式收费测试与人工核对。
- 未接任意自然语言的全新三阶段链，也未解决正式 Group ACL 的证书识别问题。
- 桌面原项目保持原 HEAD 与未提交的 Group 邀请代码不变；未回写、未提交、未推送本次集成代码。

## 下一步

先审阅独立集成副本与运行说明。需要真实增强验收时，显式指定本机 `.env` 与 `--analysis-mode enhanced --execute`；优先复用已有 Reader 结果，避免重复收费阅读。通过后再把限定文件移入赵分支，保留原目录未提交的 Group 邀请改动，单独提交并按授权推送，不整体合并 main。
