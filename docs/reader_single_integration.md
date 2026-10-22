# Reader 单篇业务对接

2026-10-11：张确认 d84924d 的 evidence.supports 使用非空字符串，承载实际结论；同一 Context 的结论合并，无关联时依次使用 summary、raw evidence。该项确认不代表全组所有协议已冻结。

本步骤增加 Leader 侧 reader_adapter.py 和单篇离线验收入口。张的 Reader 仍从显式路径加载，未复制或修改到主项目，不要求安装 PaperQA。现有 run_research.py 仍只运行独立检索，没有把 Reader 接入完整工作流。

适配器接受已绑定一篇论文的 v0.2 Planner 任务或七字段业务任务，发送前去掉 requiredSkill、dependsOn，并将 PaperRef 规范为六字段。只支持 maxPapers=1；多篇、未知约束明确拒绝，不静默截取。不生成 PDF 地址，不更改 supports 内容。

返回 result、error、diagnostics 为 Leader 内部调用结果，不是 AIP 消息或协议状态。仅结构合法不足以判定业务完成，还需有一条有效 paper_note、论文来源和证据均对应请求，且不是 Mock 结果。

- 有效单篇：error=null、diagnostics.businessCompleted=true，并保留 Reader 的警告与证据。
- 缺全文：保留空结果和 FULL_TEXT_UNAVAILABLE，businessCompleted=false、error.code=FULL_TEXT_UNAVAILABLE。
- 其他空结果：businessCompleted=false、error.code=READER_EMPTY_RESULT。
- Provider 异常（包括无证据 StructuredAnalysisError）：result=null、error.code=READER_CALL_FAILED，不回显异常正文。
- 超时或不合法结果：分别为 READER_TIMEOUT、INVALID_READER_RESULT，业务完成为 false。

这些内部标志尚未接入 LocalOrchestrator 或真实 AIP Adapter，不能将本入口通过写成实际工作流或平台验收通过。

在项目根目录运行新增测试：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_reader_adapter -v
```

用张的实际 Wrapper 与 FakeProvider 验收（必须指定含 researchmesh_paper_reader 包的目录）：

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_reader_single.py --wrapper-root "C:\Users\orika\Documents\Codex\2026-09-22\new-chat\reader-review-20261011\agents\paper_reader"
```

路径只是本机当前独立副本，其他电脑需替换。每次新建 outputs/reader-single-offline-时间戳/acceptance.json，保存范围、输入结果、检查项及 Wrapper 源码哈希，不覆盖历史记录。

该入口只使用合成数据验证 Leader 与真实 Wrapper 的兼容，测试成功中的 businessCompleted 表示测试用业务结果完整；它不证明论文内容真实、全文模型可用或平台互联。入口不读取 Key、不请求模型、不下载 PDF、不执行 ADP/AIP，也没有重新运行张的昂贵 live test。

下一步另行实现多篇拆分与汇总，并明确区分全部成功、部分成功和全部失败。真实全文与平台验证继续单独验收。
