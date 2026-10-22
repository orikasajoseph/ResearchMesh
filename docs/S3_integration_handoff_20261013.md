# 赵分支修复交付与 S3 接线基线

交付分支：zhao/planner-integration。本文件随本轮修复提交；完整 hash 以包含本文件的实际提交及推送确认消息为准。本轮不合 main，不修改张的分支。

## 本轮范围

- 包含此前438项测试版本的 INVALID_AIP_RECORD_DIRECTORY 修复，并一并修复 Reader Partner、Reader Worker 的嵌套记录目录限制。新入口的 literature/reader 阶段目录合法，outputs 根、缓存和越界路径仍拒绝。
- 新增检索传输异常分类、明确临时网络错误的有界重试；保留429退避，不叠加 SDK/Provider 重试，不重跑 Planner 或自动重试收费阅读。
- 终端展示脱敏重试进度与具体失败原因；修正报告来源表述、保存目录时间戳碰撞。
- 当前最终回归为451项测试、0跳过、5个模拟场景及本地HTTP检查通过。覆盖测试不是451次真实模型调用。
- 用户已在 VS Code 跑通检索→全文阅读；新LoRA需求也完成真实规划、AIP检索、PDF核验、AIP单篇阅读和报告。科学结论待人工复核；不证明梧桐ADP、TLS身份、跨电脑或Repository已验收。

docs/demo_fix_20261013.md 和 docs/semantic_network_recovery_20261013.md 中的“未自动提交或推送”描述的是开发/验证阶段；本次版本交付以Git远端提交为准。完整outputs、PDF、Key、证书、虚拟环境和缓存不在提交范围。

## 给张的接线边界

Repository来源按张提供的 zhang/repository-agent@be99b31b3c8411ea88a96d08b0337d6049882046；本轮没有自动合入或验证该版本。

先在独立integration分支做本机官方AIP，固定 https://github.com/pypa/sampleproject 。本地能力解析不能标为正式梧桐ADP发现。以后再接吴的认证及发现传输。

保持业务schemaVersion=0.2，taskType=repository_analyze，requiredSkill=repository.analyze，outputSchema=code_records.v1。Partner只接收七个业务字段；requiredSkill、dependsOn属于Leader内部。首版明确仓库URL，executeCode=false、allowRepositorySearch=false；首次独立仓库分析不要求论文笔记，随后再验证真实Reader笔记绑定。

现有通用接口：resolver/base.py 的 AgentResolver、AgentHandle；resolver/adp.py 的 AdpAgentResolver；aip_executor.py 的 AipAgentExecutor；aip_orchestrator.py 的 AipOrchestrator；aip_lifecycle.py 的 AipTaskRunner、sdk_sender。

接线时必须显式扩展 aip_business_partner.py 中的任务/模式白名单、健康检查，以及 run_aip_literature.py 的 invoke_business 任务类型白名单。它们目前仅支持literature_search和paper_read，不能因为通用Schema有S3就宣称本地Partner已支持S3；不直接移除校验。

参考现有 Literature/Reader的 executor注入、独立后台任务和协议完成确认。验收需经过awaiting-completion→complete→completed，并保存真实结果和诊断；静态分析成功不能称作代码复现成功。
