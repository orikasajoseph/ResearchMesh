# Leader 梧桐 MQ 连接检查（2026-10-16）

本页是 10 月 16 日新增的平台接入进度；较早文档中的“证书未申请”是历史状态。
本工具独立于 Planner/Resolver/科研业务链，不修改任务或结果 Schema，不替代吴的群组接入代码。

## 已完成与未完成

- Leader 已在梧桐审核通过、取得 AIC，并在本机申请了 clientAuth 身份证书。
- 独立连接检查已在真实平台通过：mTLS/AMQP 认证成功，官方 SDK 打开通信通道，随后断开。
- 未创建群组、队列或 Exchange，未发邀请、未发送 AIP TaskCommand，未调用 Partner。
- 没有模型请求、Semantic Scholar 请求或论文下载；不涉及 DeepSeek 费用。
- 尚未验证群组/ACL 权限、Partner 入群、ADP 发现、心跳在线或平台完整科研任务。
- 不会因为连接成功就修改 ACS active 或宣称智能体持续在线。

## 交付文件

- `run_mq_check.py`：命令行入口，AIC 和本机证书目录必须明确传入。
- `platform_mq_check.py`：校验身份、证书有效期/clientAuth、CA 与私钥匹配，再按需尝试连接。
- `requirements-mq-check.txt`：可选工具依赖。沿用项目 SDK 源码提交
  `3985c1330209f075c124669cc9d31f0e75448140`，固定实测的传输/证书依赖，不升级科研 Leader/Reader 环境。
- `tests/test_platform_mq_check.py`：离线替身测试，不访问平台、不读取真实凭据。

工具没有写死任何组员的 AIC、用户名、密钥或 Windows 用户目录。
当前只连接已核对的官方主机 `wt.ioa.pub:5671` / vhost `acps`，不接受任意目标主机，
避免把客户端证书误用于不可信服务器。使用 AMQPS + SASL EXTERNAL，不使用 guest 密码，
不关闭服务端证书校验，不做自动重连。

## 准备独立工具环境

在 ResearchMesh 仓库根目录执行。前提是已有项目 Python 3.12 `.venv`。
以下操作仅下载工具依赖，不调用科研 API；`.venv-mq-check` 不提交到 Git。

```powershell
.\.venv\Scripts\python.exe -m venv .venv-mq-check
.\.venv-mq-check\Scripts\python.exe -m pip install -r requirements-mq-check.txt
```

若该工具环境已经建立，不必重新创建。也可使用已安装相同 SDK 与 cryptography 的独立工具环境；
不要为了此检查替换现有 Leader/Reader 的解释器或重装其依赖。

## 本机证书配置

每位组员使用自己的平台身份和证书；以下三个文件放在 Git 仓库外同一个目录：

```text
外部证书目录/
  leader-cert.pem
  leader-key.pem
  trust-bundle.pem
```

如果已有证书使用不同文件名，可在本机的外部证书目录整理上述命名；不要复制到 ResearchMesh 内。
`--credentials-dir` 必须指向仓库外，符号链接目标也不能指向仓库内的凭据。
AIC 必须与证书 Subject CN 一致，使用 Leader 的实际 AIC，不是昵称、邮箱、Partner AIC 或 `{AIC}`。

EAB、登录 token、ACME account key 只用于申请/管理证书，不是本工具的输入；本工具不会读取它们，
不会加载项目 `.env`，也不读取 DeepSeek/Semantic Scholar Key。

## 先离线检查，再显式连接

先把以下两行的值改成自己的真实 AIC 和仓库外证书目录。双引号内是示意内容，不能原样执行。

```powershell
$leaderAic = "替换为网页中的完整Leader AIC"
$leaderCertDir = "替换为本机仓库外的证书目录绝对路径"
```

免费离线检查（不加 `--execute`）：

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 run_mq_check.py --aic $leaderAic --credentials-dir $leaderCertDir
```

预期：`passed=true`、`offlineChecksPassed=true`、`networkAttempted=false`。
离线通过只代表证书配置检查通过，不代表连接过平台。

进行一次真实连接：

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 run_mq_check.py --aic $leaderAic --credentials-dir $leaderCertDir --execute
```

