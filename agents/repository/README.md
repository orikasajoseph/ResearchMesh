# Repository Agent

本目录的 `researchmesh_repository/*.py` 原样来自张的
`zhang/repository-agent@be99b31b3c8411ea88a96d08b0337d6049882046`。
未修改核心实现；Leader/AIP 适配在项目根目录的新文件中。

当前入口：`run_aip_repository.py`。只分析一个明确 GitHub 仓库，固定
`executeCode=false`、`allowRepositorySearch=false`，关闭 DeepSeek 增强。
输入和结果使用共享 Task v0.2，Partner 任务只有七个业务字段。
张的原生 Wrapper 不消费论文上下文；赵的新复合适配层可消费已验证的全文笔记并做无模型词面对照，
见 `docs/reader_repository_handoff.md`。未接入仓库自动搜索、梧桐或 ADP。

在项目根目录：

```powershell
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --execute
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
