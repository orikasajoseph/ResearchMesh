# 张的 Literature Wrapper 接入说明

> 2026-10-04 23:27 更新：赵侧已经使用本机配置的 Semantic Scholar Key 完成一次实时检索。AlphaEvolve 返回并保留 5 篇论文，统一结果校验通过；单次请求 1587.145 ms，attempts=1、cacheHit=false。未调用 DeepSeek、ADP、AIP、Reader 或 Repository Agent。见 [脱敏实测记录](evidence/literature_live_20261004.json)。下文离线阶段记录保留，不再将 Key 缺失视为当前阻碍。

更新：2026-10-04。此文替代旧进度中“尚未取得 Literature Wrapper”的描述；不代表 PaperQA、CodeWiki 或梧桐已经接通。

## 本轮实际完成

- 已检查张的 `questionanswer2.docx`、Python 源码及成功、空结果样例。张反馈在线 Semantic Scholar 测试通过；赵侧本轮验证是历史样例回放和离线 Wrapper 调用，不是重复在线验收。
- 新增 `literature_adapter.py`，保持张的 `literature_search.v0.1` / `paper_records.v1` 接口不变，在 Leader 的 v0.2 与 Wrapper 之间显式转换。
- 新增 `run_literature.py`：读取一份只检索的计划，显式选择 replay/live，输出统一结果、可读论文清单与供 Reader 使用的 PaperRef。它不是 AIP Adapter，也不使用 ADP。
- 新增 27 项测试：输入映射、上下限、去重、错配、错误、超时、部分记录、后续绑定和官方 SDK Product 序列化；全项目 152 项测试通过、0 跳过，5 个模拟场景与 HTTP 检查通过。
- 已安装可选依赖并检查无依赖冲突；`verify_literature_delivery.py` 调用了张的真实 Python Wrapper 和 Provider，但上游 SDK Client 是明确的离线替身。两份交付样例均能通过双方模型检查。
- 已用现有 DeepSeek 做 v0.2 六场景真实规划回归，6 次请求、10,630 tokens，自动语义检查全部通过。未执行科研 Agent；`humanAccepted` 仍为 false。

## 字段映射

| ResearchMesh v0.2 | 张 Wrapper | 说明 |
|---|---|---|
| `subtaskId` | 编排上下文保留 | 不当作全局 taskId；另传独立 requestId 用于关联 |
| `input.query` | `query` | 原样传递，不额外用 LLM 改写 |
| `constraints.maxResults` | `limit` | 1–100；没给时本入口默认 5 |
| `yearFrom` / `yearTo` | `filters.year` | 2023:2025、2023:、:2025 或单年 |
| `requiredSkill` / `dependsOn` | 不发送给 Wrapper | 属于 Leader 发现与调度上下文 |
| `papers[]` | `items[].kind=paper` | 内层 data 保留题名、DOI、URL、PDF、年份、摘要等 |
| 作者对象数组 | 姓名数组＋`authorRecords` | 共享 authors 用姓名，原作者 ID 另存 |
| `metrics` / `provider` / `total` | 独立 diagnostics | 总命中数不是本次返回数；不混入 AIP 状态 |

未知约束会拒绝，避免悄悄丢失筛选要求。当前入口仅允许一个无依赖的 literature_search 任务；多任务计划会明确拒绝，不能只执行第一项却声称整份计划完成。

## 两个需要组员知道的问题

1. **repositoryUrls 语义不一致**：张的实现把 arXiv、PubMed、PDF 等论文地址放在 repositoryUrls；赵侧同名字段表示代码仓库。适配层将非 GitHub 论文地址保存在 `items[].data.sourceUrls`，只把明确的 HTTPS GitHub 仓库根地址留在 repositoryUrls。没有仓库就保持空数组，不从摘要抓链接、不猜仓库、不构造 PDF。
2. **缓存 key 包含 requestId**：相同请求 ID 可命中缓存；新任务 ID 的同查询不能复用缓存。本轮已离线观察到。建议张将缓存 key 限于查询、筛选和 limit，requestId 只用于响应关联。本轮不修改原交付包。

PaperRef 继续保留 DOI/url/pdfUrl；本次产生 reader_inputs.json 不代表调用了 Reader。对于缺少 PDF 的论文，后续 Reader 仍需确认全文获取方式，不能默认精读完成。

## 错误和 AIP 责任边界

