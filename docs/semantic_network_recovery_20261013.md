# Semantic Scholar 网络故障诊断与有界恢复

日期：2026-10-13。只修改赵侧 HTTP 观测、检索恢复和终端提示，保留本日先前的目录修复。未修改张的 Wrapper、AIP/业务 schema、Key、系统代理、DNS或证书验证；未自动提交或推送。

## 原失败证据

outputs/aip-research-20261013-140613-788125/literature/backend_outcome.json 记录：PROVIDER_UNAVAILABLE；无 HTTP 状态；NETWORK_OR_TIMEOUT；耗时约5.05秒；Key已配置并随请求发送；调用一次、重试零次。

这不是429限流记录，不是此前的目录错误。旧代码抹去了异常类型，不能回溯断言当时是DNS、代理、TLS还是哪一阶段超时，也不能据此宣称官方API整体宕机。

## 本次只读诊断

scripts/diagnose_semantic_network.py 使用无 Key HEAD，不查询论文或调用模型。记录在 outputs/semantic-network-20261013-141258-912098/diagnosis.json。

- 检查时环境变量代理和有效代理为空，未发现证书环境覆盖。
- 环境模式、直连模式均在约0.4—0.5秒收到HTTP 400。无搜索参数的HEAD返回400不是论文检索失败；这里只证明当时能收到服务器HTTP响应。
- DNS解析成功，地址为198.18.1.37。记录此结果但未据此判定网络软件故障，未擅自改动系统配置。
- 该诊断来自本机测试进程，不自动证明VS Code终端继承完全一致的环境。若仍失败，用户可在自己的终端运行同一脚本对照；不发送Key。

参考HTTPX官方说明：https://www.python-httpx.org/environment_variables/ 和 https://www.python-httpx.org/advanced/timeouts/ 。实际请求保留TLS验证，未通过关闭验证来“修好”网络。

## 确定的代码缺口及修复

1. 旧观测把所有传输异常合并为 NETWORK_OR_TIMEOUT。现在记录固定白名单的异常/因果类型、DNS/连接/读写/连接池超时、TLS、代理及连接中断原因，并记录实际超时配置；不保存原始异常文本、代理密码或Key。
2. 旧恢复只处理429。现在对最新观测中无HTTP回应、明确可重试的网络错误最多追加2次检索重试，等待约1秒/2秒加少量抖动；429沿用最多3次有界退避。混合故障共享总重试预算，SDK和Provider不增加内层重试。
3. TLS证书、代理配置、未知错误、无观测、已有HTTP 401/403/500，以及同一旧请求观测不作为新的临时网络故障盲目重试。显式禁用重试仍只调用一次。
4. 只重试检索，不重新生成计划，不重复下载或重试收费Reader。父入口实时转发经过严格格式筛选的重试提示，并展示脱敏provider/transport错误码；不会把完整子进程日志或Key打印到终端。

有界重试提高临时故障的恢复能力，不保证外部网络、VPN或供应商永远可用。

## 新真实链路验收

成功目录：outputs/aip-research-20261013-141704-961452。

复用本次用户失败流程的原 plan.json，SHA256保持一致；原目录不改写，不再次调用 Planner。

- Literature：真实Semantic Scholar响应依次429、429、200；自动等待合计6.226秒后返回3篇论文；官方本机AIP completed、businessReady=true。本次没有再次出现传输故障，所以新网络重试分支由离线注入故障测试验证，而非声称已在真实故障中验证。
- PDF：已完成来源、标题和缓存核验。
- Reader：真实单篇全文阅读，未复用已有阅读结论；AIP completed、businessReady=true；没有Leader自动阅读重试。准确模型请求次数和费用未知。
- pipeline.json：literature/pdf/reader全部completed。report.md、research_run.json已生成；科学证据仍待人工核对。
- 本轮只证明本机两类Agent的实际执行，不证明ADP、梧桐身份、TLS绑定、跨电脑或Repository验收。

最终回归：451项自动测试、0跳过、5个模拟场景及本地HTTP检查通过；outputs/acceptance-20261013-142106-799714。新增网络故障测试不等于新增真实模型调用。

## 用户操作

直接展示成功目录的report.md无需任何新API请求。重新真实演示可沿用原命令；也可添加 --plan outputs/aip-research-20261013-140613-788125/plan.json 跳过Planner，但检索和阅读仍会实时执行且可能计费。

再次失败时不要连续重跑收费Reader。先提供pipeline_failure.json及对应阶段日志；网络定位可用：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/diagnose_semantic_network.py
```

不要发Key、证书、.env或未经审核的完整outputs。所有失败历史保留，不把failed改为completed冒充成功。
