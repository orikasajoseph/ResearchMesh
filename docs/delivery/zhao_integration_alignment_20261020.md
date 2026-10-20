# 赵：三 Agent 验收快照对齐及集成决定草案

核对日期：2026-10-20。来源为张的固定预览快照及当日 `git ls-remote`，不直接整体合并预览分支，不改 `main`、张或吴的分支。本文件是本地处理结论和协作建议，尚未代表张、吴已同意，也不是收费新整链或平台验收证明。

## 1. 集成基线与版本事实

- GitHub 可拉取基线：`zhao/planner-integration@d4bb9d8485f1e52873074e2a255d02027a246218`。
- 张的验收快照：`zhang/three-agents-integration-preview@22863c04a8afab8457df3c1a82e46da2f6217e0c`。
- 共同祖先：`3ba828e0755f91e789a829c42f7d5df5f4045034`。
- 张的交接文档记录的是当时 `4a39ba2` 的比较；最新赵分支已追加 `d4bb9d8`。不能据旧比较认定现在仍缺少 Repository 增强。
- 赵分支相对预览独有 `2f2fb0c`、`4a39ba2`、`d4bb9d8`，都是赵的既有提交，不算张新增工作。
- 当前两提交中的 `planner.py`、`planner_contract.py`、`input_binding.py`、`aip_orchestrator.py`、`common/models.py` 内容一致，不另开 Planner 实现或协议。
- 本地“新问题统一三阶段入口”及本文件仍未提交/推送。队友仅拉取 `d4bb9d8` 不会得到这些改动；正式 PR 前需再同步一个明确的集成提交，不把工作区代码叫作已上传版本。

张的 `run_aip_three_agent.py` 是 QLoRA 固定计划的真实 Agent/AIP 定向验收入口，具有参考价值，不等于任意新问题真实模型规划的生产入口。赵本地的 `run_aip_research.py` 才负责新问题→真实 Planner→三阶段；当前只完成免费离线整合回归，尚未获得本轮收费新问题整链测试授权。

## 2. Repository：不是“静态替掉增强”

保留一套业务 Wrapper、一套 Task v0.2、一套本机入口：

| 层级 | 入口/模块 | 处理决定 |
|---|---|---|
| 用户统一入口 | `run_aip_research.py` | 保留赵的自然语言规划、绑定、检查点和汇总报告；不把 QLoRA 固定验收脚本当作默认 Planner |
| 单阶段 CLI | `run_aip_repository.py` | 保留赵现有显式双模式与免费预检；不能拿 CLI 代替平台长期服务 |
| 本机 Partner | `aip_repository_partner.py` | 保留一个显式双模式入口，复用同一 Wrapper；不在业务 Task 中增加模式字段 |
| 静态 Worker | `repository_aip_worker.py` | 真实 CodeWiki 静态分析、无模型、隔离环境、可做词面/标识符对照；不是 Mock，也不是增强复现推断 |
| 增强 Worker | `repository_enhanced_worker.py` | 复用静态事实与张的 `RepositoryEnricher`、团队共享 LLM；失败保留静态结果，不重做静态分析 |
| 三个已对齐业务文件 | `agent.py` / `enrichment.py` / `schemas.py` | `d4bb9d8` 已原样取自 `22863c0`；其余五个业务模块也与预览一致 |
| 论文证据投影 | `repository_enhancement_contract.py` | 复用稳定 `P-...` 证据 ID，校验论文/代码双方 ID，不让 Leader 反向导入具体 Partner 或 PaperQA |

安全默认 `static`；只有显式 `enhanced` 加 `--execute` 才允许收费增强。增强是真实科研分析模式，不降格为纯测试；静态同样是有效的零模型业务模式，不伪装成增强。

已验收的双模式结果要区分 `protocolCompleted`、`businessReady`、`enhancementSucceeded`。模型增强失败但静态有效时保留静态结果、标明降级，不能宣称增强成功或完成复现。

