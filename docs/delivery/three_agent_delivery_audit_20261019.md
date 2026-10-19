# 三 Agent 交付范围与 GitHub 提交前审计（2026-10-19）

本清单依据 2026-10-19 本机 AIP 验收记录与本轮一次 `git fetch origin` 更新后的远端引用。fetch 只更新本机远端引用；没有 merge、push 或正式梧桐 ADP/AMQP 运行。远端仍可能在此后继续更新，提交前须再次核对。

## 1. Git 来源和提交关系

原始 Agent 功能 checkpoint 为 `ce62c5e`，首次文档 checkpoint 为 `f5d25ae`。刷新后，`origin/main` 为 `b0d7163`，赵的 `origin/zhao/planner-integration` 为 `4a39ba2`（其前一新增提交 `2f2fb0c`），吴的 `origin/wu/planner-aip-integration` 为 `ada04bc`。fetch 时当前工作分支与最新赵分支的共同祖先是 `3ba828e`：本地有 12 个独有提交，赵分支有 2 个尚未纳入的提交；本轮后续文档 checkpoint 会增加本地提交数。`origin/main` 是赵分支的祖先，落后赵分支 10 个提交。故推荐**以最新赵分支 `4a39ba2` 的 Planner 为目标基线**，但先解决下述 Repository/AIP 重叠，再创建交付分支。与张的 `origin/zhang/agent-wrappers` (`d84924d`) 和 `origin/zhang/repository-agent` (`be99b31`) 的共同祖先均为 `b0d7163`；与吴分支的共同祖先为 `ebd8050`。

| 本地提交 | 主要内容 |
| --- | --- |
| `1f6434c` | 保全 S3 本机 AIP 集成与 Repository Wrapper |
| `b782979` | S2→S3 的 paperNotes 和 repositoryUrls 绑定、证据关联 |
| `c14b5c4` | Literature Resource Resolver |
| `d92b951` | 三 Agent 离线 AIP 绑定测试 |
| `e5d26c7` | 单请求 S1 真实检索验收工具 |
| `9cbc5e4` | OpenAlex 有界备用检索 |
| `c957eee` | Literature 本机执行期限与错误记录、Reader 动态输入初版 |
| `0e7795a` | Reader PDF 准备移入独立 Worker |
| `4398c98` | OpenAlex 传输错误脱敏分类 |
| `9fcb948` | OpenAlex 代理继承配置 |
| `ce62c5e` | Resource Resolver 请求诊断与代理配置 |

这些提交在同一条本地历史上，**不是**相对 `main` 的全部变更。最新赵分支仍是唯一 Planner 规划/调度基线；`planner.py`、`input_binding.py`、`aip_orchestrator.py` 在当前分支与最新赵分支内容一致，属继承的 Planner/AIP 核心。当前 `resolver/local_aip.py`、`result_contract.py` 有本机集成改动；赵新增的是 `resolver/local.py`，二者用途要对齐但不直接互相替换。三个 Agent 业务实现、Worker、业务 AIP Partner 归张；本机 Resolver 与联调代码保留作开发验收入口。赵负责统一 Planner 与结果汇总，吴负责正式平台通信和部署。

张的 `agents/paper_reader/` 与当前目录内容一致；当前 `agents/repository/` 相对张的 Repository 分支仅 `README.md`、`researchmesh_repository/agent.py`、`enrichment.py`、`schemas.py` 四处有差异，主要是 S2 论文笔记输入及论文与代码证据关联。Literature 基线已在旧 `main`；当前新增 OpenAlex、Resource Resolver 等扩展。吴分支包含相同 Reader Wrapper 和旧版 Repository Wrapper；Repository 上述四个文件有实际内容差异。吴的 `aip/`、`three_agent_dispatch_v02.py`、新增 AMP heartbeat 配置和 Group Literature 适配是并行的平台实现，不应覆盖当前三个 Agent 的业务 Partner。

### 与最新赵分支的同路径重叠

