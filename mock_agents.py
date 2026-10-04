"""三个纯本地测试替身。数据、链接和结论全部是模拟的。"""

from __future__ import annotations

from copy import deepcopy

from resolver.base import AgentHandle


MOCK_WARNING = "模拟数据：未调用真实检索、未阅读真实论文、未核验仓库；不能用于科研结论或 AIP 验收。"


def _result(payload, summary, items):
    return {
        "schemaVersion": "0.1",
        "subtaskId": payload["subtaskId"],
        "taskType": payload["taskType"],
        "summary": summary,
        "items": items,
        "warnings": [MOCK_WARNING],
    }


def _papers(payload, dependency_results):
    if payload["input"].get("papers"):
        papers = deepcopy(payload["input"]["papers"])
        for paper in papers:
            paper["title"] = paper.get("title") or "【模拟数据】指定论文阅读示例"
        return papers
    papers = []
    for result in dependency_results.values():
        papers.extend(deepcopy(item["data"]) for item in result["items"] if item["kind"] == "paper")
    if not papers:
        papers = [{
            "title": "【模拟数据】指定论文阅读示例",
            "doi": payload["input"].get("doi"),
            "url": "https://example.invalid/mock/paper-001",
            "isMock": True,
        }]
    return papers


class LiteratureMockAgent:
    async def execute(self, payload, dependency_results):
        paper = {
            "title": "【模拟数据】图神经网络与药物发现示例论文",
            "authors": ["模拟作者"],
            "year": 2025,
            "doi": None,
            "url": "https://example.invalid/mock/paper-001",
            "relevance": "仅演示检索结果结构，不表示真实相关性",
            "isMock": True,
        }
        return _result(payload, "【模拟】返回一条论文记录。", [{"kind": "paper", "data": paper}])


class PaperReaderMockAgent:
    async def execute(self, payload, dependency_results):
        items = [{"kind": "paper_note", "data": {
            "title": paper["title"],
            "method": "【模拟】这里将来填写从全文中提取的方法",
            "findings": "【模拟】这里将来填写有原文依据的发现",
            "limitations": "【模拟】这里将来填写论文局限",
            "source": {"title": paper["title"], "doi": paper.get("doi"), "url": paper.get("url")},
            "isMock": True,
        }} for paper in _papers(payload, dependency_results)]
        return _result(payload, "【模拟】生成论文阅读条目，未读取全文。", items)


class CodeMockAgent:
    async def execute(self, payload, dependency_results):
        papers = _papers(payload, dependency_results)
        return _result(payload, "【模拟】生成一条仓库记录，未进行搜索或核验。", [{"kind": "repository", "data": {
            "name": "mock-repository",
            "url": "https://example.invalid/mock/repository",
            "license": "UNKNOWN（模拟，未核验）",
            "relatedPaper": papers[0]["title"],
            "relationEvidence": "仅为模拟关联，没有真实证据",
            "paperSource": papers[0].get("url"),
            "isMock": True,
        }}])


class FailOnTask:
    """只用于故障注入，不是业务结果状态机。"""
    def __init__(self, executor, subtask_id):
        self.executor = executor
        self.subtask_id = subtask_id

    async def execute(self, payload, dependency_results):
        if payload["subtaskId"] == self.subtask_id:
            raise RuntimeError(f"模拟故障：{self.subtask_id} 执行失败")
        return await self.executor.execute(payload, dependency_results)


def build_mock_agents(fail_task=None):
    agents = []
    for capability, executor in (
        ("literature_search", LiteratureMockAgent()),
        ("paper_read", PaperReaderMockAgent()),
        ("code_search", CodeMockAgent()),
    ):
        if fail_task:
            executor = FailOnTask(executor, fail_task)
        agents.append(AgentHandle(f"mock-{capability}", (capability,), executor))
    return agents
