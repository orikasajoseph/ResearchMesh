"""v0.2 六个问题真实规划验收：显式运行才付费，最多 12 次请求，不运行科研 Agent。"""

import asyncio
from datetime import datetime
import json
from pathlib import Path
from run_planner import create_plan
from deepseek_model import safe_failure

CASES = [
    ("只检索", "只检索 2022—2025 年自监督学习用于显微图像细胞分割的论文，最多 6 篇，暂时不要阅读全文或查代码。", ["literature_search"]),
    ("指定论文", "仅阅读 https://arxiv.org/abs/1706.03762 ，总结方法与局限，不搜索其他论文、不查代码。", ["paper_read"]),
    ("指定仓库", "仅静态分析 https://github.com/scikit-learn/scikit-learn 的模块结构、依赖及使用门槛，不运行代码、不检索论文。", ["repository_analyze"]),
    ("澄清", "帮我做一个科研调研。", []),
    ("有条件复现链", "调研 2023—2025 年检索增强生成在临床问答中的研究：检索最多 5 篇论文，阅读其中 2 篇；仅在阅读结果明确给出 GitHub 仓库地址时，依据论文方法和实验设置静态分析复现难度，最多 2 个仓库。缺少地址则提示补充，不另行搜索仓库，不执行代码。", ["literature_search", "paper_read", "repository_analyze"]),
    ("缺少仓库地址", "只静态分析我的代码仓库结构，不检索论文；我还没提供仓库地址。", []),
]


def assess(index, plan):
    tasks = plan["tasks"]
    checks = {"taskTypes": [t["taskType"] for t in tasks] == CASES[index][2],
              "clarification": plan["needsClarification"] == (index in (3, 5)),
              "schemaVersion": plan["schemaVersion"] == "0.2"}
    for task in tasks:
        if task["taskType"] == "repository_analyze":
            checks["noRepositorySearch"] = task["constraints"].get("allowRepositorySearch") is False
            checks["staticOnly"] = task["constraints"].get("executeCode") is False
    if index == 0 and tasks:
        checks["limits"] = all(tasks[0]["constraints"].get(k) == v for k, v in
                               {"yearFrom": 2022, "yearTo": 2025, "maxResults": 6}.items())
    if index == 1 and tasks:
        checks["providedUrl"] = "https://arxiv.org/abs/1706.03762" in json.dumps(tasks[0]["input"])
    if index == 2 and tasks:
        checks["providedUrl"] = "https://github.com/scikit-learn/scikit-learn" in tasks[0]["input"].get("repositoryUrls", [])
    if index == 4 and len(tasks) == 3:
        search, reader, code = tasks
        checks["readerDependency"] = search["subtaskId"] in reader["dependsOn"]
        checks["codeDependency"] = reader["subtaskId"] in code["dependsOn"] and code["constraints"].get("requirePaperNotes") is True
        checks["limits"] = search["constraints"].get("maxResults") == 5 and reader["constraints"].get("maxPapers") == 2 and code["constraints"].get("maxRepositories") == 2
        checks["years"] = search["constraints"].get("yearFrom") == 2023 and search["constraints"].get("yearTo") == 2025
        checks["staticOnly"] = code["constraints"].get("executeCode") is False
    return checks


async def main():
    output = Path(__file__).resolve().parent / "outputs" / datetime.now().strftime("week2-live-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    records = []
    for index, (name, query, _) in enumerate(CASES):
        try:
            plan, metadata = await create_plan(query)
            checks = assess(index, plan)
            record = {"case": name, "query": query, "plan": plan, "metadata": metadata,
                      "semanticChecks": checks, "automatedPassed": all(checks.values()), "humanAccepted": False}
        except Exception as exc:
            record = {"case": name, "query": query, "automatedPassed": False, "error": safe_failure(exc), "humanAccepted": False}
        records.append(record)
        (output / f"case-{index + 1}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, "通过" if record["automatedPassed"] else "未通过", flush=True)
    (output / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("记录：", output)
    return 0 if all(r["automatedPassed"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
