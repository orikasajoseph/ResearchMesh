# Leader invitation-only 联调入口

本入口只用于平台群组授权和 Inbox 入群验证，不接 Planner、LLM、论文检索或科研任务执行。
开发与离线测试不需要吴在线；真实邀请前必须由双方确认 Partner 当前监听并允许本轮邀请。

## 三种模式和权限边界

| 模式 | 行为 | 是否修改平台 |
| --- | --- | --- |
| 默认 `offline` | 校验双方 AIC 格式、Leader 证书、确认的 URL，生成执行预检记录 | 否，不建立 HTTP/MQ 客户端 |
| `acl-check --execute` | 对随机临时群组仅授权 Leader 自己，然后撤销该群组 ACL | 是；不连接 MQ，不创建实际消息群、不邀请 Partner |
| `invite --execute --partner-ready` | 临时授权、创建测试 Group、只向指定 Inbox 发一次邀请、等目标 Partner 的 connected 状态 | 是；不发 TaskCommand，结束后解散并清理 |

这些是三个独立运行，不自动从一个阶段进入下一个阶段。
没有 `--execute` 就不能进入真实模式；邀请还必须带 `--partner-ready`。
未取得明确测试授权或队友在线确认时，不应运行真实邀请命令。

## 来源和接入方式

- 正式 MQ：`amqps://wt.ioa.pub:5671`，vhost `acps`。
- 平台管理员经吴转达的 Group 服务：`https://wt.ioa.pub/mq-auth/`。
  本机已只读验证 `/health` 和 `/openapi.json` 为 200，服务标题为 `MQ Auth Group API`，
  包含 `PUT /groups/{leader_aic}/{group_id}/members/{member_aic}` 及相应 DELETE 路由。
  这只证明入口存在，不是 Leader 写入权限或真实入群验收。
- `group_invitation.py` 使用现有固定官方 SDK 的 `GroupAclClient`、`GroupLeaderMqClient`、
  `InboxGroupInvitation` 序列化和 GroupMgmtResult 接收流程，不另造协议、不修改 SDK。
- Leader 拥有自己的单次非 robust AMQPS 连接，传入 SDK 后打开群组通道，开启身份绑定。
- 当前 Partner AIC 是人工明确指定，不通过 ADP 自动选择；不能把这次联调称作自动发现闭环。
- `InvitationOnlyMqClient` 在发消息边界禁止所有 TaskCommand、TaskResult 和额外管理命令，
  仅允许结束测试时的官方 DISBAND 管理通知；邀请由官方 SDK 的专用 Inbox 方法发送。
- 为避免泄露证书身份，只接受已确认的官方 HTTPS Group URL，不接受任意主机或 localhost 替代值。

## 依赖和证书

使用现有 `.venv-mq-check` 隔离工具环境；首次安装方法见 [MQ 检查说明](platform_mq_check.md)。
沿用现有 `requirements-mq-check.txt`，不改科研 Leader/Reader 环境、不需要新 API Key。

证书放在 Git 仓库外同一目录：`leader-cert.pem`、`leader-key.pem`、`trust-bundle.pem`。
EAB、登录 token 和项目 `.env` 不属于本入口输入。不要复制凭据到仓库或发送到聊天。

在 ResearchMesh 根目录，先把以下三个示意值替换为真实值：

```powershell
$leaderAic = "网页上的正式Leader AIC"
$partnerAic = "队友确认正在监听的Partner AIC"
$leaderCertDir = "本机仓库外的Leader证书目录绝对路径"
```

### 第一步：免费离线预检

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 run_group_invitation.py --leader-aic $leaderAic --partner-aic $partnerAic --credentials-dir $leaderCertDir
```

应看到 `passed=true`、`offlineChecksPassed=true`、`networkAttempted=false`、
`aclWriteAttempted=false`、`groupCreated=false`、`invitationPublished=false`。
输出的随机 Group ID 只是预留的本轮编号，不代表平台已建群。

### 第二步：获得授权后，单独验证真实 ACL

此命令会写入并撤销本次随机 Group ID 对应的 Leader 自身 ACL；不触碰旧群组，不邀请队友。

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 run_group_invitation.py --leader-aic $leaderAic --partner-aic $partnerAic --credentials-dir $leaderCertDir --mode acl-check --execute
```

