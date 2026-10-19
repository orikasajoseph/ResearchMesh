# 单篇本地 PDF 真实阅读入口

2026-10-12：新增 run_reader.py，仅用于已保存的 Attention Is All You Need 测试 PDF。不是通用任意论文入口，不改变 Zhang Wrapper 或共享 v0.2 业务 schema。

流程：检查本地文件标题、arXiv ID、PDF 格式和文字可提取性 -> 构造单篇七字段业务任务 -> 用显式 pdf_fetcher 将本地字节交给原 PaperQA Provider -> Leader 校验返回的笔记及证据 -> 保存记录和 Markdown。标题/ID 检查不是对文件真实性的密码学验证；记录 SHA-256 和来源地址，不声称本轮联网下载核验了来源。

不调用 Planner、Semantic Scholar、ADP 或 AIP。pdfUrl 保留原文公开地址，文件路径/哈希单独保存在内部诊断中，不添加协议字段。reader_runtime.prepare_live_reader 只增加可选的 pdf_fetcher 注入参数；不传时原有行为不变。

在 ResearchMesh 根目录运行。使用已准备好 PaperQA 和离线向量模型缓存的独立 Python，不需要重新安装依赖。

仅预检（默认，不创建 Provider、不读取 Key、不调用模型）：

```powershell
& "<READER_PYTHON>" -X utf8 run_reader.py --wrapper-root "agents/paper_reader"
```

真实阅读（会向配置的 DeepSeek API 发送论文摘录/问题并消耗额度）：

```powershell
& "<READER_PYTHON>" -X utf8 run_reader.py --wrapper-root "agents/paper_reader" --execute
```

默认文件为 examples/reader_test.pdf，默认超时 600 秒。一次 PaperQA 阅读流程可能产生多次摘要/回答模型请求；模型请求数量未统计时明确为 null，不把一次 run 当成一次 API 调用。Leader 不自动重试，底层 SDK 的内部行为不在该保证范围内。取消/超时不代表零计费。

主项目 .env 提供 DEEPSEEK_API_KEY/BASE_URL/MODEL，进程同名变量优先。不输出凭据或异常原文。不要分享 .env。

输出保存在 outputs/reader-live-时间戳：record.json 包含任务、来源哈希、Wrapper 哈希、结果与运行状态；report.md 展示方法、发现、局限及原文证据。准备失败、阅读失败、中断均不伪装成功；历史目录不覆盖。模型输出及 supports 对应关系仍需人工审核，不是科研可信度或平台互联已通过。

开发时已运行不带 --execute 的实际 PDF/真实 Wrapper 预检，及 12 项入口/配置离线测试。没有运行真实收费阅读；其结果待使用者执行上面明确带 --execute 的命令后确认。
