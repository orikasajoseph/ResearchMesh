"""张的实际 Reader Wrapper + FakeProvider 多篇离线验收，不请求模型/PDF/AIP。"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path

from reader_adapter import load_reader
from reader_batch_adapter import call_reader_batch


def fixture_task(missing_pdf=False):
    papers = [{"paperId": f"offline-p{i}", "title": f"Offline fixture {i}", "doi": None,
               "url": f"https://example.invalid/p{i}", "pdfUrl": f"https://example.invalid/p{i}.pdf", "repositoryUrls": []}
              for i in range(1, 4)]
    if missing_pdf:
        papers[0]["pdfUrl"] = None
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "goal": "Offline batch compatibility check",
            "input": {"papers": papers}, "constraints": {"maxPapers": 3}, "outputSchema": "paper_notes.v1",
            "requiredSkill": "paper.analyze", "dependsOn": []}


async def verify(wrapper_root):
    wrapper = load_reader(wrapper_root)

    class FakeProvider:
        def __init__(self, modes): self.modes = modes; self.calls = []
        async def analyze(self, paper):
            self.calls.append(paper.paperId)
            mode = self.modes.get(paper.paperId, "success")
            if mode == "error":
                raise wrapper.provider.StructuredAnalysisError("No evidence contexts; synthetic diagnostic")
            answer = (f"RESEARCH_PROBLEM: Offline fixture {paper.paperId}.\n"
                      f"METHOD: Fixture method {paper.paperId} (pqac-1234abcd).\n"
                      f"FINDINGS: Fixture conclusion {paper.paperId} (pqac-1234abcd).\n"
                      f"LIMITATIONS: Fixture limitation {paper.paperId} (pqac-1234abcd).")
            if mode == "empty":
                answer = "An intentionally unstructured synthetic answer."
            return wrapper.ProviderAnalysis(answer=answer, contexts=[wrapper.ProviderContext(
                context_id="pqac-1234abcd", locator=f"Fixture {paper.paperId} page 2",
                raw_text=f"Synthetic evidence for {paper.paperId}.", summary=f"Context summary for {paper.paperId}.",
                score=9, source=paper.title)], references="", timings={}, diagnostics={"rawAnswer": answer})

    class RecordingAgent:
        def __init__(self, provider): self.reader = wrapper.PaperReaderAgent(provider); self.requests = []
        async def run(self, task):
            self.requests.append(task.model_dump(mode="json"))
            return await self.reader.run(task)

    cases = {}
    for name, modes, missing in [
        ("allSuccess", {}, False),
        ("partialSuccess", {"offline-p2": "error"}, False),
        ("allFailed", {"offline-p2": "error", "offline-p3": "empty"}, True),
    ]:
        provider = FakeProvider(modes)
        agent = RecordingAgent(provider)
        out = await call_reader_batch(fixture_task(missing), agent, wrapper.PaperReadTask)
        cases[name] = {"outcome": out, "readerRequests": agent.requests, "providerCalls": provider.calls}

    good = cases["allSuccess"]["outcome"]
    partial = cases["partialSuccess"]["outcome"]
    failed = cases["allFailed"]["outcome"]
    requests = [request for case in cases.values() for request in case["readerRequests"]]
    checks = {
        "allSuccess": good["diagnostics"]["outcome"] == "success" and good["diagnostics"]["businessCompleted"]
            and len(good["result"]["items"]) == 3 and good["error"] is None,
        "partialSuccessRetainsBothGoodPapers": partial["diagnostics"]["outcome"] == "partial"
            and [item["data"]["source"]["paperId"] for item in partial["result"]["items"]] == ["offline-p1", "offline-p3"],
        "partialNotComplete": not partial["diagnostics"]["businessCompleted"]
            and partial["error"]["code"] == "PARTIAL_READER_FAILURE",
        "allFailedHasNoNotes": failed["diagnostics"]["outcome"] == "failed" and not failed["result"]["items"]
            and failed["error"]["code"] == "ALL_READERS_FAILED" and not failed["diagnostics"]["businessCompleted"],
        "failureReasonsRetained": [row["error"]["code"] for row in failed["paperOutcomes"]] ==
            ["FULL_TEXT_UNAVAILABLE", "READER_CALL_FAILED", "READER_EMPTY_RESULT"],
        "everyReaderRequestSinglePaper": len(requests) == 9 and all(len(r["input"]["papers"]) == 1
            and r["constraints"] == {"maxPapers": 1} and "requiredSkill" not in r and "dependsOn" not in r for r in requests),
        "aggregateUsesParentTaskId": all(case["outcome"]["result"]["subtaskId"] == "S2" for case in cases.values()),
        "missingPdfDoesNotCallProvider": cases["allFailed"]["providerCalls"] == ["offline-p2", "offline-p3"],
        "supportsPreserved": all(isinstance(item["data"]["evidence"][0]["supports"], str)
            and f"Fixture conclusion {item['data']['source']['paperId']}" in item["data"]["evidence"][0]["supports"]
            for case in cases.values() for item in case["outcome"]["result"]["items"]),
    }
    package = Path(wrapper_root).resolve() / "researchmesh_paper_reader"
    return {"scope": "OFFLINE_REAL_READER_WRAPPER_FAKE_PROVIDER_NO_LLM_NO_PDF_NO_AIP", "passed": all(checks.values()),
            "checks": checks, "wrapperRoot": str(Path(wrapper_root).resolve()),
            "wrapperSourceHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))},
            "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper-root", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    record = asyncio.run(verify(args.wrapper_root))
    out = Path(args.output_dir) if args.output_dir else Path("outputs") / ("reader-batch-offline-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True, exist_ok=False)
    (out / "acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Reader 多篇离线验收", "", "本记录使用张的实际 Wrapper 和合成 Provider 数据，没有读取真实论文或执行平台互联。", "",
             "| 场景 | 完成篇数 | 总篇数 | 汇总判断 |", "|---|---:|---:|---|"]
    for name, case in record["cases"].items():
        diag = case["outcome"]["diagnostics"]
        lines.append(f"| {name} | {diag['completedCount']} | {diag['requestedCount']} | {diag['outcome']} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "scope": record["scope"], "checks": record["checks"],
                      "record": str(out / "acceptance.json")}, ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