成功时：`passed=true`、`amqpConnected=true`、`leaderChannelOpened=true`、`connectionClosed=true`。
`protocolCompleted=false` 和 `businessReady=false` 是刻意保留的边界：本工具没有执行 AIP 任务或科研业务。
连接/开通道默认合计最多等 20 秒（`--timeout` 可设为大于 0、最多 60 秒），结束时最多等 5 秒清理连接。
清理失败也会报告失败，不会声称连接已安全关闭。

JSON 记录默认写入被忽略的 `outputs/platform-mq/`。记录只含身份编号、检查状态、错误类型、耗时等，
不包含私钥、EAB、token、凭据文件内容或原始异常消息。程序成功退出码为 0，检查失败为 1。

## 离线测试与失败排查

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_platform_mq_check -v
.\.venv\Scripts\python.exe -X utf8 verify_project.py
```

离线测试替换证书元数据和底层连接，但仍使用真实 SDK 的“已有连接打开通道”路径；
它们不证明真实证书、网络、MQ 权限或科研结论有效。

本轮验证记录（仅本机保存，不随 Git 上传）：

- 新增 28 项 MQ 离线测试，在既有 Leader 环境和独立 MQ 工具环境均通过。
- 完整回归：530 项自动测试，0 跳过，另有 5 个模拟场景和本地 HTTP 检查通过。
  本机目录：`outputs/acceptance-20261016-235633-776983`。
- 项目版默认离线证书预检通过，`networkAttempted=false`。
- 项目版真实连接通过，连接与通道均成功并关闭；单次耗时 375 ms，不能当作统计性能指标。
  本机记录：`outputs/platform-mq/mq-check-20261016-235716-f2ab48b8.json`。
  `groupsCreated/tasksSent/llmCalls=0`，`protocolCompleted/businessReady=false`。
- 独立工具环境：Python 3.12、上述固定 SDK、aio-pika 10.1.1、aiormq 7.2.2、cryptography 46.0.7。

| 错误代码 | 检查方式 |
| --- | --- |
| INVALID_LEADER_AIC | 核对网页 AIC，避免 Read-Host 将 Ctrl+V 录成控制字符 |
| CREDENTIALS_MUST_BE_OUTSIDE_REPOSITORY | 将证书保存在本机仓库外，不要靠 Git ignore 代替隔离 |
| CERTIFICATE_FILE_MISSING | 检查目录和三个文件名，不要发送文件内容 |
| CERTIFICATE_AIC_MISMATCH | 使用签发给该 Leader 的证书与 AIC |
| CERTIFICATE_NOT_CURRENTLY_VALID | 检查证书有效期及系统时钟 |
| CERTIFICATE_CLIENT_AUTH_REQUIRED | MQ 连接需要 clientAuth，不是仅有 serverAuth |
| OPTIONAL_DEPENDENCY_MISSING | 在工具环境安装 requirements-mq-check.txt |
| DNS_LOOKUP_FAILED / CONNECTION_TIMEOUT | 检查网络、代理、DNS、5671 端口；不会自动重试 |
| SERVER_CERTIFICATE_VERIFICATION_FAILED | 核对官方 trust bundle；不要设置跳过 TLS 校验 |
| AMQP_AUTHENTICATION_FAILED | 检查证书签发身份、平台认证与 vhost 权限 |
| TLS_OR_LOCAL_KEY_ERROR | 核对私钥与证书配对，勿把私钥发到聊天或 GitHub |

## Git 安全与队友对齐

本次只上传脚本、测试、依赖声明、说明和忽略规则，不上传任何本机凭据或原始运行记录。
`.gitignore` 增加了 EAB、`.acps-cli/`、`keyfiles/` 等保护；已跟踪的文件不会因忽略规则自动消失，
因此推送前仍须检查暂存列表和内容。不要在组员的仓库历史或云端已经泄密时仅靠增加忽略规则“修复”。

等吴回复后，需要对齐：

1. Literature Partner 的完整 AIC、是否监听 Inbox、Leader AIC 白名单是否配置。
2. 官方 Group/ACL 接入地址、鉴权与 SDK 版本/源码提交是否一致。
3. 首轮仅邀请入群，再执行一条受控真实检索，分别保存证据；不要把连接通过当成任务验收。

参考：梧桐官方接入说明 <https://wt.ioa.pub/docs.html#sdk-certificate>；
官方 SDK 群组模式文档 <https://github.com/AIP-PUB/ACPs-community/tree/main/acps-sdk/acps_sdk/aip>。