赵新增的 `2f2fb0c` 将 Repository 静态分析与 Reader context 接入其本机 AIP；`4a39ba2` 增加独立平台 MQ 连接检查。以共同祖先 `3ba828e` 计算，双方有 **13 个同路径改动**：`agents/repository/README.md` 与 `researchmesh_repository/{__init__,agent,codewiki_provider,config,enrichment,evidence,fetcher,schemas}.py`（9 个），以及 `aip_business_partner.py`、`aip_repository_partner.py`、`run_aip_literature.py`、`run_aip_repository.py`（4 个）。Repository 业务源码中的 `__init__`、`codewiki_provider`、`config`、`evidence`、`fetcher` 与当前内容相同；真正的内容差异集中在 Repository README、`agent.py`、`enrichment.py`、`schemas.py` 和四个 AIP/运行文件。双方还分别新增了不同文件：赵的 `repository_aip_worker.py`、`repository_paper_context.py`、`resolver/local.py`、`platform_mq_check.py` 等，与本分支的 `resolver/local_aip.py`、Reader Worker、本机三 Agent 入口需要按用途对照，不能简单全量保留两套执行路径。

Planner 核心三文件未发生双方编辑冲突；若后续刷新发现这些文件也冲突，先列出并由赵确认，不自行修改。上述 13 个路径有合并/重复实现风险，**尚未执行 merge 或冲突模拟写入**。建议先以现有三 Agent 已验证实现及论文/代码证据规则为验收准绳，再与赵逐文件确定其新 Repository 接缝如何调用同一 Agent，而非建立第二套 Agent。

## 2. 正式交付代码与本机工具

| 范围 | 正式交付所需文件 | 本机联调 / 验收工具 |
| --- | --- | --- |
| Literature | `agents/literature/researchmesh_literature/`（Semantic Scholar SDK Provider、OpenAlex、Resource Resolver、schema）、`agents/literature/requirements.txt`、`aip_literature_partner.py`、`literature_adapter.py`、`run_literature.py`、`semantic_http.py` | `run_aip_literature.py`、`scripts/run_s1_qlora_once.py`、`agents/literature/scripts/live_test_literature.py` |
| Reader | `agents/paper_reader/researchmesh_paper_reader/`、`agents/paper_reader/requirements.txt`、`aip_reader_partner.py`、`aip_reader_inputs.py`、`reader_aip_worker.py`、`reader_runtime.py`、`reader_adapter.py` | `run_aip_reader.py`、`scripts/check_aip_reader_startup.py`、`agents/paper_reader/scripts/live_test_paper_reader.py` |
| Repository | `agents/repository/researchmesh_repository/`、`agents/repository/requirements.txt`、`aip_repository_partner.py`、`repository_adapter.py` | `run_aip_repository.py`、`agents/repository/scripts/live_test_repository.py`、示例 JSON |
| 共用本机 AIP | `aip_business_partner.py`、`result_contract.py`、`resolver/local_aip.py`；正式整合前与赵确认 | `run_aip_three_agent.py`、`run_aip_s2_s3.py`、`run_aip_research.py`、`docs/S2_S3_integration_ready_20261015.md` |

独立离线测试：`agents/literature/tests/`、`agents/paper_reader/tests/`、`agents/repository/tests/`；本机集成测试：`tests/test_aip_literature.py`、`tests/test_aip_dynamic_reader.py`、`tests/test_aip_repository_integration.py`、`tests/test_aip_three_agent_offline.py`、`tests/test_s2_s3_paper_binding.py`、`tests/test_literature_openalex.py`、`tests/test_literature_resources.py`、`tests/test_semantic_http.py`。测试和 README 建议与对应交付模块一同提交；本机 live 脚本须标注仅供验收，不能作为平台正式调度入口。

## 3. 提交安全审计

复核时扫描 241 个已跟踪文件；未发现已跟踪 `.env`、`outputs/`、日志、PDF、虚拟环境、模型权重、第三方源码或大于 1 MB 文件。以当前 `.env` 中 3 个非空密钥值扫描所有已跟踪文件，未发现字面泄漏；此前审计亦未发现硬编码 Bearer/Token 值。`.gitignore` 已忽略 `.env`、`outputs/`、缓存、虚拟环境、日志与常见密钥文件，但推送前仍须重新扫描**已跟踪文件和拟提交差异**。

初次扫描发现 14 个已跟踪 Markdown 文件含个人电脑绝对路径：根 `README.md`，以及 `docs/S2_S3_integration_ready_20261015.md`、`docs/aip_business_fixture_20261012.md`、`docs/aip_reader_live_20261012.md`、`docs/arxiv_pdf_resolution_20261012.md`、`docs/independent_completion_20261012.md`、`docs/literature_integration.md`、`docs/planner_constraints_fix_20261012.md`、`docs/reader_alignment_validation_20261011.md`、`docs/reader_batch_integration.md`、`docs/reader_local_pdf_live.md`、`docs/reader_single_integration.md`、`docs/research_reader_pipeline.md`、`docs/week2_progress.md`。本轮已把**拟提交差异**中的 `docs/S2_S3_integration_ready_20261015.md` 个人路径替换为相对部署说明；其余 13 个在继承历史/基线，未擅自重写其他成员文档。向 `main` 提交前需单独清理这些文档；历史中的个人路径如需完全移除，应另行与团队商议。不要把 Git 忽略规则当成已跟踪内容的保护。

