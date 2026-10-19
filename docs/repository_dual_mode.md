# Repository 双模式整合（2026-10-19）

## 本次范围

在赵 `4a39ba2` 基础上，选择性引入张的协作预览 `22863c04a8afab8457df3c1a82e46da2f6217e0c` 中 Repository 的 `agent.py`、`enrichment.py`、`schemas.py` 三个文件。三文件保持该提交原内容。没有整体合并预览分支，也未改 Planner、Reader、Literature、吴的适配器或 Group 邀请代码。

两种模式使用同一套 Repository Wrapper、`repository.analyze` 技能、Task v0.2 七字段及 `code_records.v1`。开关属于本机运行配置，**不是新增协议字段**。`requiredSkill`、`dependsOn` 仍不发给 Partner。

| 模式 | 开关 | 行为 | 费用 |
| --- | --- | --- | --- |
| 静态（默认） | 无，或 `--analysis-mode static` | 沿用隔离静态 Worker；可对已有论文笔记做词面/标识符对照 | 模型调用 0 次 |
| 显式增强 | `--analysis-mode enhanced` | 先隔离静态分析，再用共享 DeepSeek Provider 和张的 Enricher 生成证据约束的概述、障碍与论文代码关联 | 配合 `--execute` 才可能计费 |

两个模式都不执行目标仓库代码、不安装目标依赖、不自动搜索仓库、不宣称复现成功。仍限一个显式 GitHub 仓库；带论文上下文时沿用赵的一篇真实全文笔记边界。未接正式 ADP/AMQP/Group，不能作为梧桐互联证明。

## 结构与证据

- `aip_repository_partner.py`：同一个本机 AIP Partner，根据显式运行配置选择隔离 Worker；不在 Task 内混入运行开关。
- `repository_aip_worker.py`：原静态路径保持不变；移除继承的 API Key，禁用 dotenv，阻断 Python 网络，关闭并防守任何增强调用。
- `repository_enhanced_worker.py`：先在同样的 Python 网络防护下运行静态 Wrapper，再退出该防护，仅在可选阶段构造共享 LLM Provider。先保存 `static_checkpoint.json`，避免增强超时丢掉静态事实。
- `repository_enhancement.py`：复用张的 `RepositoryEnricher`，不重复运行 CodeWiki。Provider 失败时保留静态结果及 `LLM_ENRICHMENT_UNAVAILABLE`。
- `repository_enhancement_contract.py`：复用张的稳定 `P-...` 论文证据 ID 算法；验证来源，最多投影 6 条证据，并限制输入文本长度。Leader 也独立检查双侧证据，不导入 Partner/PaperQA。
- `repository_adapter.py`：保留本分支已有的 `repository_record → repository` 结果标准化；保留代码结构、`paperEvidence` 与 `paperCodeRelations`。Reader 的 `supports` 及对外标准化证据的 `supports` 仍为具体结论字符串。

`paperComparison` 只代表静态词面线索，不能替代 `paperCodeRelations`。后者必须同时引用本次输入中的论文证据 ID 和本次仓库代码证据 ID；无有效关联则明确标记，不猜测仓库、不制造论文证据。ID 校验通过不等于科学结论已核实，仍需人工检查原文。

## 安装（移机时）

在已装 Leader 依赖的 Python 3.12 主环境中执行：

```powershell
python -X utf8 scripts/setup_repository_static.py --enhanced --execute
```

这只建立本项目 `.venv-repository`，安装固定 CodeWiki 静态依赖与团队共享 LLM 客户端，不改 Leader/Reader 环境，也不调用模型。只需要静态模式时省略 `--enhanced`。

CodeWiki 以固定提交、`--no-deps` 安装：这是刻意收窄的静态路径，不是完整 CodeWiki 生成 CLI。pip 可能提示其其他上游可选功能依赖未安装；不应因此安装完整 CodeWiki 全家桶。本地静态 smoke 和双模式预检才是此路径的验收依据。

## 如何运行

