# 平台业务入口 v1：给吴、张的接线说明

本文件定义赵侧业务调用接缝，不定义第二套通信协议。Task/Result仍为已对齐的业务v0.2。
统一业务入口已纳入赵分支，2026-10-22核对的已推送基线为 `cd56780`（早期交接提交为 `ebb4028`）。
实际部署版本需另行核对；当前正式Literature Leader单任务已通过，Reader/Repository平台接入仍待验收。
完整状态和证据边界见 [当前进度](../current_status_20261022.md)。本轮修复不改变下列业务签名或七/六字段协议。

## 一、稳定调用签名

```python
from business_execution import execute_business_task, BusinessExecutionError

result = await execute_business_task(
    task,                         # 严格七字段业务字典
    skill="literature.search",    # 平台路由上下文，不塞回 Task
    backend=executor,             # 已有业务执行器，async (task) -> result
    allow_execution=True,         # 缺省 False：拒绝执行，避免意外计费/联网
    repository_analysis_mode="static",  # Worker配置，不能放入 Task
    timeout_seconds=120,
)
```

Task只有 `schemaVersion/subtaskId/taskType/goal/input/constraints/outputSchema`。
Result只有 `schemaVersion/subtaskId/taskType/summary/items/warnings`，没有额外业务status。
`requiredSkill`、`dependsOn`属于Leader；sessionId、taskId、Agent AIC属于平台上下文。
输入多出这些字段会在执行前拒绝；不能把业务subtaskId当作平台去重taskId。

业务入口先验证任务/技能/约束，后调用后端，最后验证字段、来源和证据再交回平台。
Wrapper原生Repository结果必须先经过现有repository_adapter标准化，不能直接返回repository_record。
只有结果验证后，平台才能进入产物/完成确认；入口自身不发AIP Complete，不创建Group，不做ADP发现。

## 二、执行器来源

### Literature

```python
from aip_literature_partner import make_literature_executor
executor = make_literature_executor(wrapper_root, prepared=prepared, on_outcome=save_backend_record)
```

复用同一 `execute_live_task`、Semantic Scholar Provider、可选OpenAlex及ResourceResolver。
构造执行器不会联网。`prepared`可由运行环境调用`prepare_live_wrapper`准备，后者仅检查本机配置和包。
`on_outcome`保留HTTP、备用来源与资源解析诊断，作为验收证据；业务字段看起来正确不等于真实来源已成功。

### Reader：隔离Worker（与已跑通本机链兼容）

```python
from aip_reader_partner import create_reader_business_executor
executor = create_reader_business_executor(
    record_dir=record_dir,           # 先创建全新的本项目outputs内任务目录
    reader_python=reader_python,     # 已安装PaperQA/torch等且缓存齐全的Python
    reader_root=project_root / "agents/paper_reader",
    dynamic=True,                   # 接收Task后才准备bound_task.json与验证过的PDF
    env_file=private_env_file,
    runtime_home=runtime_cache,      # 可选；默认Reader Python的runtime-cache
)
```

每个独立Task使用一个新的record_dir及执行器；同一Task的重复消息复用执行上下文，不重新构造Worker。
没有固定 `reader_input.json` 也可创建动态执行器。实际收到本轮Task后，Worker才核验已知arXiv来源、标题、PDF哈希与缓存，再调用原Reader。
当前并不支持任意出版社URL的自动全文验证；资源未核验时明确失败，不把摘要或链接当作全文。
不加Worker的`--execute`只做本机配置预检，不下载PDF，并明确记录“全文未验证”。
此隔离路径每个Task一个进程，不保证跨进程embedding常驻。

### Reader：平台长期运行、复用embedding

在Reader运行环境启动时只准备一次现有Agent：

```python
from reader_runtime import prepare_live_reader, make_persistent_reader_executor
prepared = prepare_live_reader(reader_root, env_file=private_env_file,
                               runtime_home=runtime_cache, pdf_fetcher=verified_cache_fetcher)
executor = make_persistent_reader_executor(prepared=prepared, prepare_task=verify_current_pdf)
```

`verify_current_pdf(task)`必须核验本轮论文/PDF，并让`verified_cache_fetcher(url)`只读取其已验证的缓存字节。
可复用`aip_reader_inputs.prepare_bound_reader_input`，它**可能下载PDF**，仅在已获执行授权的任务回调中调用。
不能让该fetcher按未经核验的任意URL直连下载，也不能省略prepare回调。
此后多Task复用同一个`prepared`和executor，不在每次调用中重新初始化PaperQA/embedding。
执行器串行化准备和阅读，避免可变诊断/缓存关联到另一篇论文。底层模型请求次数仍可能多于一次。
该方案是运行于Reader依赖环境中的可注入业务接缝；吴需决定实际进程部署/IPC，不能直接要求Planner轻量环境加载PaperQA。