**不得提交** `.env`、任何 Key/Token、原始 `outputs/`（含业务结果与日志）、下载 PDF、embedding/模型缓存、独立 venv、临时仓库与第三方 upstream checkout。只提交下述脱敏验收摘要，不提交原始运行记录。

## 4. 可交付性与优先级

| 优先级 | 事项 |
| --- | --- |
| 提交前必须 | 更新本地远端引用并核对目标基线；确定赵/吴/张代码归属；清理拟提交差异中的个人绝对路径；按拟提交文件再次扫描密钥、二进制和日志。 |
| 团队联调前 | 在另一台机器建立独立 Reader Python 3.13 + PaperQA/embedding 缓存、Repository Python 3.12 + CodeWiki/Node 环境；核对本轮已补充的 `.env.example` 与目标部署环境；正式 ADP 发现、AMQP 消息和平台进程管理做真实联调。 |
| 演示准备 | Repository 结构化 `entrypoints` 为 0、README `runCommands` 为 5，其中一条命令编码异常；展示时明确区分两者。总耗时约 207 秒，应准备进度显示与超时说明；完善 DeepSeek 请求数、token、费用采集。 |

当前三个 Agent 的本机功能验收通过；跨机器可部署性和正式平台协议尚未验证。`agents/literature/README.md` 的早期“待 AIP 联调”状态描述已过时，正式提交前应更新文档，避免与实际验收摘要矛盾。

## 5. 建议的 GitHub 集成方式

目标基线建议为刷新后的 `origin/zhao/planner-integration@4a39ba2`。先与赵确认上述 13 个同路径改动的取舍及 `repository_aip_worker.py` 等并行执行路径；与吴确认其平台 `aip/` 桥接到现有三个业务 Partner 的最小适配，不复制 Agent。之后从该基线创建独立交付分支，按路径审查并组织 Literature/Reader/Repository 业务代码、对应 Partner、独立测试、本机三 Agent 回归/验收脚本、配置模板与文档。`run_aip_three_agent.py`、`run_aip_s2_s3.py` 必须保留且标记为**本机开发与回归入口**，不宣称平台 ADP/AMQP PASS。当前 `1f6434c..f5d25ae` 的 12 个提交是可追溯来源，**不是可盲目整体 cherry-pick 的最终 PR 范围**。特别不能把 `origin/main..HEAD` 全算作本人的 Agent 代码，也不能整分支 merge 张或吴的 Wrapper 副本。本轮未 push、未 merge、未建 PR。

建议在新交付分支整理为四组可审查提交（最终哈希只有在解决冲突并实际整理后才能给出）：

1. Literature：现有 `agents/literature/` 增量、`aip_literature_partner.py`、`literature_adapter.py`、`run_literature.py`、`semantic_http.py`、相应测试及 `.env.example` 的公开配置名。
2. Reader：`agents/paper_reader/`、`aip_reader_inputs.py`、`aip_reader_partner.py`、`reader_aip_worker.py`、`reader_runtime.py`、`reader_adapter.py`、相应测试。
3. Repository：已验收的 `agents/repository/` 版本及 `aip_repository_partner.py`，与赵新静态接缝逐文件整合后提交；保留 Repository 独立及 S2→S3 测试，不把其静态 Worker 的“无 LLM”结果冒充当前真实 enrichment。
4. 本机回归与交接：`resolver/local_aip.py`、适配层需要的公共辅助、`run_aip_three_agent.py`、`run_aip_s2_s3.py`、三 Agent 离线测试及本目录交接文档。`run_aip_literature.py`、`run_aip_repository.py` 与赵分支同路径，先确定最终唯一入口和兼容测试。

PR 目标推荐最新赵分支而非旧 `main`；如团队要求最终进 `main`，应先由赵的 Planner 变更进入 `main`，再将三个 Agent 交付指向包含该 Planner 的基线。吴的平台层随后单独按接口接入，并以正式 ADP/AMQP 测试验收。此处是集成方案，尚未创建分支、PR 或执行 merge。
