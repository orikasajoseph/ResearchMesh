# 新问题 → 三智能体统一入口（2026-10-20）

入口仍是 `run_aip_research.py`，没有另建 Planner、通信协议或共享 schema。
兼容原来的检索→阅读两阶段；当真实 Planner 生成三个合规任务时，自动继续仓库分析。
当前 Task v0.2 / `requiredSkill` / `repository_analyze` / 七个 Partner 业务字段不变。

## 本轮完成与未验收边界

- 已实现新问题规划→S1 文献检索→已知来源 PDF 校验→S2 全文阅读→仓库资源绑定→S3 分析→Markdown/JSON 报告。
- 复用原来本机官方 AIP 的三个独立阶段入口；不是正式 ADP、MQ、Group 或证书身份联调。
- 已做免费自动测试：真实 Planner 解析/校验、输入绑定、官方 SDK 与内存 ASGI；科研后端及模型响应由测试替身提供。
- 首次开发只跑离线测试。随后用户已授权并手动完成一次LoRA新问题的真实Planner→检索→全文阅读→仓库静态分析→报告，记录 `outputs/aip-research-20261020-135700-393904/`；三业务与三AIP任务通过。后续迁入的OpenAlex/资源配置/动态平台接缝本轮仅做离线验收，不能把该旧LoRA记录当作这些新增能力的真实联网验收。
- 默认仓库 `static`：确定性静态分析与论文笔记词面证据对照，不声称理解算法等价或完成复现。
- 显式 `enhanced`：静态结果之上调用已有 DeepSeek 增强，最多两次请求尝试，失败保留静态结果并明确标记降级。

## 免费验证（现在可以运行）

在项目根目录终端执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_three_agent_entry.py
.\.venv\Scripts\python.exe -X utf8 verify_project.py
```

第一条保存 `outputs/three-agent-offline-<时间>/acceptance.json`、`test_log.txt` 与 `SUMMARY.md`。
第二条验证整个原项目、五个模拟场景及本机 HTTP，不访问外部科研服务。

统一入口默认也不会执行任何 API：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_research.py
```

已保存三阶段计划可用 `--plan 路径` 做免费格式/范围检查；不加 `--execute` 不会启动 Agent。

## 真实运行（可能计费，获得同意后再执行）

需要本机 `.env` 的模型/检索配置，以及已安装依赖和缓存的 Reader、Repository 独立 Python。
沿用已经验收的 Reader 根目录与运行环境，通过参数明确指定；代码没有新增个人绝对路径。
也可在**当前终端**设置 `RM_READER_ROOT`、`RM_READER_PYTHON`，后续省去这两个参数；不要把个人路径当作团队公共配置提交。
Repository 默认使用项目内 `agents/repository` 与 `.venv-repository/Scripts/python.exe`，可通过参数覆盖。

```powershell
$env:RM_READER_ROOT = "你的 Paper Reader Wrapper 根目录"
$env:RM_READER_PYTHON = "你的 Reader 环境\Scripts\python.exe"
.\.venv\Scripts\python.exe -X utf8 run_aip_research.py --execute
```

在“请输入研究问题”后输入自然语言，不需要 JSON。建议第一次选一个已知有开源代码、arXiv 全文可用的方向，并提供明确仓库地址以降低外部资源查找的不确定性：

> 检索2021—2022年低秩适配LoRA用于大语言模型参数高效微调的论文，最多3篇，按返回顺序阅读其中1篇，提取方法、发现和局限并保留原文证据，再结合全文笔记静态分析 https://github.com/microsoft/LoRA 的结构、依赖和复现准备障碍。只分析1个仓库，不执行代码。