### Repository

```python
from aip_repository_partner import create_repository_business_executor
executor = create_repository_business_executor(
    record_dir=record_dir, repository_python=repository_python,
    wrapper_root=project_root / "agents/repository",
    dynamic=True, analysis_mode="static",
)
```

该工厂不依赖预先存在的repository_input.json；收到Task后才写入，随后复用原隔离Worker。
增强需**同时**配置工厂 `analysis_mode="enhanced"`、私密`env_file`及业务入口 `repository_analysis_mode="enhanced"`。
这不是Task新字段，也不会因业务goal写了“增强”就自动开启计费。
静态工厂移除模型凭据、不读.env；增强使用同一Wrapper/Enricher/共享LLM。
业务返回保留 `enrichment`、warnings与双侧证据：增强失败不抹掉静态事实，不能把协议completed等同于增强成功。
带论文笔记但未产生合法`paperCodeRelations`时，不得报告论文—代码关联分析完成。

## 三、平台层职责和费用防护

吴继续负责AIC/证书、ACS/ADP、Group/Inbox/AMQP、官方AIP状态转换及持久化去重。
去重键依已对齐约定为sessionId + taskId + Agent AIC；backend实例内的缓存只是附加防线，不能代替持久化记录。
同一Task重复Start不能新建执行器、目录或收费请求；断线后不能只靠subtaskId判定重做。

业务默认等待预算：Literature 120s、Reader 660s、Repository static 180s / enhanced 300s。
Leader/平台等待应留出传输、PDF准备与完成确认余量，具体由吴结合SDK确定；不要覆盖成统一20/90s。
`timeout_seconds`可明确覆盖，但不是平台协议超时字段。
超时抛出 `BusinessExecutionError(code="BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED")`，
`remote_execution_uncertain=True`；不重试，不保证远端已停止或不会收费。
取消传递`asyncio.CancelledError`，由平台持久化取消/不确定状态；它不等于第三方API撤销。
其他失败只返回固定错误码，不外传原始Provider异常、Key或本机.env内容。

## 四、备用检索与资源开关

代码已迁入，但保持原Semantic Scholar默认路径，避免改变已验收运行的联网范围。
在本机私密.env或进程环境显式启用：

```dotenv
LITERATURE_SEARCH_PROVIDER=semantic-scholar
LITERATURE_OPENALEX_FALLBACK=1
LITERATURE_RESOLVE_RESOURCES=1
LITERATURE_RESOURCE_MAX_PAPERS=3
LITERATURE_RESOURCE_MAX_REQUESTS=12
LITERATURE_RESOURCE_TIMEOUT_SECONDS=5
```

OpenAlex Key如需使用，只存本机`OPENALEX_API_KEY`；其真实服务权限/额度尚需另行联网验证，不能从离线通过推断。
没有Key、元数据不匹配、超时或限流时，保留真实诊断，不造论文或仓库。
备用HTTP与资源查找不额外调用LLM；它们仍是实际外部请求，不属于免费离线验收。
资源解析与检索共用有界截止时间；启用备用检索后不再叠加外层重复检索重试。
Reader保持maxPapers=1；Repository保持明确URL、maxRepositories=1、executeCode=false、allowRepositorySearch=false。

## 五、免费验收和真实记录分开

`tests/test_platform_business_entry.py`覆盖统一入口、七/六字段、来源、模式、超时/取消、无重试、动态工厂、持久Reader复用，
以及假HTTP OpenAlex+资源→真实Reader Wrapper（假Provider）→真实Repository Enricher（假Provider）的自动绑定。
`tests/test_aip_dynamic_reader.py`另以官方SDK/内存ASGI验证动态Reader消息和完成确认。
这些不是正式梧桐验收，也不是本轮收费测试。

此前 `outputs/aip-research-20261020-135700-393904` 的LoRA新问题三Agent本机静态真实链已通过。
该历史记录不覆盖本轮刚迁入的OpenAlex/动态平台部署，不重写旧记录来宣称新增能力已真实验收。

2026-10-20该轮完整免费回归为684项（0 skip）+五个模拟场景+本机HTTP；真实CodeWiki本地临时仓库smoke另10/10通过。
该轮没有重新执行真实模型、论文HTTP、PDF下载、GitHub clone或正式平台请求；后续验收以当前进度页及对应记录为准。
完整记录 `outputs/acceptance-20261020-141840-108212/`；源文件与本机原始日志不应混同为远端已提交内容。
