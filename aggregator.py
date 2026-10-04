"""不调用大模型的最小汇总器；生成明确标注模拟内容的可读报告。"""

import json


def render_report(run):
    lines = [
        "# ResearchMesh 本地模拟演示报告", "",
        "> 所有论文、分析与仓库记录均为模拟数据。未连接真实模型、未调用平台发现或 AIP；不能作为科研依据或赛事互联证明。",
        "", f"研究需求：{run['userQuery']}", "",
    ]
    if run["needsClarification"]:
        lines.extend([f"需要补充信息：{run['clarificationQuestion']}", "未执行任何任务。", ""])
    for result in run["results"]:
        lines.extend([f"## {result['subtaskId']}：{result['taskType']}", "", result["summary"], ""])
        for item in result["items"]:
            lines.extend([f"条目类型：{item['kind']}", "", "```json",
                          json.dumps(item["data"], ensure_ascii=False, indent=2), "```", ""])
        for warning in result["warnings"]:
            lines.append(f"注意：{warning}")
        lines.append("")
    failed = [record for record in run["executionRecords"] if not record["succeeded"]]
    if failed:
        lines.extend(["## 本地执行错误（不是业务结果 status）", ""])
        for record in failed:
            lines.append(f"- {record['subtaskId']}：{record['error']['code']}；{record['error']['message']}")
        lines.append("")
    lines.extend(["## 执行摘要", "",
                  f"成功 {len(run['results'])} 项；未成功或跳过 {len(failed)} 项。",
                  f"依赖执行批次：{run['executionWaves']}", ""])
    return "\n".join(lines)
