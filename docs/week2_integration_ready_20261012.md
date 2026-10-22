# 第二周赵侧接入补齐说明

本轮只做离线开发和既有记录整理，没有付费模型、科研 API 或平台请求；没有修改张、吴的模块。

## ADP Resolver 与 AIP 调度

新入口 resolver/adp.py 使用本机官方 SDK 2.2.0 的 DiscoveryRequest/DiscoveryResponse/ACS；过滤 active 和 skills.id，与吴交付包 discovery.py 的官方请求形式对齐。不经过吴旧版 ResearchTask v0.1，避免把赵业务 v0.2 降级。

调用形态：

```python
from resolver.adp import AdpAgentResolver
from aip_orchestrator import AipOrchestrator

# 下面两项必须由吴提供/确认，不自行推测地址或证书。
# authenticated_adp_send(request_dict) -> await official_response_dict
# verified_aip_factory(candidate) -> AipAgentExecutor
resolver = AdpAgentResolver(authenticated_adp_send, verified_aip_factory,
    skill_map={"literature.search": "literature.search", "paper.analyze": "paper.analyze",
               "repository.analyze": "repository.analyze"})
run = await AipOrchestrator(resolver, task_timeout_seconds=15).run_plan(plan)
```

当前映射是待平台确认的默认值，不是注册证明。candidate 保留完整 ACS、endpoint、security；factory 负责明确传输路径、证书、正式 AIC、启用 SDK 身份绑定、连接资源管理，不静默降级 mTLS。不自动尝试第二个 Agent 或重复收费任务。

aip_executor.py 将已绑定七字段业务任务交给已验证的 AipTaskRunner，完成确认后才交回结果。aip_orchestrator.py 复用依赖分批，新增 AIC/taskId/status/receipt/evidence、失败及远程状态未知记录，保留独立成功结果和跳过失败依赖。它没有 Mock 科研兜底；调用返回中的 platformDiscoveryVerified/realResearchVerified 默认 false，真实证据要另行审核。

离线测试用官方 SDK/ASGI、合成 ADP 及科研内容，检验匹配、结构、状态机和绑定，不访问梧桐。此组件“可接入”不代表“真实 ADP 已完成”。目前没有可运行的正式平台 send/factory 配置，因此未启用实际平台入口。

## 张的公共 LLM Provider 接入

新 team_planning_model.py 实际调用仓库 common.llm.researchmesh_llm.LLMProvider 的公共 chat 方法；仍读取赵现有统一配置，支持 provider/base_url/model/api_key，不修改张的代码。Planner 继续依赖 generate，并用已有 Schema/依赖校验，不绑定具体技术基座。

张的 Provider 是同步接口，适配器使用后台线程，不阻塞事件循环；其配置 max_retries 明确为 0。不调用自带两次 JSON 重试的 generate_json，避免与 Planner 的两次校验尝试相乘。模型普通文本回复由提示词约束为 JSON，语法和语义最终由 Planner 校验；当前公共 chat 不支持 response_format 参数，不声称已用其 JSON 模式。

run_planner.py 增加 --llm-adapter team 可选接入。此前已经真实验证的异步 DeepSeek 路径仍为默认，Reader/PaperQA 没有被偷偷改模型。共享 Provider 用真实源码 + HTTP 替身做了离线验证；切换后的真实收费验收未运行。会议上应决定是否统一启用该路径，而不是把“能切换”写成全组都已切换。

```powershell
# 会收费；本轮没有执行。只规划，不运行功能 Agent。
.\.venv\Scripts\python.exe -X utf8 run_planner.py --llm-adapter team
```

同步 HTTP 被 asyncio 取消不等于外部请求停止，适配器会等待其 SDK 超时/返回后关闭，不发替代请求。默认数字配置仍用已有 LLM_TIMEOUT_SECONDS（或 DEEPSEEK_TIMEOUT_SECONDS）；张公共 Provider 本身的配置读取入口没有修改。

## 统一真实记录

assemble_aip_evidence.py 复核既有两次真实 AIP 检索/阅读记录、业务结果、关联身份与 PDF 哈希，输出 research_run.json/report.md，包括每项 subtaskId、requiredSkill、agentAic、aipTaskId、开始/结束、latency、status、warnings、error、evidence 和源文件 SHA256。

已生成 outputs/aip-evidence-20261012-201417-544808。它是历史记录汇总，不是重新执行的端到端任务；耗时只能列原任务耗时和两项之和，不能当纯网络通信时延、完整链延迟、成功率或平台访问次数。旧Partner回包sentAt未刷新为完成时间，汇总以Leader实际收到completed的event时间作为finishedAt；原始回包保留，早先200324汇总已由此版本取代。

同时生成 repositoryHandoff 输入，保留论文来源/阅读证据。此次两任务计划没有 S3，不能擅自添加执行；现有结构化 repositoryUrls 为空，需要实际仓库 URL 和张的 Wrapper。S3 绑定逻辑有离线通过记录，真实仓库功能仍未验收。

## 尚需团队提供

吴：正式 ADP 调用方法/认证配置位置、脱敏响应、已注册 skillId 映射、AIC/ACS、endpoint 选择、Direct/AMP 策略、启用身份绑定的 SDK client；证书/Key 保留在本机。

张：Repository Agent 分支/提交、安装与启动、真实成功和失败结果、证据/许可证字段；Reader 可移植安装说明和实际 Provider 切换意见。

全组：确认业务 v0.2、requiredSkill、repository_analyze 及证据字符串；明确何时合 main；不要把 Interface Contract 文档修订号、业务 schema、Wrapper v0.1 和 SDK2.2.0 当成同一个版本。
