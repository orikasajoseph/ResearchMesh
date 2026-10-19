# 赵侧独立工作收尾：单入口、共享证据和换机说明

2026-10-12。继续补齐可独立完成的工程工作；未修改张的Reader源码、吴的交付、共享业务Schema或其他分支。没有新模型、论文API、PDF下载、平台请求、commit或push。

Git状态更新：以上是独立开发阶段记录。本文件现纳入赵分支交付，实际提交/推送状态以远端分支为准；共享范围与接收方式见GitHub_sync_20261012.md，不合main、不包含本机PDF或完整outputs。

本轮回归：425项自动测试、0跳过，5个模拟场景、本地HTTP全部通过；记录 outputs/acceptance-20261012-203952-625118（以后复测以最新目录为准）。新增21项离线测试不等于21次真实科研请求。

## 本轮交付及真实程度

| 内容 | 当前状态 |
|---|---|
| 更新 README / Reader 状态 | 已与真实Reader成功记录对齐，保留历史离线记录 |
| 单入口 run_aip_research.py | 已开发并通过离线替身测试；默认不执行；尚未新增付费整链实测 |
| 分阶段保存、防重复执行、检查点锁 | 已测试；失败或运行状态未知的收费阶段不自动重放 |
| PDF 独立核验 aip_pdf_worker.py | 已测试分支，并在现有Reader运行时确认可启动；不是新增真实下载验收 |
| 脱敏证据 export_team_evidence.py | 可从已有真实记录导出固定白名单摘要，无模型结论、PDF、Key或本机路径 |
| Git候选只读检查 | scripts/prepare_team_sync.py；不暂存/提交/推送，不声称替代人工审查或历史泄密审计 |
| 换机运行说明 | 本文提供目录/运行时/依赖边界；未在第二台电脑安装验证 |

新的单入口只覆盖 **1个独立检索→阅读结果中的1篇**。不支持自动挑选“最相关/最代表论文”，按检索返回顺序绑定；首篇缺可验证arXiv来源就停止，不偷偷跳到其他论文或编造全文链接。Repository、平台ADP、认证、最终UI不在本入口范围。

## 你在VS Code里怎么运行

终端工作目录为 ResearchMesh。先做完全免费的范围检查：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_research.py --plan "outputs/research-live-20261012-164848-827095/plan.json"
```

它只检查保存计划是否适用，不代表Key、服务、模型或PDF已联通。默认不弹出问题输入，也不执行任务。

明确允许重新检索、下载和收费阅读时，用以下命令。**本轮未执行它**，Reader可能多次请求DeepSeek：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_research.py --reader-root "agents/paper_reader" --reader-python "<READER_PYTHON>" --execute
```

然后在终端只输入自然语言，例如：

> 检索2017—2018年Transformer用于机器翻译的论文，最多3篇，阅读检索结果中的1篇，概括方法、发现和局限，并保留原文证据。不分析代码仓库。

若计划需要澄清或超出两任务/单篇范围，计划会保留，但不会启动检索/阅读。若已有适用计划，加 `--plan "实际plan.json路径"`可跳过模型规划；`--llm-adapter team`是可选公共Provider路径，不默默替换默认。

按阶段生成 `outputs/aip-research-时间戳/`：

- `plan.json` / `planner_metadata.json`：计划与模型统计（使用已有计划时无新Planner统计）。
- `literature/`：官方AIP与真实检索记录。
- `pdf_resolution.json`：已知arXiv源和缓存哈希核验；已有失败证明需保留时，新提供的证明存为provided-pdf-哈希.json，不覆盖旧文件。
- `reader/`：单篇Reader预检、任务、模型后台记录与AIP响应。
- `pipeline.json`：阶段状态；`literature.log` / `pdf.log` / `reader.log`查看运行进度。
- `research_run.json` / `report.md`：各阶段成功后统一汇总。

汇总器acceptance中的newCalls=0仅表示汇总本身没有重放任务；不能用它宣称此前Planner/检索/Reader免费。新入口的pipeline字段单独记录执行范围，准确模型请求次数与费用仍未知。

`--pdf-proof "已有pdf_resolution.json"`可以复用本机缓存，不重新下载；只有对应同一篇论文且缓存哈希一致才接受。证明不匹配时失败，不自动联网找替代品。PDF新下载只在已知arXiv来源范围核对标题、原文及哈希，不从DOI或题目猜地址。

## 失败与续跑边界