- 本适配返回 `{result, error, diagnostics}`，这是本地函数返回值，不是新增网络协议。
- `NO_RESULTS` 暂按“成功完成检索但零篇”转换为 `items=[]` 和明确 warning；不编造论文，也不说相关研究不存在。交给吴时需确认这个空结果策略；不能直接当全组已冻结的 AIP 状态规则。
- RATE_LIMITED、INVALID_REQUEST、PROVIDER_UNAVAILABLE 保留安全错误码和 retryable；未知错误归 UNKNOWN_PROVIDER_ERROR。底层异常正文不写入共享日志，防止包含凭据。
- 状态与数据互相矛盾、requestId 不匹配、无任何有效论文则拒绝；部分坏记录剔除并计数，不伪造缺失值。
- 吴的 AIP 层应按官方 SDK 处理 TaskCommand/TaskResult、任务关联、身份和完成确认；结果 Product 装适配后的 ResearchResult。SDK Product 序列化测试通过不等于真实网络或平台验收。
- 张文档中把 subtaskId/taskType/summary/items/warnings 泛称为“AIP 外层”的说法需要澄清：这些是我们约定的业务内容，官方 taskId/status 才是 AIP 字段。

## 本机先看一次回放

在 VS Code 项目终端执行，路径要指向包含 researchmesh_literature 子目录的交付根目录：

```powershell
$deliveryRoot = 'C:\Users\orika\Desktop\智创赛\张\ResearchMesh_LiteratureAgent_AIP_Handoff\literature_agent_aip'
.\.venv\Scripts\python.exe -X utf8 run_literature.py --mode replay --plan examples/literature_alphaevolve_v02.json --fixture-input "$deliveryRoot\examples\literature_success_input.json" --fixture-output "$deliveryRoot\examples\literature_success_output.json"
```

终端会给出 outputs/literature-replay-* 目录，打开 report.md 看可读论文清单。这里只回放张的 AlphaEvolve 记录，不消费 API，不下载论文。回放会严格匹配 query、年份、limit、requestId，不能拿同一历史结果冒充另一个问题的答案。

检查交付包本身（不联网）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-literature.txt
.\.venv\Scripts\python.exe -X utf8 verify_literature_delivery.py --wrapper-root "$deliveryRoot"
```

## 真正调用时需要做的事

由持有凭据的人在各自本机 ResearchMesh/.env 配置 `SEMANTIC_SCHOLAR_API_KEY`，不要发到聊天、共享文档或 GitHub。赵侧已经配置并实测通过；代码仓库不会携带该 Key，队友仍需自行配置。

```powershell
.\.venv\Scripts\python.exe -X utf8 run_literature.py --mode live --plan examples/literature_alphaevolve_v02.json --wrapper-root "$deliveryRoot"
```

此命令真实请求 Semantic Scholar，不调用 DeepSeek。为限定首次联调请求量，入口设置单次 SDK 请求、不自动重试、单请求 20 秒和外层 45 秒超时；没有更改张内部默认重试配置。429 后应等待并协调限流，不要连续重跑。实时失败不回退成历史成功结果。

也可以先用 run_planner.py 生成自己的“只检索”计划，再把对应 plan.json 传给 --plan。暂不在本入口运行复杂三 Agent 链。

## 证据及剩余工作

- 本地总验收：outputs/acceptance-20261004-231420-405904。
- 张成功样例回放：outputs/literature-replay-20261004-231159-046153。
- 交付包导入、两样例和上游替身检查：outputs/zhang-delivery-20261004-231538-075411，含源代码 SHA256。
- 六场景真实 Planner：outputs/week2-live-20261004-231426-009456。
- 队友可查看仓库内的 [六场景真实规划记录](evidence/v02_live_regression.json)，无需拿到赵的 outputs 文件夹。
- 源码和数据未整体复制进赵的 Git 分支：保留张交付归属，不上传 .env/私钥；队友机器需先取得同一交付包，或等张提交其开发分支后合并。

下一步：将已实时验证的文献业务适配交给吴接入 AIP/ADP；等待并对接 Reader 与 Repository Wrapper。此次使用已有计划，尚不是自然语言实时规划与检索的一键串联，也不代表真实梧桐注册、发现、跨端闭环完成。

实时本地记录：outputs/literature-live-20261004-232717-108835，包含 plan.json、outcome.json、metadata.json、reader_inputs.json、report.md。5 条 pdfUrl 均为 null；代码仓库均未确认。未下载或阅读全文，相关性与论文质量仍需后续判断。1587.145 ms 只是一次调用观测，不能作为平均性能。

新版 Planner 人工复核提醒：案例 2 的模型补出了 arXiv PDF 地址，尚未联网验证可用性；案例 5 写了“最相关”，当前绑定器实际只按结果顺序取前 N 篇，不具备重排评分。自动通过只说明既有断言通过，不等于这些内容已获得人工科研验收。
