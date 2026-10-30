# ResearchMesh LoRA 隔离静态验证执行报告（2026-10-29）

## 最终状态

**FAIL — checkout timeout**

本次不是正式平台S3；没有操作Partner、Group、邀请或Task。唯一授权执行已消耗，失败后没有重试。

## 身份与输入

- ResearchMesh实际commit：`039ebf1483f27e9f0c4ea0fcb7f05ea3e5ec6650`
- 执行标识：`repository-group-a1c7b5ddf6ee437c8347819dd06ca618`
- 现场：`<REDACTED_LOCAL_PATH>
- Reader输入：`<REDACTED_LOCAL_PATH> 的 `outcome.result`
- Reader canonical SHA-256：`ec69793fdd8457ac1312c988bc9352e8c4aaea94fb42168969770e50102a1427`
- 目标：`https://github.com/microsoft/LoRA`
- 输入约束：1篇真实paper note、`requirePaperNotes=true`、`executeCode=false`、`allowRepositorySearch=false`、`outputSchema=code_records.v1`
- `repository_input.json` SHA-256：`640F4F7BECBD35DE2DDF34D99CCECBAC2AFD4F0C0DBFB96EBC0A0646B8EB7404`
- `backend_outcome.json` SHA-256：`18535C8B67BC4360F7216BB7DBC0DDCAF872383645735F9D09286090C9E58BC8`

## Git阶段

### clone

- 命令策略：protocol v2、depth 1、single branch、no tags、`filter=blob:none`、no checkout、progress；child-only直连；LFS smudge关闭。
- `returncode=0`
- `timedOut=false`
- `durationMs=4194.576`
- stderr末段：接收182/182对象，48.82 KiB，211.00 KiB/s，done。
- filter warning：**未观察到**。
- filter退化：**未观察到明确证据**；没有warning本身不能证明远端filter已生效。
- `resolvedCommit=null`：commit只允许在后续HEAD验证成功后记录。

### checkout

- 独立checkout，禁用目标仓库hooks，progress开启。
- `returncode=1`
- `timedOut=true`
- `durationMs=60435.971`
- stdout/stderr为空。
- `resolvedCommit=null`

checkout超时后立即停止。没有执行HEAD、工作树验证或CodeWiki。不得从clone成功推断checkout或工作树成功。

### cleanup

- `returncode=null`（文件系统阶段，无Git退出码）
- `timedOut=false`
- `durationMs=58.531`
- stderr为空
- cleanup成功，未覆盖checkout主错误。

## 业务与Schema

- backend scope：`ISOLATED_CODEWIKI_STATIC_NO_LLM`
- backend phase：`failed`
- 对外错误：`REPOSITORY_CLONE_FAILED`（兼容既有错误Schema；内部失败阶段为checkout）
- `result=null`
- `resolvedCommit`：无
- 真实`repository_record`：未生成
- `code_records.v1`结果：未生成，因而无结果可执行Schema校验
- 输入Schema和Reader绑定：执行前通过
- CodeWiki：未运行
- 模型调用：0
- Python网络阻止事件：0
- 目标代码执行：false
- 目标依赖安装：false
- 正式平台访问：无

## 进程与现场

- 唯一worker自然以失败终态退出。
- 执行后进程快照未发现Git或本轮`repository_aip_worker.py`残留。
- owned checkout进程树按新版timeout路径处理；直接Git进程退出码为1。
- 没有单独持久化`taskkill`命令自身的returncode，故不宣称该字段已审计。
- 历史目录未删除或覆盖；本轮现场和两个JSON证据保留。
- 工作树tracked内容未改变；两个既有未跟踪文件保持原样。

## 结论与建议

本次证明partial clone对象获取已在4.19秒内成功，但checkout阶段（其中可能包含promisor blob的lazy获取）在60秒内未完成。由于checkout没有stderr、没有HEAD和工作树证据，不能进一步断言是网络传输、Git checkout处理或其他具体子阶段。

状态仍为 **NOT_READY_FOR_S2_TO_S3_FORMAL_TEST**。后续应先离线增强checkout阶段诊断/控制，必要时将lazy blob获取与工作树物化进一步拆分；新的真实Git操作必须再次获得明确授权。不得进入Partner切换或正式S3。
