# v0.2 开发分支交接

> 2026-10-04 23:27：Semantic Scholar Key 已在赵侧配置，已有计划 → 张 Wrapper → 真实 Semantic Scholar → 统一结果／报告已通过，返回 5 篇 AlphaEvolve 相关检索记录。未调用 LLM、ADP 或 AIP，尚非平台链路。见 [实测证据](evidence/literature_live_20261004.json)；下文 Key 待配置的描述已过时。

> 2026-10-04 后续更新：已收到并适配张的 Literature Wrapper，完成交付样例回放及实际 Wrapper 的离线调用检查；自动测试增至 152 项。v0.2 六类真实 DeepSeek 规划回归已自动通过，待用户人工确认。Semantic Scholar Key 尚未在赵侧配置，真实 AIP/ADP 未接通。详见 [文献接入说明](literature_integration.md)。以下早期计数与“未收到 Wrapper”描述保留为历史。

分支：zhao/planner-integration。业务接口已在赵侧实现，等待三方确认后合并；不是已冻结的全组接口。

## 2026年10月4日验证结果

- 125 项自动测试通过，0 项跳过；5 个 Mock 场景和本地 HTTP 检查通过。完整记录在本机 outputs/acceptance-20261004-205352-651435。
- 吴原交付包的本地 AIP 文献传输测试通过，protocolCompleted=true、businessReady=false、researchExecuted=false；临时 Partner 已停止。本机记录为 outputs/wu-aip-local-20261004-205047-866837。
- 两次真实 DeepSeek 调用通过：指定仓库返回新任务类型和禁搜/禁执行约束，缺地址返回澄清且 tasks=[]。共 2 次请求，3365 tokens，没有执行科研 Agent。这是两项冒烟验证，不是六场景完整真实验收，也没有替用户标记人工接受。
- 仓库内保留可供队友查看的两次真实响应与元数据：[v0.2 真实规划记录](evidence/v02_live_smoke.json)。其中不含 Key、环境变量和本机路径。

## 可以查看的改动

1. Planner、Pydantic 模型、调度器、JSON Schema 和当前样例统一为 requiredSkill / repository_analyze / schemaVersion=0.2。
2. 仓库发送前必须收到明确 GitHub URL，拒绝自动搜索和代码执行。显式地址优先，缺失地址保留其他成功结果。
3. 显式旧计划迁移入口和历史 v0.1 Schema；历史 outputs 不改写。无仓库地址的旧代码检索任务会阻止整份计划迁移。
4. 吴原交付包保留独立文献传输兼容入口。默认计划改为仓库内样例，不再需要赵的历史输出。
5. 真实规划验收脚本更新为 v0.2 六种情况，包括有条件复现链和缺少仓库地址；原五个 v0.1 人工确认记录仍归原版本。

## 本地运行

先按 README 安装依赖，再运行以下命令。前两条不付费，不访问真实科研源。

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_project.py
.\.venv\Scripts\python.exe -X utf8 run_demo.py
.\.venv\Scripts\python.exe -X utf8 run_wu_aip.py --wu-root "吴交付包中包含wu_delivery文件夹的目录"
```

旧计划迁移使用 `migrate_plan.py "旧plan.json路径"`，结果写入新的 outputs/migration-*，不调用 LLM。只传完整 Plan，不传 Swagger 的 {plan, metadata} 外壳。

需要真实规划时自行配置 .env，然后运行 run_planner.py。verify_live_planner.py 最多产生 12 次模型请求；不能把自动测试中的模型替身算作真实 DeepSeek 验收。

## 给吴

请确认三个业务 Skill ID，按 requiredSkill 对接发现，按 repository_analyze 对接仓库任务。AIP 封装仍使用官方 SDK。文献传输兼容入口暂时支持你原交付包的 v0.1，不代表你已经迁移。论文阅读 paperUrl/paper_analysis 与 papers/paper_note 的差异也需统一，不能仅改仓库任务名就认为三类 Agent 均可联调。

请在你的开发分支提供 ACS 注册信息、ADP 可执行调用入口及新版 Adapter，说明环境和启动步骤；Key、私钥和密码不进仓库。

## 给张

请在你的开发分支提供 Wrapper、依赖、启动方式和至少一个真实仓库输出。Wrapper 可内部把 repositoryUrls 逐个转换成 repository_url，再克隆到 repo_path 调用 CodeWiki；多个仓库的数量上限、错误和部分结果需要保留。对外返回统一 ResearchResult，不直接返回内部 job/status 或仅本地磁盘路径。

自动根据论文寻找仓库暂不纳入首期。内部 MCP 工具调用与外部 AIP 通信分别对接。返回结构、依赖和复现结论时提供实际依据或明确未知。

## 当前仍未完成

三方 v0.2 确认与合并、真实 ACS/ADP 发现、三类科研 Wrapper 的统一执行、带真实论文和仓库证据的完整科研报告、正式平台身份与 AMP 联调。Mock 正常流程全部是假数据，其中 GitHub 形状的模拟地址不保证存在，程序不会访问它。
