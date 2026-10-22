"""真实 Reader Wrapper + FakeProvider 的单篇离线验收，不访问模型/PDF/AIP。"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path

from reader_adapter import call_reader, load_reader


def fixture_supports_match(value):
    """只忽略夹具标点前的空格，不改写真实业务证据或接受分类标签。"""
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = re.sub(r"\s+([.!?,;:])", r"\1", value.strip())
    return normalized == "Fixture method. Fixture conclusion. Fixture limitation."


async def verify(wrapper_root):
    wrapper = load_reader(wrapper_root)
    task = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "goal": "Offline compatibility check",
            "input": {"papers": [{"paperId": "offline-p1", "title": "Offline fixture", "doi": None,
                "url": "https://example.invalid/p1", "pdfUrl": "https://example.invalid/p1.pdf", "repositoryUrls": []}]},
            "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1", "requiredSkill": "paper.analyze", "dependsOn": []}

    class FakeProvider:
        def __init__(self, error=False): self.error = error; self.calls = 0
        async def analyze(self, paper):
            self.calls += 1
            if self.error: raise wrapper.provider.StructuredAnalysisError("No evidence contexts")
            answer = ("RESEARCH_PROBLEM: Offline fixture.\nMETHOD: Fixture method (pqac-1234abcd).\n"
                      "FINDINGS: Fixture conclusion (pqac-1234abcd).\nLIMITATIONS: Fixture limitation (pqac-1234abcd).")
            return wrapper.ProviderAnalysis(answer=answer, contexts=[wrapper.ProviderContext(
                context_id="pqac-1234abcd", locator="Fixture page 2", raw_text="Synthetic evidence for adapter testing.",
                summary="Fixture context summary.", score=9, source="Offline fixture")],
                references="", timings={}, diagnostics={"rawAnswer": answer})

    good_provider = FakeProvider()
    success = await call_reader(task, wrapper.PaperReaderAgent(good_provider), wrapper.PaperReadTask)
    task["input"]["papers"][0]["pdfUrl"] = None
    missing_provider = FakeProvider()
    missing = await call_reader(task, wrapper.PaperReaderAgent(missing_provider), wrapper.PaperReadTask)
    task["input"]["papers"][0]["pdfUrl"] = "https://example.invalid/p1.pdf"
    failure = await call_reader(task, wrapper.PaperReaderAgent(FakeProvider(error=True)), wrapper.PaperReadTask)
    checks = {
        "singlePaperCompleted": success["diagnostics"]["businessCompleted"] and success["error"] is None,
        "supportsIsConcreteString": fixture_supports_match(
            success["result"]["items"][0]["data"]["evidence"][0]["supports"]),
        "missingPdfNotCompleted": not missing["diagnostics"]["businessCompleted"] and missing["error"]["code"] == "FULL_TEXT_UNAVAILABLE",
        "missingPdfDoesNotCallProvider": missing_provider.calls == 0,
        "providerErrorMapped": failure["error"]["code"] == "READER_CALL_FAILED" and failure["result"] is None,
    }
    package = Path(wrapper_root).resolve() / "researchmesh_paper_reader"
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))}
    return {"scope": "OFFLINE_REAL_WRAPPER_FAKE_PROVIDER_NO_LLM_NO_PDF_NO_AIP", "passed": all(checks.values()),
            "checks": checks, "wrapperRoot": str(Path(wrapper_root).resolve()), "wrapperSourceHashes": hashes,
            "cases": {"success": success, "missingPdf": missing, "providerError": failure}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper-root", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    record = asyncio.run(verify(args.wrapper_root))
    out = Path(args.output_dir) if args.output_dir else Path("outputs") / ("reader-single-offline-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True, exist_ok=False)
    (out / "acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "scope": record["scope"], "checks": record["checks"], "record": str(out / "acceptance.json")}, ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
