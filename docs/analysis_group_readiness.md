# Reader / Repository Group 接入交接（2026-10-23）

代码分支：`wu/reader-repository-readiness`。独立工作树：
`D:\research\aic\worktrees\reader-repository-readiness`。
以 Wu `2010be5` 为基础合入赵侧 `3e5efd2`，保留 Wu Group 模块，
复用已发布的隔离 Reader / CodeWiki 后端，并接入 Literature 审计提交。
原主工作树的未提交草稿、运行环境、输出和正式 Literature Partner 没有被覆盖或停止。

## 已独立完成

- Reader：新增正式 `paper.analyze` Group 入口、严格七字段单篇 Task 校验、
  SQLite 去重、独立 Worker、六字段全文 Result 校验、持久化回读及退群审计。
  缺 PDF、批量输入、摘要冒充全文、论文来源不一致均不能成为成功 Product。
- Repository：将现有未提交接入草稿复制到隔离分支完善，固定 static 模式，
  每个新 Task 独立 Worker/目录；禁止增强模型、运行仓库代码、安装目标依赖。
- 两个 Agent 共用 `AnalysisGroupHandler` 的持久化生命周期；
  reserve → working 在执行 Worker 前完成，重复 Start 和进程重启不触发自动重跑。
  调用结果不确定时保留 uncertain，不自动重试；任务取消后不提交迟到 Product。
- 复用应用内 SDK 子类观察 DISBAND、退群入口、清理成功/失败和 GROUP_LEFT。
  原 SDK 处理 ACK 和 `_pending_leave`，没有修改 SDK 安装文件。
- 审计脱敏 Result 支持 paper_note 和 repository；丢弃自由文本与原 URL，
  保留契约结构、原始内容摘要、脱敏摘要与落盘回读结果。
- `verify_analysis_acceptance.py` 持久化五项判定：SQLite completed、
  backend 一次执行、六字段 Result 回读、DISBAND/清理/GROUP_LEFT、无异常及残留。
  **backend 一次不等于模型 HTTP 一次。** Reader 可能发送多次 PaperQA/DeepSeek 请求，
  报告明确设置 providerHttpCountVerified=false，不套用 Literature 的单次 Semantic Scholar 标准。
- `--serve` 前检查本机运行时；检查失败时不开 MQ。默认调用只做路径与本机 mTLS 检查。

核心文件：

| 文件 | 用途 |
| --- | --- |
| `aip/group_analysis_handler.py` | 两个 Agent 的共享持久化任务生命周期及审计 |
| `aip/group_analysis_partner.py` | 可信邀请、Group 回调和进程生命周期 |
| `aip/group_reader_handler.py`, `aip/group_repository_handler.py` | 各自输入/输出契约及超时 |
| `aip/reader_unified_executor.py`, `aip/repository_unified_executor.py` | 独立、无重试的每 Task Worker |
| `aip/group_reader_partner.py`, `aip/group_repository_partner.py` | 后端与正式 SDK 连接 |
| `run_group_reader.py`, `run_group_repository.py` | 默认离线检查，显式 --serve 才启用正式连接 |
| `aip/literature_audit.py`, `verify_literature_acceptance.py` | 扩展共享证据能力；Literature 默认判定保持 HTTP 严格计数 |
| `verify_analysis_acceptance.py` | 两个 analysis Agent 的只读验收与独占报告文件 |
| `scripts/check_analysis_group_readiness.py` | 不创建 Provider 的证书/缓存/解释器检查 |
| `scripts/test_literature_audit_offline.py`, `scripts/test_analysis_wrappers_offline.py` | 阻断外网的测试运行器 |
| `tests/test_analysis_group_readiness.py`, `tests/test_group_repository_platform.py`, `tests/test_repository_static_executor.py` | 新入口、去重、失败、隔离目录及模拟 SDK 生命周期 |
| `.env.example`, `config/amp/fluent-bit-heartbeat-repository-platform.conf` | 配置说明与心跳采集示例；不包含密钥 |

