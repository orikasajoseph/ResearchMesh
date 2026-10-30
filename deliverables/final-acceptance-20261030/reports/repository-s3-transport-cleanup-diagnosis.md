# ResearchMesh Repository S3 传输与清理故障诊断

日期：2026-10-29（Asia/Shanghai）

## 事故状态（保持不变）

- Task：`REMOTE_STATE_UNKNOWN`
- Group：`CLEANUP_INCOMPLETE`
- Group：`rm-repository-chain-9211b8c8548f47c6afdd3191a8e3edfd`
- Session：`session-72bff710c946411a91d8cd546eba82e5`
- Task：`task-32ea552ec03d4bc1857c73d8ff36e037`

本轮未发送 Start、Complete 或 invitation，未操作 Partner、SQLite、ACL、MQ、证书或远端资源。

## 本机只读检查

- 当前 HEAD：`618489f0fc1ac00c8273d916096d970d92b04dbd`；检查前无已跟踪修改，仅保留两个既有未跟踪文件。
- 2026-10-29 09:49 +08:00，`wt.ioa.pub` 单次 DNS 解析成功为 `49.232.42.120`；针对该地址的单次 TCP 5671 检查成功。该结果只证明检查时可用，不能反推事故窗口。
- Windows 时间服务最近成功同步时间为 2026-10-29 07:57:31，当前时间与时区正常。
- 当前 shell 未发现 HTTP/HTTPS/ALL/NO proxy 环境变量；未修改进程或系统代理。
- Repository 证书 CN 为 Repository AIC，客户端认证用途存在，有效期为 2026-10-20 至 2026-12-08。未读取或输出私钥内容。
- 配置一致：host `wt.ioa.pub`、port `5671`、vhost `acps`、Repository AIC 与 Group queue 日志一致。

## 根因分级

### 已确认

1. Start publish 没有取得成功返回；计数保持 `taskCommandsSent=0`，但不能据此证明 broker 未接收。
2. 事故窗口出现 ConnectionAborted、AMQPConnectionError、ChannelInvalidStateError 和一次 DNS 失败。
3. Repository 没有 TASK_COMMAND、reservation、Worker、输出或 SQLite Task 记录。
4. Repository 未记录 DISBAND_RECEIVED 或 GROUP_LEFT；Group queue 恢复持续 ACCESS_REFUSED。
5. 原实现清理时复用同一 Group client 发布 Disband；即使 Disband 或 MQ 资源删除失败，仍会继续删除 ACL。这是可由代码独立确认的部分清理风险。

### 候选原因

- Start publish 与连接断开发生在 publisher confirmation 不确定窗口。
- Disband 通过失效的 client/channel 发送失败。
- MQ 资源删除部分失败后 ACL 删除成功，使 Partner 持有旧 Group 状态但无法恢复 Group queue，形成持续 ACCESS_REFUSED。

### 服务器侧依赖

- broker 是否接收、持久化或路由 Start；具体 publisher-confirm 状态。
- Group ACL 实际状态，以及两个 queue、exchange、bindings 和 consumer 是否残留。
- ACCESS_REFUSED 的精确 AMQP method。当前日志只证明 robust recovery 的 queue configure 被拒绝。

项目没有现成的远端只读 ACL/MQ 管理查询封装；本轮没有试探调用管理接口或 passive declaration。

## 本地最小修复

本地 commit：`4e36f5a`（`Harden Leader transport and partial cleanup handling`）

提交文件：

- `group_literature_leader.py`
  - Start、Complete、Disband 发布前检查当前 connection/channel/exchange；可观察到失效或 exchange 不属于当前 channel 时 fail closed。
  - 保存不含凭据的 publish path 快照。
  - Start/Complete 发布失败保留独立错误类别；Start 仍最多尝试一次，未知状态不重发。
  - Disband 发布结果单独记录；失败时不伪造成功。
  - Partner queue、Leader queue、exchange 分别记录删除结果。
  - Disband 或 MQ 资源清理不完整时延后 ACL 删除，并记录 `delete_own_acl_deferred/DependencyFailed`。
- `platform_mq_check.py`
  - 区分 DNS、AMQP connection、channel invalid/closed 和 ACCESS_REFUSED，不保存异常原文。
- `tests/test_platform_literature.py`
- `tests/test_platform_mq_check.py`
  - 覆盖失效 channel、DNS、ChannelInvalidState、ACCESS_REFUSED、Disband 失败、资源部分清理和禁止 ACL 删除。

SDK 未暴露稳定的 channel generation 标识；本修复只能验证当前对象和 transport/channel/exchange 一致性，不能证明远端 broker generation。该限制需要后续 SDK 或平台 trace 支持。

## 离线验证

- 传输、Leader、cleanup、lifecycle 关联回归：124/124 PASS。
- Repository Live Chain、Reader handoff、AIP Repository 等：98/98 PASS。
- Repository Fetcher/Agent：36/36 PASS。
- `py_compile`：PASS。
- `git diff --check`：PASS。
- launcher `.venv` 首次扩大回归因缺少 `litellm` 出现导入错误；改用项目既有 `.venv-reader` 并设置测试 PYTHONPATH 后真实通过，未将该环境错误计为成功。

全部测试使用 Mock 或本地 fixture；未访问平台、MQ 管理面、GitHub 或模型服务。

## 当前结论

本地可确认的发布健康检查、错误分类和部分清理误判路径已完成最小修复并通过离线回归。事故 Task 仍为 `REMOTE_STATE_UNKNOWN`，旧 Group 仍为 `CLEANUP_INCOMPLETE`。代码修复不会改变当前运行 Partner，也不会修复旧 Group 的远端残留；后续仍需平台管理员提供 broker/ACL 只读状态并授权定向清理。

提交前最终复核：完整 diff 仅包含上述四个文件；未发现凭据或无关改动；两个既有未跟踪文件未暂存、未修改、未删除。提交后工作区仅剩这两个既有未跟踪文件。本轮未 push、merge 或创建 PR。
