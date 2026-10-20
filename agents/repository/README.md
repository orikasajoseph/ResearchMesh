# Repository Agent

业务代码沿用张的实现，不重新开发 Agent：其余五个模块与原
`zhang/repository-agent@be99b31b3c8411ea88a96d08b0337d6049882046` 一致；
`agent.py`、`enrichment.py`、`schemas.py` 已在赵提交 `d4bb9d8` 原样对齐
`zhang/three-agents-integration-preview@22863c0`。八个 Python 模块现均与该预览一致。
Leader/AIP 适配位于项目根目录，不整体覆盖双方同路径文件。

当前单阶段入口：`run_aip_repository.py`；统一编排入口：`run_aip_research.py`。
只分析一个明确 GitHub 仓库，固定 `executeCode=false`、`allowRepositorySearch=false`。
默认 `--analysis-mode static`，仅显式 `--analysis-mode enhanced --execute` 才启用收费增强。
静态和增强是同一 Wrapper 的两种真实业务模式，静态不是模拟数据；两者都不是正式梧桐联调证明。
输入和结果使用共享 Task v0.2，Partner 任务只有七个业务字段。
静态模式通过赵的复合适配层消费全文笔记、做无模型词面对照；增强模式使用张的
`RepositoryEnricher` 与团队共享 LLM Provider，保留论文与代码双侧证据。
不是用静态路径替代已验收增强，也不另建结果合同。详细说明见项目根目录的
`docs/repository_dual_mode.md` 与 `docs/reader_repository_handoff.md`。
未接入 Repository Agent 自动搜索、正式梧桐或 ADP。

在项目根目录：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --execute
```

上面是零模型的真实静态分析（会 clone）；收费增强必须另行获得同意后显式指定模式。
不加 `--execute` 只预检，不 clone、不发 AIP、不调用模型。新电脑需要增强依赖时：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/setup_repository_static.py --enhanced --execute
```

本机隔离环境已准备。新电脑先运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/setup_repository_static.py --execute
```

静态专用安装故意省略 CodeWiki 的 LLM、Mermaid、完整 CLI 依赖；
因此不是完整 CodeWiki 发行环境，也不声称 `pip check` 全部通过。
只能使用本项目已验证的 `handle_analyze_repo` 静态路径。
CodeWiki 固定 commit：`f0975ea19b17568ceb55cd5422206e1a1ae13c0b`。
缓存预热只下载分词词表，不是模型请求。无须配置 API Key。
详细说明见 `docs/repository_local_aip_static.md`。
