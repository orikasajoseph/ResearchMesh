# 已知 arXiv 来源的全文核验与缓存

## 问题与范围

真实检索记录 research-live-20261012-162657-971039 返回3篇论文，选中的 Weighted Transformer Network for Machine Translation 没有 pdfUrl，但 sourceUrls 有 arXiv 摘要页面。因此 Reader 返回 FULL_TEXT_UNAVAILABLE，没有生成模型笔记。这不是检索限流。

新增 arxiv_pdf.py 是 Leader 侧全文准备模块，不是新 Partner，不修改 PaperRef 六字段、Task v0.2、AIP 或 Zhang Wrapper。它不按标题搜索、不依据 DOI 猜测 PDF、不绕付费墙；仅使用已提供的 arXiv 页面。无已知来源时保留 null 和明确失败。

## 核验流程

只处理已经按 maxPapers 选中的论文；sourceUrls 从与 PaperRef 完全对应的检索条目取得，或使用该 PaperRef 自身的 arXiv url。

1. 限定 arxiv.org/www.arxiv.org 的合法摘要页 ID，拒绝凭据、异常端口和外域，不携带团队 Key。
2. 实际 GET 官方页面，规范化标题与检索标题一致后才读取页面中的 PDF 链接。没有链接就失败，不简单拼接一个 PDF 地址当作已验证结果。
3. PDF 链接必须仍是同篇 arXiv ID；只允许同域 HTTPS 有限重定向。响应成功、文件大小受限、PDF 文件头正确，使用 pypdf 检查可解析页数及首页标题。
4. 核验通过才补入 pdfUrl。缓存按 SHA-256 命名，原子写入 outputs/pdf-cache；内部记录页面地址、页面哈希、PDF 哈希/页数/大小、核验时间。
5. 缓存续跑再次检查文件哈希和标题，成功则将本地字节交给原 PaperQA Provider，不重复下载。已有非空 pdfUrl 不被替换，仍沿用原 Reader 下载行为。

限制：标题匹配不是内容质量评价；页面/首页可疑、PDF 不可解析/缺标题、网络不可用都可能拒绝。不能保证所有论文都有可用全文。暂不扩展其他来源。解析官方页面每次请求至少间隔3秒，无自动 HTTP 重试，大小上限50MiB。

## 接入与恢复

run_research.py --with-reader 在绑定检索结果后、调用 Reader 前加入来源核验。原检索 outcome 保持原样；另存 reader_task_before_pdf_resolution、pdf_resolution 和补全后的 reader_task，科研结果中不混入来源服务私有字段。

续跑校验任务、来源和成功笔记。旧 task 只允许与原始绑定输入一致，或与已核验的新 task 一致；禁止借补全文改标题、goal、数量、依赖、原有 PDF。旧失败会重新尝试，成功笔记仍复用。核验中断也保留此前成功笔记。原运行目录不覆盖。

新增 --prepare-reader-sources 仅能与 --resume --with-reader 及已有成功检索检查点一起使用。此模式不读取 Key、不创建 LLM/Literature/PaperQA Provider、不运行 Reader，只核验 PDF 并另存 reader-ready 检查点；未补全则报告 PDF_SOURCE_RESOLUTION_INCOMPLETE。已有 pdfUrl 原样沿用，不能把准备模式称为对所有来源重新核验。

## 本机已完成的真实来源准备

本次仅访问这篇公开论文的 arXiv 页面和 PDF，复用已有计划及检索结果，没有调用 DeepSeek 或科研阅读。

- 页面：https://arxiv.org/abs/1711.02132
- 经官方页面链接验证的 PDF：https://arxiv.org/pdf/1711.02132
- 缓存：outputs/pdf-cache/01e83f2f448e5acf6169d20aef3c2c873ee7b0c5da41b4e50bccace884e94f3c.pdf
- 实际 PDF 10页、812256字节，pypdf 解析/首页标题和首页渲染检查通过。
- 新检查点：outputs/research-live-20261012-163942-267946；metadata 明确 llmUsed=false、literatureReused=true、paperReadingExecuted=false、pipelineOutcome=reader-ready。
- 用真实 pypdf 再次读取该缓存，HTTP 被测试传输层阻止；缓存哈希及 task 对应校验通过，无网络请求。

下一步由用户在项目目录执行真实阅读（可能多次模型请求和计费，开发过程没有自动执行）：

```powershell
& "<READER_PYTHON>" -X utf8 run_research.py --resume "outputs/research-live-20261012-163942-267946" --with-reader --reader-root "agents/paper_reader"
```

这仍是本地业务链路，尚非 ADP/AIP/梧桐验收；笔记的证据关联精度也需另行处理。

## 回归验证

- 新增26项离线测试，覆盖来源/标题/PDF不一致、伪PDF、域名与重定向限制、请求大小限制、缓存篡改、缓存复用、只准备模式、恢复不重复模型/搜索、已有成功笔记保留和中断。
- 单元测试使用 MockTransport、合成字节及显式 FakePdfReader，不要求在主项目轻量环境安装 pypdf/PaperQA，不把测试替身当成真实PDF验收。
- 完整验收307项、0跳过，5个模拟场景与本地HTTP通过，记录 outputs/acceptance-20261012-164354-072257。
- 实际 Literature/Reader Wrapper + FakeProvider 的16项串联检查通过，外部网络尝试0，记录 outputs/research-reader-offline-20261012-164401-898922/acceptance.json。
- 真实来源核验另用已备好的隔离环境中的 pypdf；真实缓存再校验时阻断网络，确认未重复下载。
- 本轮没有调用收费模型或重新检索论文，未提交或推送Git。
