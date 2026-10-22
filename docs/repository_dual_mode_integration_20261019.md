# Repository 双模式迁回赵分支

本轮按用户授权，将独立副本验收过的 Repository 双模式代码选择性迁回桌面原项目 `zhao/planner-integration`。基线为 `4a39ba29ab5ebbf16aac4a3443c929dc805518d0`，使用张 `22863c04a8afab8457df3c1a82e46da2f6217e0c` 的三个 Repository Wrapper 文件；没有整体合并张的预览分支，也没有改 main、张或吴的远端分支。

## 保留与提交范围

- 桌面原项目已有的 Group 邀请代码、文档、测试和 README 邀请说明保持原样，保留为未提交改动，不夹带进入本次 Repository 提交。
- 仅提交 Repository 双模式、结果证据校验、Reader 结果复用入口、安装说明及对应测试。
- README 仅暂存 Repository 说明的新段落，不暂存原有邀请说明。
- 不复制 `.env`、Key、证书、EAB、登录 token、PDF、运行缓存或虚拟环境；原始验收记录不上传。
- 迁移前的代码文本备份与指纹清单位于本机仓库外 `repository-migration-backup-20261019` 目录，不属于提交内容。

## 行为

默认静态模式行为不变，模型调用 0 次。只有 `--analysis-mode enhanced --execute` 才允许 Repository 增强计费。支持复用已经完成的真实 Reader 记录及显式 `--reader-cache`，不重新规划、检索、下载或阅读。

两种模式共用 Task v0.2、`repository.analyze` 和 `code_records.v1`，都不执行目标代码，不安装目标依赖，不宣称已经复现。增强失败时保留静态结果，并区分业务可用与增强验收通过。

## 验收与记录

迁回后已重新运行免费验收：**587/587 自动测试（0 skip）、五个模拟场景、本地 HTTP、真实 CodeWiki 临时本地仓库 smoke 的 10/10 checks、两种模式 CLI 离线预检均通过**。未重新调用真实 DeepSeek 或论文服务。

587 项包含桌面原目录已有、仍未提交的 32 项 Group 邀请离线测试；本次提交的已验收 Repository 基线在独立副本中为 555 项，不能要求其他成员仅拉取本次提交就得到未上传的 Group 测试。

本机记录（Git 忽略，不上传）：

- 全量验收：`outputs/acceptance-20261019-231313-708566/`。
- 真实 CodeWiki smoke：`outputs/repository-dual-smoke-20261019-231350-737e0ec7/`。
- 静态预检：`outputs/aip-repository-20261019-231351-14e15b2b/`。
- 增强预检：`outputs/aip-repository-20261019-231353-6962b3a8/`。

原有四个 Group 邀请文件已比较迁移前后 SHA-256，一致；README 仅插入 Repository 新说明，原有说明保留。

之前的真实增强记录仍在独立集成副本 `repository-dual-mode-20261019/outputs/aip-research-handoff-20261019-230234-fc7b6cd5/`，它证明“旧 LoRA Reader → 新 Repository 本地 AIP”通过；不代表全新三 Agent 或正式梧桐链路完成。具体调用数、token 和警告见 [真实增强验收](repository_dual_mode_live_20261019.md)。

## 本机运行环境

不复制副本的虚拟环境。桌面原 `.venv` 和 Reader 环境保持不变；仅在现有 `.venv-repository` 中安装 `agents/repository/requirements-enhanced.txt` 对应的共享 LLM 客户端依赖。CodeWiki 仍是固定提交的静态路径，不要求安装完整 CodeWiki 生成 CLI。

```powershell
# 免费预检，不会调用模型。
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --analysis-mode enhanced

# 原静态模式，无模型费用；execute 会 clone 并静态分析。
.\.venv\Scripts\python.exe -X utf8 run_aip_repository.py --repository https://github.com/pypa/sampleproject --execute
```

若确实要重新做收费增强，请先核对旧结果，避免重复付费；不把再次运行命令当作免费续跑。
