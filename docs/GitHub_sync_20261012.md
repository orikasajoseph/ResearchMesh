# 赵开发分支交付说明（2026-10-12）

交付目标：`zhao/planner-integration`。不合并main、不修改张或吴的分支、不强制推送。实际远端同步请查看该分支提交；本文不把准备提交当已经推送成功。历史说明里的“未提交/未推送”指此前开发阶段。

## 本次代码范围

- Reader单篇/批次业务适配、断点保存、实际检索→阅读直接链路。
- 官方SDK本机AIP Partner与检索/单篇Reader调用，异步查询、完成确认、隔离Reader进程及缓存取回边界。
- 新单入口 `run_aip_research.py`，默认不联网不收费；显式`--execute`允许新规划/检索/下载/阅读。
- 可注入正式ADP的Resolver、通用AIP执行调度、可选公共LLM Provider桥接。
- 原始成功记录复核工具、脱敏数字摘要、上传前只读检查及相关测试/说明。

不上传`.env`、Key、证书、虚拟环境、outputs完整记录、缓存或`examples/reader_test.pdf`。本机论文文件仍保留；仅增加ignore防止误传。需要独立单篇PDF测试时自行合法取得指定论文，用`run_reader.py --pdf "本机PDF路径"`显式提供。

本次不会把张的外部Reader副本复制进主项目；实际成功验收的Wrapper提交为 `d84924d14ecb02a8ae19779dad11f2488df6a9f5`。运行需要显式Reader源码和第二套环境，见独立工作运行指南；不是只clone赵分支便自动安装PaperQA或embedding缓存。

## 已验证与未验证

提交前回归：425项自动测试、0跳过、5个模拟场景、本机HTTP通过。仅离线/回环验证，不是425次真实科研请求。

此前本机真实AIP检索返回3篇，真实AIP Reader返回1篇笔记和5条证据；可共享摘要在`docs/evidence/local_aip_verified_20261012.json`，不含原文、Key或本机路径。

新单入口仅离线测试，未新增付费整链验收。正式梧桐ADP/注册身份与证书、Repository Wrapper、全组协议冻结、v0.2人工签收和科学结论核验仍未完成。

## 张、吴如何接收

先在自己的工作目录保存/提交自己的改动，再fetch，查看赵分支的提交和diff。fetch只下载远端信息，不替换当前工作文件：

```powershell
git fetch origin
git log -3 --oneline origin/zhao/planner-integration
git diff --stat origin/main...origin/zhao/planner-integration
```

不要在有未保存工作时直接切换或merge。需要运行赵的最新代码，推荐在新目录clone一次，再切换到该分支，按requirements与运行指南安装；不复制.env或虚拟环境进入Git。

三方讨论后再决定合main；这次同步不是协议冻结或平台验收。没有自动开启收费实测，也没有向队友发送消息。