以下命令中的 `python` 指已安装 Leader/AIP 依赖的 Python；不是裸系统 Python。项目根目录运行。

### 1. 免费预检（建议先运行）

```powershell
python -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject
python -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --analysis-mode enhanced
```

不加 `--execute`：不 clone、不发送 AIP Task、不读 `.env`、不调用模型。预检确认依赖与输入，不验证真实 API Key 或模型响应。

### 2. 原静态模式（行为不变）

```powershell
python -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --execute
```

会通过 Git 获取公开仓库并做静态分析；DeepSeek 0 次。`.env` 不读取，已有环境变量中的模型 Key 不传给静态 Worker。

### 3. 显式模型增强（可能收费，需另行确认后运行）

```powershell
python -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --analysis-mode enhanced --env-file .env --execute
```

只从本机指定 `.env` 读取 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`，不复制密钥文件、不打印 Key。也可在进程私密环境中提供这三个变量，不传 `--env-file`。静态模式不允许传 `--env-file`。

Repository-only 请求只有概述和障碍，**没有论文笔记就不生成论文代码关联**。

### 4. 复用已有 Reader 结果做增强

```powershell
python -X utf8 run_reader_repository.py --reader-source "已有的真实 AIP Reader 或父记录目录" --reader-cache "该记录对应的原 PDF 缓存根目录" --repository https://github.com/microsoft/LoRA --analysis-mode enhanced --env-file .env --execute
```

仅复用经校验的旧 Reader 结果，不重新规划、搜索、下载或阅读；本轮只运行 Repository。仍不是一次全新的 S1→S2→S3。

## 请求预算、失败和结果解释

- 共享 Provider 网络重试关闭；网络/鉴权/限流失败不自动重做收费阶段。
- 空响应或无效 JSON 最多允许一次轻量补救，共最多 2 次实际请求尝试；超过预算拒绝调用。模型调用数、成功响应 token 单独记录。
- 失败/超时也可能产生费用；收到的 usage 不包含所有失败请求，不据此估算总费用。
- 同一 Partner 实例对同一输入只运行一次 Worker，成功结果缓存；执行状态不确定时拒绝再次调用。**重启新进程不具备持久去重保证**，不要把重新运行命令当作免费续跑。
- `protocolCompleted=true` 仅说明 AIP 完成；`businessReady=true` 表示有可用、匹配的业务结果。
- 增强模式另看 `enhancementSucceeded` 和 `enrichmentStatus`。模型失败但静态可用时，可以是 `businessReady=true`、`enhancementSucceeded=false`、`passed=false`，并以非零退出码提示增强未完成。
- `no_supported_claims` 表示没有足够的有效概述/关联证据，不能当成“已确认不存在关联”或“容易复现”。
- 两种模式都输出 `report.md`、`outcome.json`、`backend_outcome.json`、`acceptance.json`。增强报告明确标记状态及模型请求尝试数，不再显示“本轮关闭 LLM”。

## 免费验证

```powershell
python -X utf8 -m unittest tests.test_repository_dual_mode -v
python -X utf8 verify_project.py
.\.venv-repository\Scripts\python.exe -X utf8 scripts/verify_repository_dual_mode_static.py
```

第一项使用真实 Wrapper、Enricher、AIP SDK 与假 Provider，覆盖开关、七字段、来源校验、无效引用过滤、失败降级、报告、防重复执行及模型预算。最后一项使用真实 CodeWiki 分析临时微型本地仓库，模型仍为假 Provider；不会执行临时仓库代码。

初次集成先完成免费验收。随后按用户授权完成一次真实 DeepSeek 增强：复用旧 LoRA Reader，模型 1 次、13,265 tokens，增强通过并保留警告。详见 [真实增强验收](repository_dual_mode_live_20261019.md)。未完成正式平台链路，未整体合并或推送 GitHub。

后续已将限定代码迁回桌面项目，当前提交范围、环境与测试状态见 [桌面项目整合说明](repository_dual_mode_integration_20261019.md)。上段描述的是独立副本验收时的 Git 状态。
