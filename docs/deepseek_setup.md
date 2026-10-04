# DeepSeek Planner 接入与验收步骤

## 已提供的内容与边界

`deepseek_model.py` 实现现有 PlanningModel.generate，使用 AsyncOpenAI 调用 DeepSeek 官方 API。
JSON 模式、提示词、输出 Schema 与 Pydantic 校验共同控制输出；JSON 合法不等于研究计划合理，验收时仍应人工检查任务范围、输入和依赖。
本机已发现 openai 2.48.0、python-dotenv 1.2.1 和 httpx 0.28.1。组员可通过 requirements-llm.txt 安装已验证的版本。

适配器保留当前 Planner 提示词和业务外壳；未改动队友的 Wrapper、Discovery、AIP 或原模拟接口。
当前 requiredCapability 仍使用现有的下划线标签，与 Mock 注册表兼容。共享回复文档中的 literature.search / paper.analyze / repository.analyze 标签仍需全组确认后集中同步，本次不偷偷更换。

## 第一步 只在本机填写 Key

在 VS Code 打开项目根目录 `.env`，在 `DEEPSEEK_API_KEY=` 后填写真实 Key 并保存。
默认官方地址为 https://api.deepseek.com，模型为 deepseek-flash，超时 60 秒，规划输出上限 4096 tokens。
适配器只允许官方 HTTPS 地址，避免误配置导致 Key 发往其他服务器。
进程环境变量优先于 `.env`；如果曾设置旧的 DEEPSEEK_* 环境变量，请核对本机配置，程序不会打印这些变量。

`.gitignore` 忽略 `.env` 及其同名前缀文件，仅允许无 Key 的 `.env.example` 共享。
手动上传或压缩文件时仍须排除 `.env`。不要在聊天、日志截图或共享文档中展示 Key。

## 第二步 验证真实连通性

在 ResearchMesh 根目录的 PowerShell 终端执行：

```powershell
& "D:\Anaconda\python.exe" -X utf8 test_deepseek_connection.py
```

验收标准：命令退出码为 0，明确显示收到非空、正常结束的真实回复。
仅发一次短文本请求，不做隐藏重试，关闭思考模式，会消耗账户 API 额度。
未配置 Key 时退出码为 1，明确提示配置缺失，不联网。401 表示认证失败，402 表示额度不足，429 表示限流；程序不打印原始提供商报错或请求头。

## 第三步 验证一个新问题的真实规划

```powershell
& "D:\Anaconda\python.exe" -X utf8 run_deepseek_planner.py --query "调研 AlphaEvolve 的研究进展并分析复现难度" --max-attempts 1
```

第一次建议只允许一次调用以控制额度。省略 --max-attempts 时，JSON 或结构错误最多修正一次。
网络或认证失败不重试，正常结束但 JSON / 计划不合法才由原 Planner 发起格式修正；被截断的响应明确失败。

验收标准：

- 退出码为 0，userQuery 原样保留；任务只包含用户要求的工作。
- 任务类型、字段、编号与依赖通过现有校验；人工确认是否需要检索、阅读和仓库分析，而不是机械生成三项。
- 新生成的 `outputs/live-planner-时间戳/plan.json` 可打开。
- 同目录 `run_info.json` 标明 deepseek_live、调用次数、最后一次回复用量、planValidated=true、agentsExecuted=false、aipUsed=false。

run_info 的用量仅对应最后一次回复，不是多次重试的总费用。输出保存不包含 Key 或原始 HTTP 诊断。
计划中没有论文内容和代码分析结果，因为这里没有执行科研工具。不能把“真实规划成功”写成“真实科研报告或 AIP 互联成功”。

## 第四步 回归现有模拟程序

```powershell
& "D:\Anaconda\python.exe" -X utf8 -m unittest discover -s tests -v
& "D:\Anaconda\python.exe" -X utf8 run_demo.py
```

自动测试使用假 Key 和 HTTP MockTransport，不读取本机真实 Key、不联网到 DeepSeek。
原 run_demo.py 仍返回预写计划、运行三类 Mock，输出位置和警示不变。
原 `/plan` 仍只支持预写的三个问题；新增真实入口不会自动更改 HTTP 服务，也不会把真实模型用于每次 API 请求。

## 官方参考

- [首次调用 API](https://api-docs.deepseek.com/zh-cn/)
- [JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)
- [思考模式及关闭参数](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)

本文中的模型名按 2026 年 9 月 29 日查阅的官方文档配置；未来更名时应重新核对后更新配置及测试。
