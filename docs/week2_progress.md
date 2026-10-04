# 赵：第二周阶段成果与接续清单

> v0.2 开发更新：requiredSkill / repository_analyze 已在赵分支实现；仓库首期要求明确地址，新增旧计划迁移与吴旧版文献传输兼容入口。当前状态和接续工作见 [交接说明](v02_handoff.md)。下文五个真实案例是 v0.1 的历史验收，不自动算作新版本通过。

> 2026-10-04 人工验收补充：五个真实规划案例已由赵在对话中逐例确认通过，已同步 case-1..5.json 与 summary.json 的 humanAccepted 标记，并在 docs/week2_live_review.md 保留限制与备注。第二周“至少五个新问题的规划结果与人工验收记录”已完成；不等于真实科研执行或平台互联完成。

> 2026-10-04 补充：已拿到吴的 v0.2 本地交付，并新增 `run_wu_aip.py`，完成“已保存计划 S1 → 吴的本地 Partner → AIP 完成确认 → 日志”的独立入口。文献检索传输通过，businessReady=false；不是实际论文检索或平台发现。下文“未拿到吴的 Adapter”相关历史描述已被这次交付更新，真实平台配置仍待提供。阅读/代码契约仍有差异，详见 `wu_local_integration.md`。

更新时间：2026-10-02。依据第二周任务规划、第一周分配表和当前代码复核；第一周进度 PDF 中“未接入 LLM”已过时，DeepSeek 在 9 月 29 日已真实运行成功。

## 本轮已完成

1. 保留已有 DeepSeek 接入与 .env，不重复部署。新增统一 Provider 配置层，Planner 仍只依赖 generate 接口；可配置 deepseek/qwen/openai-compatible。真实验证只做了 DeepSeek，其余提供商仅以本地 HTTP 替身检验请求兼容性。
2. 新增 live_api.py：真实 /plan 能接受新问题；保留 api.py 的预写 Mock 接口，避免混淆。health 不产生费用，也不承诺 Key 或网络可用。只监听 127.0.0.1。
3. requiredCapability 改为业务 skill 点分命名；检验任务类型/输出格式对应、依赖、输入定位信息、年份和数量、禁止执行仓库代码。
4. 下游输入绑定：论文记录 → PaperRef → 论文阅读；论文 source/阅读笔记/仓库 URL → 代码分析。保留依赖并发与失败隔离，不再传“来自 S1”占位说明。
5. 结果检查补充论文来源、真实阅读层级、证据和仓库许可；区分摘要/全文、模拟/真实结果。
6. 五个新问题真实 DeepSeek 规划通过（5 次请求，8,355 tokens）。不是五个 Mock 场景，也没有执行真实科研 Agent。
7. 官方 SDK 2.2.0 结构化消息和完成确认组件已实现，并通过本地状态机/SDK HTTP 替身测试。尚未接到实际 ADP/Partner。
8. 建立独立 .venv，Python 3.12.14，满足官方 SDK 的 Python>=3.10 要求；未修改全局 Anaconda、原 .env 或你的 VS Code 设置。

## 证据位置

- 真实规划：`outputs/week2-live-20261002-213600-256091/summary.json` 及 case-1..5.json。
- 本轮最终自动验收：`outputs/acceptance-20261002-214544-948177/SUMMARY.md`、test_log.txt；95 项测试通过、0 项跳过，另有 5 个 Mock 场景和本地 HTTP 检查。是本地测试，不是平台证明。
- AI 内容复核与已完成的人工验收表：`docs/week2_live_review.md`（2026-10-04 五例确认通过）。
- 队友接口稿：`docs/InterfaceContract_v0.1.md`。请全组确认变更，不要继续用旧的下划线 capability 或 selection 输入。

## 仍未完成及原因

| 工作 | 当前状态/原因 | 需要谁提供什么 |
|---|---|---|
| 真实 ADP Resolver | 尚未接入，只有本地 Mock Resolver | 吴：可运行发现示例、返回样本、业务 skillId 映射、endpoint 选择规则 |
| 真实 AIP 身份/传输/平台留痕 | 本地组件可测，真实平台未验收 | 吴：AIC、证书本机配置方式、Adapter、AMP；不要在聊天发私钥 |
| Literature/PaperQA/CodeWiki Wrapper | 未拿到可运行代码；选定开源项目不等于完成 Wrapper | 张：仓库或本机路径、启动方式、真实成功/失败结果、超时/限流约定 |
| 接统一团队 Provider | 赵侧兼容层可用；队友实现接口尚未对齐 | 张：Provider 的函数签名/配置约定，确认复用还是替换适配层 |
| 真实检索→阅读→代码分析→汇总 | 缺真实 Agent 与平台入口，不能用 Mock 冒充 | 上述材料齐备后联调 |
| 流式、取消、重试/切换、动态重规划 | 当前无自动实现；超时记录远程状态未知 | 与吴 Adapter 确认生命周期和幂等策略后补齐 |
| 团队接口冻结 | 仍待全组对齐阅读/代码等接口差异；五例规划人工验收已完成，不再作为此项阻碍 | 赵/吴/张共同确认共享约定 |

这些阻碍不是“没有 LLM”。第一周遗留的真实规划新问题验收已补做；真实工具、平台互联和最终报告的科研可信性仍不能判为完成。

## 你现在怎么运行（VS Code）

项目目录仍为 `C:\Users\orika\Desktop\智创赛\ResearchMesh`。无需激活 conda，终端直接执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_demo.py
.\.venv\Scripts\python.exe -X utf8 verify_project.py
```

上面不调用付费模型。真实新问题规划（会消耗额度，不执行 Agent）：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_planner.py
```

可继续使用原来的 run_deepseek_planner.py。若使用右上角运行按钮，手动在 VS Code 选择解释器 `.venv\Scripts\python.exe`；原 Anaconda 可以运行大部分旧代码，但不能运行官方 SDK 2.2.0。

真实规划网页 API：

```powershell
.\.venv\Scripts\python.exe -X utf8 live_api.py
```

打开 http://127.0.0.1:9041/docs，POST /plan 填 `{"userQuery":"你的研究问题"}`。仅本机使用，无远程鉴权；不要发布公网。按 Ctrl+C 停止。原 api.py 仍只支持预写问题。

## 给队友发的最小请求

“赵侧 DeepSeek 真实规划、skill 约定和输入绑定已可用，请按 InterfaceContract_v0.1.md 确认。吴请给 ADP/Adapter 可运行代码与脱敏样本、skill 映射及证书本机配置方式；张请给三个 Wrapper 的代码位置、启动方式和成功/失败结果样本。拿到后优先联调 literature.search，再接阅读和代码分析。无需再等待模型 Key。”

## 依赖可复现

本机 .venv 已装 requirements-dev.txt、requirements-llm.txt 和官方 ACPs SDK 2.2.0。当前包源未提供 2.2.0，因此从已有官方源码提交 `3985c1330209f075c124669cc9d31f0e75448140` 安装；SDK 目录无跟踪文件改动。requirements-aip.txt 固定相同 Git 提交，其他机器需 Python>=3.10 和 Git 才能安装。不要把本机 .venv 打包发给队友。
