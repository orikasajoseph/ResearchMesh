# S2→S3 本机集成预检（2026-10-15）

## 基线和边界

- S3 本地 checkpoint：`1f6434c`。Reader Wrapper 仅取自
  `origin/zhang/agent-wrappers@d84924d` 的 `agents/paper_reader/`。
- 新入口 `run_aip_s2_s3.py` 复用 `AipOrchestrator`、`LocalAipResolver`、
  Reader/Repository Partner 和官方 AIP SDK。默认仅执行免费预检；没有 `--execute`
  时不会发送 AIP Start，也不会加载模型。
- S3 接受可选的、由 `paper_notes.v1` 校验后投影的 `paperNotes`。Partner 顶层仍为
  七个业务字段；`requiredSkill` 和 `dependsOn` 留在 Leader。无笔记时保留仓库级分析。
- CodeWiki 仍只做静态分析。`paperCodeRelations` 需要论文和代码两侧证据 ID；
  不表示目标代码已运行或论文已复现。

## 待确认的真实样本

| 项目 | 已核对内容 |
|---|---|
| 论文 | QLoRA: Efficient Finetuning of Quantized LLMs，arXiv:2305.14314，DOI `10.48550/arXiv.2305.14314` |
| 论文页面 | https://arxiv.org/abs/2305.14314 |
| PDF | https://arxiv.org/pdf/2305.14314；HTTP 200，1,065,470 字节，26 页；本地 `inspect_pdf` 标题检查通过 |
| PDF SHA-256 | `33a4e757c19d6ea8d3cca8958fce8fe405e290272e135c99ee66065c775d5bd1` |
| 作者仓库 | https://github.com/artidoro/qlora；README 直接称其支持该论文，`git ls-remote` 返回 HEAD `7f4e95a68dc076bea9b3a413d2b512eca6d004e5` |
| 浅克隆规模 | 禁用 hooks、跳过 checkout 的单分支浅克隆：274 个文件、pack 30.57 MiB；测量后已删除临时克隆 |
| 分析范围 | 一个公开仓库的浅克隆、CodeWiki 静态结构和证据、一次可选 DeepSeek enrichment；不运行仓库命令 |

具体 S2 输入保存在被 Git 忽略的
`outputs/s2s3-qlora-prep-20261015-162552-381676/reader_input.json`。
免费联调预检记录在被 Git 忽略的
`outputs/aip-research-s2s3-20261015-162616-563575/`；
`reader_preflight.json` 确认 `pdfHashAndTitleVerified=true`、
`providerCreated=false`、`modelExecutionAttempted=false`。
PDF 的进一步 PaperQA 分块、embedding 和回答质量只能在获批的真实阅读中确认。

## 环境与下一次执行

- Leader：本工作区 `.venv`，官方 AIP SDK 2.2.0。
- Reader：独立的 `paper-qa-clean/.venv/Scripts/python.exe`（部署时通过 `--reader-python` 指定），
  Python 3.13；PaperQA 安装版本含 `57e89f722`，并有
  `sentence-transformers`、PyTorch。模型文件位于用户缓存
  用户缓存目录下的 `.cache/huggingface`；通过
  `--reader-runtime-home` 指定，不复制权重。
- Repository：本工作区 `agents/repository/.venv`，CodeWiki 2.0.0。
- Reader 配置通过 `--reader-env-file` 显式指定现有本机 `.env`；不将 Key
  写入命令行参数、输出记录或 Git。

下一次经确认后，使用同一输入并显式添加 `--execute` 运行
`run_aip_s2_s3.py`。该入口会先重复免费 Reader 预检，再启动两个本机 Partner；
S2 成功后由现有 `input_binding` 绑定 `paper_note` 与仓库 URL，再发 S3。
各阶段输出写入新建的 `outputs/aip-research-s2s3-*` 目录，失败时不自动重跑
已可能计费的 Reader 任务。

## 调用、超时和费用风险

- 固定计划复用输入：Planner 0 次；文献搜索 0 次；PDF 已缓存，正常情况下
  再下载 0 次。Repository 需要一次 GitHub 浅克隆，不执行目标仓库代码。
- Reader 的 PaperQA 会对 26 页 PDF 做分块、embedding、摘要和一次综合
  `aquery`。DeepSeek HTTP 请求数随分块和 PaperQA 内部行为变化，不能从
  当前预检精确估算；每一次可能计费。Reader Worker 超时 600 秒，Partner
  后台超时 660 秒，AIP 等待 675 秒。
- Repository enrichment 为一次逻辑 `generate_json`；共享 Provider 默认
  `LLM_MAX_RETRIES=2`，故异常时最多可能有 3 次 HTTP 尝试。
  Git 克隆超时 90 秒，Repository Partner/AIP 超时 180 秒；
  `AipOrchestrator` 的单次解析超时设置为 700 秒，整条顺序链没有
  700 秒的总墙钟上限。具体金额取决于模型价格、实际输入输出 token 及重试，本轮没有
  发送任何收费请求。
