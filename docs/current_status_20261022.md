# 当前进度与本地修复（2026-10-22）

本页对应赵的 `zhao/planner-integration`，修复起点为已推送的 `cd5678054c1df89daec6f1c8879cc18f528e4301`。
本轮补丁的发布状态以实际Git提交/远端为准，不将未提交修改写成已推送。

## 已有证据

| 范围 | 当前结论 | 证据位置或来源 |
| --- | --- | --- |
| 本机三Agent静态全链 | 已真实运行规划、检索、全文阅读、仓库分析和报告 | `outputs/aip-research-20261020-135700-393904/` |
| Repository增强 | 复用旧Reader结果的真实增强已通过 | `docs/repository_dual_mode_live_20261019.md` |
| 正式Literature单任务 | Leader自动验收通过，正确LoRA结果、Complete/completed及Leader清理通过 | `outputs/platform-literature/20261022-194336-3eeaa1c4/` |
| Partner持久化与生命周期 | 吴反馈唯一SQLite completed记录、GROUP_JOINED、DISBAND_RECEIVED | 吴侧第三次Group证据摘要，原始数据库留在吴侧 |
| 修复前回归 | 718项自动测试、0跳过，5个模拟场景及本地HTTP通过 | `outputs/acceptance-20261022-234407-215406/acceptance.json` |

第三次Result已在Leader侧回读核对，独立 `result.json` 与 `record.json.result` 完全一致。
这些文件不是Partner的缓存；Partner缓存回读、实际HTTP次数、GROUP_LEFT及部署版本仍需吴提供同一Task的证据。
此时只可声明Leader自动验收通过，不将所有双方待核对项标为完成。

## 本轮本地修复

1. 人工放行：原 `asyncio.to_thread(input)` 超时后会阻塞executor关闭，导致进程无法退出、最终报告延迟保存。改为单次守护输入线程和异步结果等待；超时、EOF及取消均进入既有清理与报告路径。发送权限仍由主协程和原Group客户端控制，线程只接收输入。
2. 报告：明确一次Start加一次Complete计为两条协议命令，不代表两次业务执行或Provider HTTP次数。
3. 提交检查：为凭据前缀增加左边界，避免把 `task-<编号>` 中的 `sk-` 误判为密钥；真实前缀、私钥标记和本机已知秘密的精确匹配仍保留。
4. 文档：更新README、正式Literature说明和业务交接说明，纠正“Agent目录为空”“统一入口尚未推送”“只有两阶段”等过期描述。保留原始验收记录与历史文档。

新增回归使用真实子进程、保持打开的stdin和平台替身，覆盖等待超时、取消、EOF、SEND成功以及最终报告落盘；没有实际平台或科研API请求。
七字段Task、六字段Result、SDK消息类型、Agent业务实现和统一 `execute_business_task(...)` 签名均不变。
继续使用原运行命令和依赖，无需队友调整业务接口。终端确认入口是单次CLI，一轮结束后启动新进程进行下一轮。

### 修复验证结果

- 完整回归：726项自动测试（0跳过）+5个模拟场景+本地HTTP检查通过；记录为 `outputs/acceptance-20261022-235646-449970/acceptance.json`。
- 针对性测试：Group/人工放行/扫描共42项通过；在实际 `.venv-mq-check` 环境另验证4个真实子进程输入场景，全部通过。
- 当前Git候选扫描：278个文件，0项规则告警；`outputs/sync-audit-local-fixes-20261022.json`。范围为当前候选文件，不代表已扫描全部Git历史。
- `git diff --check`通过。本轮不调用科研API，不重发任务，不覆盖第三次验收原始记录；修改保存在本地工作区，尚未提交或推送。

## 剩余工作

- 赵可独立推进：正式Reader/Repository Leader单任务入口、分能力等待期限、结果校验与离线测试；后续接入正式ADP发现与三阶段编排。
- 吴侧待核对：Literature同Task的HTTP次数、结果缓存回读、离群证据和准确部署版本；Reader/Repository各自身份、证书与Partner部署状态。
- 共同验收：Reader和Repository正式单任务，再做正式平台S1→S2→S3；默认不重跑已完成的Literature。
- 提交前：确认整合commit、更新演示/运行材料与用户交互入口，并按实际赛事提交要求复核。

原 `run_aip_research.py` 是本机AIP链；正式入口目前为 `run_platform_literature.py`，仅支持Literature。
`resolver/adp.py` 已有可注入组件，尚未连入正式三阶段入口。Literature现有180秒默认/600秒上限不可直接照搬给660秒业务预算的Reader。
