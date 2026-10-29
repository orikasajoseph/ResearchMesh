# ResearchMesh S2→S3 双侧联合候选审查摘要

- 基线：`32b24af5360b050b928e989ca78b5410a8aab2d3`
- 补丁：`D:\research\aic\evidence\ResearchMesh_S2_S3_joint_candidate_20261029.patch`
- 大小：`68141` bytes
- SHA-256：`447A79FEE6B4F7BB35C157206ABCA1B83EC69C5681A4EC5664861B5CE1FE9BE7`
- 修改文件：`aip/audited_group_client.py`、`group_invitation.py`、`group_literature_leader.py`、`tests/test_group_invitation.py`、`tests/test_literature_acceptance_audit.py`、`tests/test_platform_literature.py`
- 新增文件：`tests/test_partner_leave_receipt.py`
- 行为：包含 Partner 两阶段离组回执、Leader 回执校验与 fail-closed 清理，以及 Group 创建已尝试但结果不确定时保留 MQ 资源和 ACL 的保护。
- 测试：`python -m unittest tests.test_group_invitation tests.test_platform_literature tests.test_partner_leave_receipt`，97/97 通过；`git diff --check` 通过。
- 可移植性：在干净的指定基线临时 worktree 中 `git apply --check` 通过；临时 worktree 已清理。
- 安全检查：补丁仅含上述 7 个源码/测试文件；未检出常见私钥、API token 或 Bearer 凭证模式，未包含运行日志。
- 限制：测试均为离线/mock 证据，不代表真实平台、MQ、ACL 或端到端时限验收。