Repository Wrapper 原生产物 item kind 是 `repository_record`；赵的 `repository_adapter.py` 将其转换为 Leader 现有标准 `repository`。两者不是需要全组各写一套的外层协议。吴接业务能力时复用此适配，不把原生产物直接送入当前 Leader，也不修改共享合同去无校验地接受两种结构。对外标准化 `evidence.supports` 仍为非空具体结论字符串。

## 3. 13 个业务同路径改动逐项安排

按共同祖先重新计算，13 个业务重叠路径之外还包含根 `README.md`，即总共14个同路径；下面的13项不包含根 README。当前8个 Wrapper Python文件已与预览 Blob 完全一致，真正还需取舍的是5个文档/接缝路径，而不是13个文件全部重写。

| 路径 | 当前状态与建议 |
|---|---|
| `agents/repository/README.md` | 赵修正旧的“永远关闭增强”说明；张复核能力边界 |
| `agents/repository/researchmesh_repository/__init__.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/agent.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/codewiki_provider.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/config.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/enrichment.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/evidence.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/fetcher.py` | 与预览相同，直接保留 |
| `agents/repository/researchmesh_repository/schemas.py` | 与预览相同，直接保留 |
| `aip_business_partner.py` | 赵处理公共生命周期、Task七字段与模式防护；采纳缺失的安全错误码，并在 Worker 前校验全文上下文；张复核业务语义 |
| `aip_repository_partner.py` | 赵保留已验收隔离双模式和防重复调用；张复核它仍复用原 Enricher、证据与降级路径；动态长期服务部分须与吴另行适配 |
| `run_aip_literature.py` | 赵处理运行控制与来源验收，采纳显式独立 Python 参数、OpenAlex来源兼容；保留诊断和有限重试，不整体替换 |
| `run_aip_repository.py` | 赵保留显式模式、预检、来源校验和报告；张复核增强模式输出及退出码 |

建议由赵执行最终逐文件整合，张负责三个 Agent 的实现及增强语义审核，吴负责正式 ACS/ADP/AMQP 接入审核。根 README 由赵归并说明，避免把任何人的历史工作重新署为新开发。
完整 Blob 对照保存在旁边的 `zhao_integration_alignment_20261020.json`，测试会检查已对齐的8个 Repository模块及迁入的5个Reader业务模块。

## 4. 早先对齐阶段的本地修改（631项测试时）

1. 原样选择性迁入张预览快照的 Reader 五个业务模块、依赖声明及14项离线测试；不复制 Key、模型、环境、PDF，也没有重写 Reader。统一入口默认可定位仓库内 `agents/paper_reader`，仍通过显式 `--reader-python` 或私密环境变量选择缓存齐全的本机运行环境。
2. 修正 Repository README 和 Partner 过时的 static-only 描述；代码保留默认静态、显式增强，不增加隐式付费行为。
3. 为文献适配器增加 `provider=openalex` 接收兼容，文献来源验收要求对应 Provider 的实际成功 HTTP 诊断；不能用 Semantic Scholar 200 或仅一个 OpenAlex 标签冒充备用检索通过。
4. 为现有 `live_partner` 增加张验收脚本依赖的可选 `python_executable` 参数；默认运行环境不变，路径无效时不启动进程。
5. 公共 AIP 入口在启动 Repository Worker 前严格校验全文笔记与论文来源，并保留张的固定 PDF/仓库错误码。未知异常仍不回显配置或密钥。
6. 增加快照来源、六字段 PaperRef、七字段 Task、来源诊断和兼容边界的离线回归。

当时只修正文献接收/验收兼容，尚未迁入OpenAlex Provider、资源配置和动态Reader；**这是早先状态，不是当前状态**。后续已选择性补齐，见第6节。没有整枝覆盖 `resolver/local_aip.py`、原生产物双类型校验或固定QLoRA调度，PDF/来源验证仍保留。

### 本轮免费验收

