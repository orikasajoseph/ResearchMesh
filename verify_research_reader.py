"""实际 Literature/Reader Wrapper 与合成 Provider 的串联离线验收。"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from literature_adapter import call_wrapper, load_wrapper
from reader_adapter import load_reader
from reader_batch_adapter import call_reader_batch
from run_research import run_pipeline, save_pipeline, load_resume_plan, load_resume_record


def fixture_plan():
    return {"schemaVersion": "0.2", "userQuery": "离线合成夹具：检索并阅读三篇论文", "needsClarification": False,
            "clarificationQuestion": None, "tasks": [
                {"schemaVersion": "0.2", "subtaskId": "Search", "taskType": "literature_search",
                 "goal": "Offline fixture search", "input": {"query": "offline synthetic fixtures"},
                 "constraints": {"maxResults": 3}, "outputSchema": "paper_records.v1",
                 "requiredSkill": "literature.search", "dependsOn": []},
                {"schemaVersion": "0.2", "subtaskId": "Reader", "taskType": "paper_read",
                 "goal": "Offline fixture reading", "input": {"papers": []}, "constraints": {"maxPapers": 3},
                 "outputSchema": "paper_notes.v1", "requiredSkill": "paper.analyze", "dependsOn": ["Search"]}]}


def stamp_offline(record):
    record = deepcopy(record)
    record["metadata"].update(mode="offline-real-wrappers-fake-providers", fixtureReplay=True,
                               llmUsed=False, realSourceRequested=False, paperQAExecuted=False,
                               pdfFetched=False, syntheticData=True)
    return record


async def verify(reader_root, output):
    project = Path(__file__).resolve().parent
    literature = load_wrapper(project / "agents" / "literature")
    wrapper = load_reader(reader_root)
    cases = {}
    saved_paths = {}

    class SearchProvider:
        def __init__(self, missing=False): self.calls = []; self.missing = missing
        async def search(self, request):
            self.calls.append(request.model_dump(mode="json"))
            papers = [literature.schemas.PaperRecord(
                paperId=f"fixture-p{i}", title=f"Synthetic fixture {i}", url=f"https://example.invalid/p{i}",
                pdfUrl=None if self.missing and i == 1 else f"https://example.invalid/p{i}.pdf") for i in range(1, 4)]
            return literature.provider.ProviderSearchResult(papers, 3, 1, False)

    class ReaderProvider:
        def __init__(self, modes=None): self.calls = []; self.modes = modes or {}
        async def analyze(self, paper):
            self.calls.append(paper.paperId)
            mode = self.modes.get(paper.paperId)
            if mode == "error": raise RuntimeError("synthetic-private-error")
            if mode == "cancel": raise asyncio.CancelledError()
            answer = (f"RESEARCH_PROBLEM: Synthetic problem {paper.paperId}.\n"
                      f"METHOD: Synthetic method {paper.paperId} (pqac-1234abcd).\n"
                      f"FINDINGS: Synthetic finding {paper.paperId} (pqac-1234abcd).\n"
                      f"LIMITATIONS: Synthetic limitation {paper.paperId} (pqac-1234abcd).")
            return wrapper.ProviderAnalysis(answer=answer, contexts=[wrapper.ProviderContext(
                context_id="pqac-1234abcd", locator="Fixture page 2", raw_text="Synthetic evidence only.",
                summary="Synthetic summary only.", score=9, source=paper.title)], references="", timings={},
                diagnostics={"rawAnswer": answer})

    async def run_case(name, modes=None, *, missing=False, resume=None):
        search_provider, reader_provider = SearchProvider(missing), ReaderProvider(modes)
        search_agent, reader_agent = literature.LiteratureAgent(search_provider), wrapper.PaperReaderAgent(reader_provider)
        requests, planner_calls = [], []
        async def planner(query):
            planner_calls.append(query)
            return fixture_plan(), {"requestCount": 0, "mode": "offline-injected-plan"}
        async def search(payload):
            return await call_wrapper(payload, "offline-fixture", search_agent, literature.LiteratureSearchRequest)
        class Agent:
            async def run(self, task):
                requests.append(task.model_dump(mode="json"))
                return await reader_agent.run(task)
        async def read(payload, **options):
            return await call_reader_batch(payload, Agent(), wrapper.PaperReadTask, **options)
        path = None
        def first(record):
            nonlocal path
            path = save_pipeline(stamp_offline(record), output / name)
        def progress(record):
            save_pipeline(stamp_offline(record), output_dir=path)
        old_plan = load_resume_plan(resume, with_reader=True) if resume else None
        old_record = load_resume_record(resume, old_plan) if resume else None
        old_bytes = (resume / "pipeline.json").read_bytes() if resume else None
        try:
            record = await run_pipeline(fixture_plan()["userQuery"], planner_call=planner, search_call=search,
                                        reader_call=read, saved_plan=old_plan, saved_record=old_record,
                                        on_plan=first, on_progress=progress)
            record = stamp_offline(record)
            save_pipeline(record, output_dir=path)
        except asyncio.CancelledError:
            record = load_resume_record(path, load_resume_plan(path, with_reader=True))
        cases[name] = {"record": record, "plannerCalls": planner_calls, "searchCalls": search_provider.calls,
                       "readerRequests": requests, "readerProviderCalls": reader_provider.calls,
                       "historyUnchanged": not resume or (resume / "pipeline.json").read_bytes() == old_bytes,
                       "directory": str(path)}
        saved_paths[name] = path

    await run_case("allSuccess")
    await run_case("partial", {"fixture-p2": "error"})
    await run_case("resumePartial", resume=saved_paths["partial"])
    await run_case("allFailed", {"fixture-p2": "error", "fixture-p3": "error"}, missing=True)
    await run_case("interrupted", {"fixture-p2": "cancel"})
    await run_case("resumeInterrupted", resume=saved_paths["interrupted"])
    await run_case("resumeCompleted", resume=saved_paths["allSuccess"])
    good, partial, failed = [cases[name]["record"] for name in ["allSuccess", "partial", "allFailed"]]
    requests = [r for case in cases.values() for r in case["readerRequests"]]
    checks = {
        "allSuccess": good["metadata"]["pipelineOutcome"] == "completed" and len(good["readerOutcome"]["result"]["items"]) == 3,
        "partialRetainsFirstAndLast": [p["data"]["source"]["paperId"] for p in partial["readerOutcome"]["result"]["items"]] == ["fixture-p1", "fixture-p3"],
        "partialNotCompleted": partial["metadata"]["pipelineOutcome"] == "partial" and not partial["readerOutcome"]["diagnostics"]["businessCompleted"],
        "failuresRetained": failed["readerOutcome"]["error"]["code"] == "ALL_READERS_FAILED" and failed["readerOutcome"]["result"]["items"] == [],
        "missingPdfSkipsProvider": cases["allFailed"]["readerProviderCalls"] == ["fixture-p2", "fixture-p3"],
        "singlePaperRequests": all(r["constraints"] == {"maxPapers": 1} and len(r["input"]["papers"]) == 1 for r in requests),
        "partnerPayloadStripped": all("dependsOn" not in r and "requiredSkill" not in r for r in requests),
        "supportsAreConcreteStrings": all(isinstance(p["data"]["evidence"][0]["supports"], str) and "Synthetic finding" in p["data"]["evidence"][0]["supports"] for p in good["readerOutcome"]["result"]["items"]),
        "resumePartialOnlyRetriesMiddle": cases["resumePartial"]["readerProviderCalls"] == ["fixture-p2"],
        "interruptionSavesFirst": cases["interrupted"]["record"]["metadata"]["pipelineOutcome"] == "interrupted" and [p["paperId"] for p in cases["interrupted"]["record"]["readerProgress"]] == ["fixture-p1"],
        "resumeInterruptionOnlyRetriesRemaining": cases["resumeInterrupted"]["readerProviderCalls"] == ["fixture-p2", "fixture-p3"],
        "completedResumeNoReader": cases["resumeCompleted"]["readerProviderCalls"] == [] and cases["resumeCompleted"]["readerRequests"] == [],
        "resumesNoPlannerOrSearch": all(not cases[n]["plannerCalls"] and not cases[n]["searchCalls"] for n in ["resumePartial", "resumeInterrupted", "resumeCompleted"]),
        "resumeDoesNotOverwriteHistory": all(c["historyUnchanged"] for c in cases.values()),
        "allRecordsTruthfulOffline": all(not c["record"]["metadata"]["llmUsed"] and c["record"]["metadata"]["syntheticData"] and not c["record"]["metadata"]["aipUsed"] for c in cases.values()),
    }
    return {"scope": "OFFLINE_REAL_LITERATURE_AND_READER_WRAPPERS_FAKE_PROVIDERS_NO_LLM_NO_PDF_NO_AIP",
            "passed": all(checks.values()), "checks": checks, "cases": cases,
            "readerRoot": str(Path(reader_root).resolve()),
            "readerSourceHashes": {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted((Path(reader_root) / "researchmesh_paper_reader").glob("*.py"))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader-root", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or Path("outputs") / ("research-reader-offline-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True, exist_ok=False)
    blocked = []
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        blocked.append("external-socket"); raise RuntimeError("OFFLINE_NETWORK_BLOCKED")
    async def guarded_send(*args, **kwargs):
        blocked.append("http"); raise RuntimeError("OFFLINE_NETWORK_BLOCKED")
    with patch.object(socket.socket, "connect", guarded_connect), patch.object(httpx.AsyncClient, "send", guarded_send):
        record = asyncio.run(verify(args.reader_root, output))
    record["checks"]["noExternalNetworkAttempts"] = not blocked
    record.update(passed=all(record["checks"].values()), externalNetworkAttempts=len(blocked))
    (output / "acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "scope": record["scope"], "checks": record["checks"],
                      "record": str(output / "acceptance.json")}, ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
