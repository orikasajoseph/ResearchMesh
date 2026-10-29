# Repository Partner 候选补丁

- 补丁基线：`4e36f5afe662c19947def23dde2eff6384418151`
- SHA-256：`AD010A58238AFBD0BDB9C5096DBD836A9B99AE45917CD391563DB4E11DD2CC36`
- 目标：对齐 Repository Partner Heartbeat 输出与 Fluent Bit 读取路径。
- 适用性：不能直接应用到赵的 `58ebbbfe6331fc8136a167f074d3dd24b33661e2`，因为该候选不含目标配置文件；需在双方联合候选中选择性整合。
- 限制：仅为审查材料；禁止据此合并 `main` 或提交业务代码。