- `verify_project.py`：631项自动测试通过，0 skip；五个模拟场景和本地HTTP检查通过。
- 构成：已提交基线555项 + 原有未提交Group32项 + 上一轮统一入口18项 + 张的Reader离线单测14项 + 本轮对齐12项。631不是队友仅拉取d4bb9d8就应看到的数量。
- 已迁入Reader五个业务文件与预览快照的Git Blob逐项一致；八个Repository业务模块同样一致。
- 使用仓库内Reader包与已有PDF缓存的免费预检通过；未启动Reader模型、未下载、未重新检索。
- `git diff --check`通过；没有暂存文件，没有commit、push或修改远端分支。
- 记录：`outputs/acceptance-20261020-134932-969080/`；本地Reader预检：`outputs/aip-reader-20261020-135129-780152/`。原始记录保留本机，不上传。

## 5. 下一次 PR 与平台对齐

- PR 以赵最新已同步提交为 base，在明确的独立 integration 分支逐项引入；不要向旧 main 直接整体 merge预览快照。
- Literature 的 OpenAlex/资源查找 Provider 与配置迁入，需要张复核，并同时检查已有共享限流修复、请求预算与最终来源诊断，不能只复制一个 Adapter 就宣称功能已启用。
- Reader 包已可随仓库分发，但平台动态收 Task、PDF获取、缓存预热和持久 embedding 复用仍须一起核对，不直接用一次性 CLI 的固定输入文件当平台服务。
- 吴复用 `literature.search / paper.analyze / repository.analyze` 与七字段 Task；ADP发现、AIC/证书、Group/ACL和AMQP传输属于平台适配层。平台尚未通过不能反向否定已通过的本机业务链，也不能把本机完成写成梧桐完成。
- 真实新问题整链、真实检索/PDF与收费模型测试，需用户另行同意；本轮没有重复收费验收。
- 本地已有 Group 邀请代码和上一轮统一入口改动保留；本次没有 commit、push 或创建PR，不声称队友已拿到工作区内容。

## 6. 后续补齐完成（当前684项免费测试）

- OpenAlex Provider、ResourceResolver、Agent来源标记与配置已迁入；默认不开启备用检索/资源解析，显式.env/进程配置生效。API Key不改、不上传。保留赵原有共享限流/有界重试，开启备用路径时不叠加外层恢复。
- 修复开放年份范围2022:被误当单年，以及资源请求数/时间预算配置校验。
- 动态Reader Partner、bound Task→来源/PDF验证→原Reader Worker已接入；不再必须提前准备固定reader_input.json。动态预检不下载PDF或构造模型，正式Worker执行须明确授权。
- 提取 `create_reader_business_executor`、`create_repository_business_executor`，本机AIP与平台适配共用；动态Repository同样由本轮Task生成输入，不另建Agent。
- 稳定平台入口 `business_execution.execute_business_task` 已实现。缺省拒绝执行，输入七字段/输出六字段，校验skill、来源、全文及增强模式；不发协议消息、不新建status、不重试收费后端。
- `reader_runtime.make_persistent_reader_executor` 支持复用同一准备好的Agent/embedding，要求调用者先验证本轮PDF。隔离Worker仍可使用，但不宣称跨进程embedding常驻。
- 超时/取消保留不确定性，平台持久去重仍归吴。Reader660秒、Repository静态180/增强300秒是业务预算，平台等待须留余量。
- 本轮完整免费验收：684项、0 skip，五个模拟场景、本地HTTP通过；记录 `outputs/acceptance-20261020-141840-108212/`。收费API、真实论文HTTP/PDF/clone及正式平台调用均未重新执行。
- 真实CodeWiki本地临时仓库smoke为10/10通过，模型仍是假Provider：`outputs/repository-dual-smoke-20261020-142111-86ad07c5/`。Reader现有Python/缓存免费预检通过：`outputs/aip-reader-20261020-142116-701074/`。源文件只读扫描仍仅警告历史文档taskId中的`sk-`子串（已核实误报），不包含本轮新凭据。
- 旧LoRA真实新问题三阶段静态链记录 `outputs/aip-research-20261020-135700-393904/` 保留。本轮复核不把该旧记录变造为OpenAlex/新平台入口的真实验收。
- 入口签名、工厂示例、配置、安全和费用边界详见 `platform_business_entry_v1.md`。尚未commit/push；发布基线仍d4bb9d8，需同步明确新commit后张、吴才能拉取这些补丁。