如还需要额外模型增强（额外计费），明确开启：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_research.py --repository-analysis-mode enhanced --execute
```

模型规划最多两次；Reader/PaperQA 可能多次模型请求，不能用“一次运行”估计成“一次 API”。报告整理不额外调用模型。
这些命令是运行说明，写在文档中不代表本轮已经执行或已获收费测试授权。

## 如何自动接上 S3

1. 保留原始三任务 `plan.json` 与真实 Planner 元数据；不把预写样例当作真实新问题规划。
2. S1 原始 AIP 结果和后台记录一致后，才允许下载/校验已知 arXiv PDF。
3. S2 输入来自本轮 S1 与验证过的 PDF，必须产出全文笔记、具体结论证据与匹配来源。
4. 地址优先级：用户在原问题明确给出的 GitHub 根地址 → 公开资源解析（候选来自结构化来源、论文页链接或 GitHub 公共搜索）。
5. 自动候选必须在仓库 README 中同时找到论文标题和 arXiv/DOI 标识符，排除明确非官方重实现声明。不访问任意候选网页，不跟随 HTTP 跳转；最多8个公共请求、单请求6秒，并有子进程总时限。
6. 找不到、限流、身份不匹配时保留 S1/S2，S3 不运行。不从模型 prose 猜 URL。用户给的仓库仅表示用户选择，并不证明它与论文相关；报告会提示。
7. S3 将完整匹配的 `papers`、`paperNotes` 与资源地址交给现有业务适配器，仍由原 `repository.analyze` 本机 Resolver/AIP 执行。`requiredSkill`、`dependsOn` 不传 Partner。
8. `repository_binding.json` 保留原依赖和已验证 S2 的 AIP taskId；独立 S3 执行子计划把已解决依赖清空，不重跑 S2，也不修改原完整计划。
9. 三个 AIP taskId 必须不同，最终结果须匹配后台记录、AIP 数据产物与绑定输入。协议完成不等于业务有效。

资源查找是 Leader 的受限公开资源工具，**不是** Repository Agent 的无边界搜索；Partner 仍只收到一个明确 URL，`allowRepositorySearch=false` / `executeCode=false` 不变。
README 身份线索也不保证作者官方身份、最新 commit 代码等价或复现成功，最终结论待人工核验。

## 当前明确限制

- 一次独立检索、按返回顺序阅读1篇、分析1个明确 GitHub 仓库；没有多篇智能排序。超过支持数量会在检索/阅读前停止，不擅自缩减用户要求。
- 全文校验仍沿用已验收的**已知 arXiv 来源**流程。任意出版社、需登录全文、只有摘要、没有可靠来源时不会冒充全文已读。
- 一个问题不一定能找到全文或仓库，因此代码可自动串联不等于任意科研问题都能成功。
- 本机阶段顺序启动，尚非三个长期驻留的跨电脑 MQ 服务；正式平台联调工作未被这一入口替代。
- 未实现桌面聊天网页；当前通过 VS Code 终端输入问题，结果是可阅读报告，不要求用户编写 JSON。

## 产物与中断保护

`outputs/aip-research-<时间>/` 包括：

- `plan.json`、`planner_metadata.json`：规划与调用统计（保存计划模式不新建模型统计）。
- `pipeline.json`：各阶段状态、文件指纹与本次运行配置。
- `literature/`、`reader/`、`repository/`：本轮 AIP 消息、后台结果、阶段报告。
- `pdf_resolution.json`：PDF 来源与缓存哈希。
- `repository_resource_input.json`、`repository_resource.json`：资源选择与来源核验。
- `repository_binding.json`、`repository_plan.json`：原始依赖、已绑定业务输入与独立 S3 子计划。
- `research_run.json`、`report.md`：三阶段报告；失败时也保留已验证部分和固定错误码。

已完成阶段会复核文件哈希、来源与协议后复用；完整检查点可用同样运行参数加 `--resume 输出目录 --execute` 再整理，不重新计费。
失败或中断且远端结果不确定的阶段**不会自动重跑**；不要手改状态绕过保护。
续跑不能默默改变 Reader/Repository 环境、PDF 证明或增强开关。`--resume` 不是“自动重新尝试失败收费 API”的按钮。
`.env`、模型 Key、证书、缓存、outputs 不提交 Git；本轮未涉及 GitHub 推送。
