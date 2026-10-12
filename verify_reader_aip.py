"""Actual Zhang Reader Wrapper + synthetic Provider + official AIP, offline only."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx
from aip_business_partner import create_local_business_app
from aip_reader_partner import make_reader_executor
from reader_adapter import call_reader, load_reader, to_reader_request
from run_aip_literature import invoke_business
from run_aip_reader import assess_reader_run

ROOT = Path(__file__).resolve().parent


def fixture_task():
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
            "goal": "Offline synthetic Reader AIP compatibility check",
            "input": {"papers": [{"paperId": "offline-p1", "title": "Offline fixture paper",
                "doi": None, "url": "https://example.invalid/p1",
                "pdfUrl": "https://example.invalid/p1.pdf", "repositoryUrls": []}]},
            "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1"}


async def verify(wrapper_root):
    wrapper = load_reader(wrapper_root)
    calls, requests, journals = [], [], []
    class FakeProvider:
        async def analyze(self, paper):
            calls.append(paper.paperId)
            answer = ("RESEARCH_PROBLEM: Synthetic research problem.\n"
                      "METHOD: Synthetic method (pqac-1234abcd).\n"
                      "FINDINGS: Synthetic concrete conclusion (pqac-1234abcd).\n"
                      "LIMITATIONS: Synthetic limitation (pqac-1234abcd).")
            return wrapper.ProviderAnalysis(answer=answer, contexts=[wrapper.ProviderContext(
                context_id="pqac-1234abcd", locator="Synthetic page 2", raw_text="Synthetic evidence only.",
                summary="Synthetic context summary.", score=9, source=paper.title)],
                references="", timings={}, diagnostics={"rawAnswer": answer})
    actual = wrapper.PaperReaderAgent(FakeProvider())
    class RecordingAgent:
        async def run(self, task):
            requests.append(task.model_dump(mode="json"))
            return await actual.run(task)
    task = fixture_task()
    async def worker():
        outcome = await call_reader(task, RecordingAgent(), wrapper.PaperReadTask)
        journal = {"scope": "OFFLINE_FAKE_PROVIDER_NO_LLM_NO_PDF", "phase": "completed",
                   "task": deepcopy(task), "outcome": outcome, "modelExecutionAttemptedEver": False}
        journals.append(journal)
        return journal
    app = create_local_business_app(execute=make_reader_executor(worker, task), partner_aic="offline-reader",
                                    background_execution=True, mode="local-reader-live")
    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request",
                      side_effect=AssertionError("External network is forbidden in offline validation")):
        first = await invoke_business(task, "http://127.0.0.1/rpc", "offline-reader",
                    expected_task_type="paper_read", transport=httpx.ASGITransport(app=app), poll_seconds=.001)
        second = await invoke_business(task, "http://127.0.0.1/rpc", "offline-reader",
                    expected_task_type="paper_read", transport=httpx.ASGITransport(app=app), poll_seconds=.001)
        missing = deepcopy(task)
        missing["input"]["papers"][0]["pdfUrl"] = None
        unavailable = await call_reader(missing, RecordingAgent(), wrapper.PaperReadTask)
    result = first["execution"]["result"] if first["execution"] else None
    commands = [row["command"]["command"] for row in first["aipMessages"]]
    checks = {
        "officialAipCompleted": first["error"] is None and first["execution"]["state"] == "completed",
        "startPollComplete": commands[0] == "start" and "get" in commands and commands[-1] == "complete",
        "actualWrapperResultMatchesAip": result == journals[0]["outcome"]["result"],
        "singlePaperSevenFields": len(requests) == 2 and all(to_reader_request(r) == r
            and len(r) == 7 and len(r["input"]["papers"]) == 1 for r in requests),
        "secondTaskReusesResultWithoutProvider": len(journals) == 1 and calls == ["offline-p1"]
            and second["error"] is None and second["execution"]["result"] == result,
        "newAipTaskIdsSameSubtask": first["execution"]["taskId"] != second["execution"]["taskId"]
            and first["execution"]["subtaskId"] == second["execution"]["subtaskId"] == "S2",
        "missingPdfDoesNotCallProvider": unavailable["error"]["code"] == "FULL_TEXT_UNAVAILABLE"
            and calls == ["offline-p1"],
        "supportsConcreteStringPreserved": all(isinstance(e["supports"], str)
            and "Synthetic concrete conclusion" in e["supports"] for e in result["items"][0]["data"]["evidence"]),
        "offlineNeverClaimsLiveBusinessReady": not assess_reader_run(first, journals[0], task)["businessReady"],
        "noExternalNetworkAttempts": True,
    }
    package = Path(wrapper_root).resolve() / "researchmesh_paper_reader"
    return {"passed": all(checks.values()), "scope": "OFFLINE_REAL_READER_WRAPPER_FAKE_PROVIDER_OFFICIAL_AIP_ASGI_NO_LLM_NO_PDF",
            "checks": checks, "realResearchExecuted": False, "modelCalled": False,
            "pdfRead": False, "platformDiscoveryUsed": False, "wrapperRoot": str(Path(wrapper_root).resolve()),
            "wrapperHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))},
            "cases": {"first": first, "cachedSecond": second, "missingPdf": unavailable}, "journals": journals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader-root", required=True)
    args = parser.parse_args()
    record = asyncio.run(verify(args.reader_root))
    output = ROOT / "outputs" / datetime.now().strftime("reader-aip-offline-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    (output / "acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "scope": record["scope"], "checks": record["checks"],
                      "record": str(output / "acceptance.json")}, ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