通过需要 `leaderAuthorized=true`、`aclDeleted=true`、`cleanupSucceeded=true` 和 `passed=true`。
如果失败，先看记录的 error/cleanupErrors，不要继续邀请，也不要换 SDK 默认 localhost 试凑。

### 第三步：与队友同时在线后，单独发送真实邀请

```powershell
.\.venv-mq-check\Scripts\python.exe -X utf8 run_group_invitation.py --leader-aic $leaderAic --partner-aic $partnerAic --credentials-dir $leaderCertDir --mode invite --execute --partner-ready
```

程序顺序：授权 Leader → 连接 MQ → 创建随机测试群 → 开始接收入群状态 → 授权指定 Partner →
单次 Inbox invitation → 等目标 Partner 的入群状态 → 默认观察 30 秒 → DISBAND 和清理。
这里会使用/声明平台共享的 `inbox.topic`，但绝不删除共享 Exchange 或任何人的持久 Inbox。

关键输出：

- `INVITATION_PUBLISHED`：只代表 SDK 发布调用返回成功，不代表已入群。
- `GROUP_JOINED_CONFIRMED`：SDK 校验 AMQP user_id 与 senderId/groupId 后，收到指定 Partner 的
  `GroupMgmtResult.status.connected=true`。请与队友的 `GROUP_JOINED` 及相同 Group ID 对照。
- `cleanupSucceeded=true`：本轮清理操作未报错；清理失败时 `passed` 必须为 false，
  即使之前已收到入群确认也不能隐瞒残留风险。

测试群不是长期在线群组。观察期结束后程序会自动发送 DISBAND 并删除本轮群组资源和 ACL。
下一轮测试前，队友应确认已回到待邀请状态；如还持有旧群组状态，应先在其本机正常重启 Partner。

无论哪种模式，`taskCommandsSent=0`、`llmCalls=0`、`protocolCompleted=false`、`businessReady=false`。
入群确认不代表完成科研任务，不代表付费 API 已运行，也不证明 ADP/完整三 Agent 链已验收。

## 超时、拒绝和清理

- 连接、ACL、发布每步默认最多等待 20 秒（`--timeout`，>0 且 <=60）。
- 默认等入群确认 45 秒（`--join-timeout`，1 到 120）；不自动重发邀请。
- 观察期默认 30 秒（`--hold-seconds`，0 到 60）。
- 每项清理默认最多等 5 秒，并独立尝试；资源已经不存在视为已清理，权限/网络错误必须保留。
- 只删除程序自己生成的随机 Group ID 下的群组队列、Exchange 和 ACL；不能指定其他已有 Group ID。
- 对拒绝邀请，Partner 可能把错误发给 Leader Inbox；这个最小入口只消费群组状态，
  因此拒绝、离线或身份校验失败都可能表现为 `PARTNER_JOIN_TIMEOUT`，不能仅据此断言原因。
- 先停止 SDK 群组消费，再严格检查本轮资源删除，最后撤销 ACL、关闭 MQ/HTTP 客户端。
- 单次 Ctrl+C 会尝试清理；强制杀进程、机器断电、再次 Ctrl+C 或网络故障不能保证回滚。
  若 `cleanupSucceeded` 不是 true，先检查记录中的 Group ID 和失败步骤，不要不断重跑新邀请。

运行日志位于被 Git 忽略的 `outputs/group-invitation/<本轮目录>/record.json`，
外部操作前会保存本轮 Group ID。文件仅保存阶段、结果、消息编号与错误类型，
不保存完整 invitation、invitationToken、私钥、HTTP token 或原始异常内容。