可用同一入口 `--resume "outputs/aip-research-实际目录"`，同时提供同样的Reader参数及`--execute`。它复核计划哈希，跳过已完成阶段，校验上游记录后再进入尚未开始的步骤。

最适合的情况是：检索已成功，但PDF证明缺失、路径/来源校验未通过，Reader尚未启动；修正`--pdf-proof`后继续，不再规划和搜索。

**不自动重放 failed/running 的阶段**，不将超时当作“没付费”。检索的已有有界限流策略仍保留；新入口不叠加无限重试。PDF阶段失败可提供新的有效`--pdf-proof`绕过下载重试，不能宣称缓存结果来自本次新下载。

若Reader后台成功但Leader未收齐协议结果，应检查`reader/reader_journal.json`，用原`run_aip_reader.py --resume`的明确完成缓存取回路径；新入口暂不自动接管这个恢复过程。失败/中断的Reader是否重试应人工确认计费风险，不能连续重跑。

`pipeline.lock`防止同一目录并发续跑；异常终止遗留锁时先确认旧Leader/Partner/Worker确已停止，再处理锁，不盲目删锁。取消只清理自己启动的进程树，不能撤销服务商已经接收的模型请求或费用。

## 换一台电脑：需要准备什么

1. 获取赵最新开发分支；这轮新增文件尚未推送，远端不会自动出现。Reader源码目前仍是显式外部副本；先从张的 `zhang/agent-wrappers` 对应提交获取，已验收提交为 `d84924d14ecb02a8ae19779dad11f2488df6a9f5`，确认其目录包含 `researchmesh_paper_reader`。不要把主项目空的 `agents/paper_reader` 当完整实现。
2. Leader使用Python3.12独立虚拟环境，安装项目已有 requirements.txt、requirements-llm.txt、requirements-literature.txt、requirements-aip.txt；回归测试另装requirements-dev.txt。固定AIP SDK2.2.0和其官方提交；安装命令从仓库根目录执行。不要覆盖系统Anaconda或队友GPU环境。
3. Reader使用第二个独立Python3.12环境，安装张Wrapper自己的requirements.txt及pypdf，并确认paperqa、sentence_transformers、torch可导入。张当前PaperQA固定提交 `57e89f7223b0960d5ee5ea048c69e3c47e088572`。Windows依赖/安装结果需按新机实际情况验收；现有环境曾成功不等于新机必成功。
4. 提前准备embedding缓存：`sentence-transformers/multi-qa-MiniLM-L6-cos-v1`；Reader环境目录下 `runtime-cache/huggingface` 及需要的PaperQA/tiktoken缓存。现有代码要求离线可用，不会在阅读时自动下载模型。不要把几个GB的虚拟环境或缓存上传GitHub；新机缓存准备应由张提供可复现说明，不能假定复制单个模型文件就足够。
5. 本机项目根目录配置.env中的Semantic Scholar与DeepSeek配置；Key、证书不提交。Reader当前读取DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL/DEEPSEEK_MODEL，不能仅配置另一套LLM_*名称便声称它已读到Key。
6. 新入口显式传自己的`--reader-python`和`--reader-root`，不硬编码orika路径。主Leader环境不必装torch，Reader环境不必装AIP SDK；PDF核验工具也不导入SDK。新机先运行离线回归，再做免费预检，最后明确授权收费实测。

## 发给队友的证据摘要

`docs/evidence/local_aip_verified_20261012.json`会保存经过原始记录关联/哈希复核的检索与阅读数量、耗时、离线回归数量及来源文件哈希。摘要特意省略研究问题、论文文本/模型结论、AIC、任务号、本机用户名和路径；必要完整日志在会上单独脱敏审核，不把整个outputs上传。

摘要是两次已有真实本机执行的证据，**不证明新增单入口真实整链已跑通、不证明平台发现或证书身份**。它能随代码共享，但当前仍未推送。

上传前只读检查已执行；`examples/reader_test.pdf`被标为需人工确认授权/来源，建议不纳入这轮上传。测试中的模拟私钥头曾触发模式检查，已用字符串拼接保留测试但避免被误认成文件里真的保存了私钥；没有放宽检查规则。已配置Key的值不会出现在检查输出里，当前检查也不等于历史或远端全量泄密审计。

下一步仍需：你人工确认v0.2计划与科学证据、全组冻结接口、吴正式平台交付、张Repository交付，以及明确授权后的新单入口付费验收。不要把“独立工程收尾”写成赛事项目全部完成。