审计文件与报告均独占新建，位于 `D:\research\aic\evidence\`，不覆盖历史。
Worker 本地科研原件在工作树 `outputs/`（被 Git 忽略）；不会把这些全文记录推送到 GitHub。
正常退群后应用继续存活。SDK 完整清理成功后，应用关闭旧客户端并创建新连接与 Inbox；
审计记录 `INBOX_RESTART_STARTED` / `INBOX_RESTART_SUCCEEDED`。清理失败时停止重建，
不以强制关闭整个应用作为退群成功条件，也不在残留资源上继续服务。

## 本机验证结果

- 项目测试和 Reader 原生 Wrapper：859 项通过。
- Repository 原生 Wrapper 在现有 Python 3.12 / CodeWiki 环境：22 项通过，
  包括实际 CodeWiki 对临时本地小仓库的静态分析，没有网络 clone 或模型调用。
- 合计 881 项通过。新增 analysis Group 与 Repository 定向测试共 26 项。
- 两个注册身份的证书 CN、有效期、证书/私钥匹配与本机 mTLS 均通过。
- Reader 依赖/配置/embedding 缓存检查通过；Repository Python 3.12、
  CodeWiki 2.0.0 固定提交及静态入口导入检查通过。
- 正式 MQ/ADP/AMP 在线状态未验证，未启动正式 Partner，未创建科研 Task。

证据文件：

- `D:\research\aic\evidence\analysis-group-handoff-tests-20261023.txt`
- `D:\research\aic\evidence\repository-wrapper-runtime-tests-final-20261023.txt`
- `D:\research\aic\evidence\analysis-group-readiness-0ad3abe7bc734826abe32d9bd2d9c4d7.json`

早期测试失败日志也保留：Windows 非 UTF-8 默认编码、本机测试 HTTP 服务被过严拦截、
测试的 logging 全局状态相互污染，以及误在项目 Python 运行依赖隔离 CodeWiki 的测试。
最终分别在正确运行时、UTF-8 下验证；没有跳过 Repository 实际本地静态测试。
Literature 原审计分支的 149 项中一项基线失败，在本集成分支采用队友最新 Planner 修复后不再出现。

复现：

```powershell
Set-Location D:\research\aic\worktrees\reader-repository-readiness
& D:\research\ResearchMesh\.venv\Scripts\python.exe -X utf8 scripts/test_literature_audit_offline.py --pattern 'test_*.py' --module agents.paper_reader.tests.test_researchmesh_paper_reader
& D:\research\ResearchMesh\.venv-repository\Scripts\python.exe -X utf8 scripts/test_analysis_wrappers_offline.py --agent repository
& D:\research\ResearchMesh\.venv\Scripts\python.exe -X utf8 scripts/check_analysis_group_readiness.py --env-file D:\research\ResearchMesh-literature-live\.env --reader-python D:\research\ResearchMesh\.venv-reader\Scripts\python.exe --repository-python D:\research\ResearchMesh\.venv-repository\Scripts\python.exe --reader-runtime-home D:\research\aic\model_cache
```

测试允许本机临时回环端口供 asyncio / 本机测试 HTTP 服务使用，禁止外网和正式 MQ。
Windows 沙箱限制回环或临时目录时，使用允许本机测试资源的终端，仍保留运行器网络阻断。

## 人工安排正式运行时使用（本次未执行 --serve）

下面示例只设置非秘密运行路径及已注册身份；不要复制第三方 API Key 到命令行。
先确认队友 Leader 对 paper_read / repository_analyze 的路由、ACL、目标 AIC 和超时，
完成代码审查与运行窗口协调，再运行相应 --serve。不要并行启动同 AIC 的重复实例。

```powershell
Set-Location D:\research\aic\worktrees\reader-repository-readiness
# 从已核对的注册表获取身份；不会连接平台。
$env:RM_READER_AIC = (& D:\research\ResearchMesh\.venv\Scripts\python.exe -c 'from scripts.platform_preflight import AICS; print(AICS["reader"])')
$env:RM_REPOSITORY_AIC = (& D:\research\ResearchMesh\.venv\Scripts\python.exe -c 'from scripts.platform_preflight import AICS; print(AICS["repository"])')
$env:RM_ALLOWED_LEADER_AIC = (& D:\research\ResearchMesh\.venv\Scripts\python.exe -c 'from scripts.platform_preflight import LEADER_AIC; print(LEADER_AIC)')
$env:RM_READER_PYTHON = 'D:\research\ResearchMesh\.venv-reader\Scripts\python.exe'
$env:RM_READER_WRAPPER_ROOT = "$PWD\agents\paper_reader"
$env:RM_READER_RUNTIME_HOME = 'D:\research\aic\model_cache'
$env:RM_REPOSITORY_PYTHON = 'D:\research\ResearchMesh\.venv-repository\Scripts\python.exe'
$env:RM_REPOSITORY_WRAPPER_ROOT = "$PWD\agents\repository"
# 下面两条默认只做离线配置检查。
& D:\research\ResearchMesh\.venv\Scripts\python.exe -X utf8 run_group_reader.py --env-file D:\research\ResearchMesh-literature-live\.env
& D:\research\ResearchMesh\.venv\Scripts\python.exe -X utf8 run_group_repository.py --env-file D:\research\ResearchMesh-literature-live\.env
# 正式窗口就绪后，在各自终端对对应命令加 --serve；本交付没有执行。
```

Reader Group 外层预算 690 秒，业务后端 660 秒，Worker 阅读约 600 秒；
Repository Group 外层 210 秒，业务后端 180 秒。Leader 超时须留出额外平台往返余量。
Reader 每 Task 启动独立 Worker，不承诺跨进程复用 embedding；可能有额外冷启动成本。
Worker 会核验已绑定 PDF 的来源、哈希/标题，再进入全文与模型路径；路径/缓存预检通过不代表 PDF 已核验。
Repository 不自动查找仓库；必须提供明确 HTTPS GitHub URL，论文上下文只能按既有绑定规则传入。
AMP 心跳文件写出不等于平台在线；Fluent Bit 示例中的路径需要与正式部署工作树一致，采集上线另验。

## 下一步需要队友配合

1. Literature 先用全新 Group/Session/Task 验证原五项；第三次记录不重跑、不补造。
2. 协商 Reader 单任务：由 Leader 绑定一篇真实可访问、来源可验证的 PDF，
   允许正式阅读所需的模型调用，保留七字段 Task 与 Leader 六字段 Result 原件和时间线。
3. 协商 Repository static 单任务：提供明确仓库 URL；不运行仓库代码，不启用模型增强。
4. 每轮完成 Result → Complete → SQLite completed 后再发 DISBAND；
   收齐清理、GROUP_LEFT 与无重复执行证据，运行对应验收器。

```powershell
& D:\research\ResearchMesh\.venv\Scripts\python.exe -X utf8 verify_analysis_acceptance.py --task-type paper_read --journal '<本次审计JSONL>' --database D:\research\aic\state\group_tasks.sqlite3 --group '<新Group>' --session '<新Session>' --task '<新Task>' --agent '<Reader AIC>'
# Repository 将 task-type 改为 repository_analyze，并使用其自己的本轮标识与审计文件。
```

报告 accepted=true 只表示该 Agent 的规定业务与生命周期证据满足规则，
不表示模型请求只有一次，不表示论文结论已由人工逐项复核，不表示远端 broker 已独立盘点。
当前交付是已离线验证的正式接入代码与本机准备检查，尚不是两个 Agent 的真实平台验收。