## 离线测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_group_invitation -v
.\.venv\Scripts\python.exe -X utf8 verify_project.py
```

测试通过替身验证 ACL→建群→邀请顺序、目标匹配、失败/中断清理和无任务请求，
并用真实 SDK 的消息序列化、身份检查以及内存 HTTP 传输检查 `/mq-auth/` 路径。
离线测试不会授权平台、创建真实 Group 或邀请真实 Literature Agent。

## 本轮开发验收（2026-10-18）

- 新增 32 项离线测试通过；完整回归 562 项自动测试、0 跳过，另有 5 个模拟场景和本地 HTTP 检查通过。
- 完整回归本机目录：`outputs/acceptance-20261018-214437-380741`。
- 使用正式 Leader 证书和队友给定的 Literature AIC 做了默认离线预检，`passed=true`，
  `networkAttempted=false`、`aclWriteAttempted=false`、`groupCreated=false`、`invitationPublished=false`。
- 预检本机目录：`outputs/group-invitation/20261018-214451-e17df10a`。
- 以上为离线开发验收时的状态；后续首次 ACL 实测结果见下节。原始运行记录和证书均不上传仓库。

## 首次真实 ACL 检查：被 HTTPS 入口证书认证拦截（2026-10-18）

用户授权后仅运行了 `acl-check --execute`，没有运行邀请模式。
SDK 首次请求失败后，针对同一个随机临时 Group ID 做了一次幂等诊断并再次请求清理，没有生成新群组。

- `PUT /mq-auth/groups/<Leader AIC>/<本轮 Group ID>/members/<Leader AIC>`：HTTP 403。
- 同一临时 Group 的 DELETE：HTTP 403，故不宣称清理已成功。
- 两次诊断响应均为 `CLIENT_CERTIFICATE_REQUIRED`，详情为
  `a valid client certificate common name is required`。
- 本机证书身份、有效期、clientAuth 与私钥配对检查通过；未配置 HTTP/HTTPS 代理。
- 对 `wt.ioa.pub:443` 的一次直接 TLSv1.3 握手和 `/mq-auth/health` GET 只读检查：
  服务端证书校验通过、health 为 200，但握手未请求客户端证书，客户端因此未发送 Certificate 消息。
- 这些证据指向“当前 HTTPS 入口没有向 Group 服务提供所需的客户端身份”的接入问题；
  还需管理员确认正确 mTLS 入口、TLS 终止/透传方案与部署版本，不能仅据此断定整个平台注册或证书签发有故障。
- 没有建立 AMQP 消息群、没有邀请、没有 TaskCommand 或模型调用。ACL 并未获得成功响应，
  原始清理状态保留为未确认，不把认证拒绝包装成授权或回滚成功。

本机记录：`outputs/group-invitation/20261018-214608-82f8a73c/record.json` 与同目录
`acl_diagnostic.json`。此时应停止邀请，向平台提供错误码和入口握手现象；
不要关闭 TLS 校验、伪造客户端 CN 请求头、反复重建群组或要求队友重新申请 API Key。

## 2026-10-20 复检结果

使用相同正式 Leader AIC、Literature AIC、证书目录和确认的 Group 服务入口重新执行：

1. 默认离线预检通过，未联网、未写 ACL、未创建 Group；
2. 随后只执行一次 `acl-check --execute`；
3. Leader ACL 写入仍未获得授权，记录为 `GROUP_ACL_REQUEST_FAILED`；
4. `leaderAuthorized=false`、`cleanupSucceeded=false`、`aclDeleted=false`；
5. 未连接 AMQP、未创建消息群、未发送 invitation 或 TaskCommand。

本次脱敏记录：`outputs/group-invitation/20261020-143158-23a53a07/record.json`，
临时 Group ID 为 `rm-acl-check-b4b3346086604061b34e45a3dff824ff`。记录未保存原始 HTTP
状态和响应正文，因此不能只凭该文件断言仍为此前完全相同的 403；平台仍需确认正式 Group ACL
入口、mTLS 身份转发和 Leader AIC 权限。在得到明确修复答复且新的 ACL 检查通过前，不执行
`invite --execute`。
