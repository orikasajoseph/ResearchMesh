"""Replay an *archived* real Paper Reader v0.2 result through the local AIP SDK.

This is a NO-API / NO-LLM / NO-PDF-fetch transport and contract test.
The original PaperQA analysis is NOT rerun; researchSucceeded remains False
for this invocation (backend_mode='fixture'), even if the archived result
contains evidence-grounded full-text analysis.

From repository root:
    .\\.venv\\Scripts\\python.exe -X utf8 .\\run_reader_aip_replay_v02.py
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from agents.paper_reader.researchmesh_paper_reader.schemas import PaperReadResult, PaperReadTask
from aip.paper_reader_v02_bridge import (
    create_reader_local_app,
    invoke_reader_aip,
    validate_reader_result,
)
from aip.wu_delivery.structured import ResearchClient

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "agents" / "paper_reader" / "output" / "paper_reader_live.json"


def load_archived_result(source_path: Path) -> tuple[PaperReadTask, dict[str, Any], dict[str, Any]]:
    """Load a complete PASS record and derive a matching one-paper business task.

    The saved PaperRef is reused *unchanged*: never invent a PDF/evidence URL.
    """
    archive = json.loads(source_path.read_text(encoding="utf-8"))
    if archive.get("status") != "PASS":
        raise ValueError("ARCHIVE_NOT_PASS")
    original = PaperReadResult.model_validate(archive["warmResult"])
    if len(original.items) != 1 or original.items[0].data.analysisLevel != "fulltext":
        raise ValueError("NOT_ONE_FULLTEXT_NOTE")

    source_ref = original.items[0].data.source.model_dump(mode="json")
    task = PaperReadTask.model_validate({
        "schemaVersion": "0.2",
        "subtaskId": original.subtaskId,
        "taskType": "paper_read",
        "goal": "Replay previously saved full-text paper analysis through local AIP (no research)",
        "input": {"papers": [source_ref]},
        "constraints": {"maxPapers": 1},
        "outputSchema": "paper_notes.v1",
    })
    validated = validate_reader_result(original, task)  # Includes Leader v0.2 checks.
    evidence = validated["items"][0]["data"]["evidence"]
    meta = {
        "archiveReportedStatus": archive["status"],
        "archiveFileSha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "evidenceCount": len(evidence),
        "localEvidenceUrlCount": sum(
            "127.0.0.1" in (e.get("url") or "") or "localhost" in (e.get("url") or "")
            for e in evidence
        ),
        "sourceFile": str(source_path.resolve()),
        "replayOnly": True,
        "researchExecutedDuringReplay": False,
        "upstreamLLMCalledDuringReplay": False,
        "note": "Localhost source URLs are historical evidence, not durable remotely accessible links.",
    }
    return task, validated, meta


class ArchivedResultAgent:
    """Minimal async fake backend returning *the exact saved analysis*.

    No PaperQAProvider is constructed; the installed SDK still performs
    a genuine TaskCommand / TaskResult lifecycle over in-process ASGI.
    """

    def __init__(self, expected_task: PaperReadTask, saved_result: dict[str, Any]) -> None:
        self.expected_task = expected_task
        self.saved_result = saved_result

    async def run(self, incoming: PaperReadTask) -> PaperReadResult:
        if incoming.model_dump(mode="json") != self.expected_task.model_dump(mode="json"):
            raise ValueError("REPLAY_TASK_MISMATCH")
        return PaperReadResult.model_validate(self.saved_result)


async def replay_via_aip(task: PaperReadTask, original: dict[str, Any]):
    agent = ArchivedResultAgent(task, original)
    app = create_reader_local_app(agent, backend_timeout_seconds=30.0)
    client = ResearchClient(
        partner_url="http://127.0.0.1:8011/rpc",
        leader_id="reader-archive-replay-leader",
        identity_binding_enabled=False,
        transport=httpx.ASGITransport(app=app),
    )
    try:
        return await invoke_reader_aip(
            client, task, timeout_seconds=45.0,
            backend_mode="fixture",  # MUST NOT relabel replay as actual research execution.
        )
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="No-cost local AIP replay of saved full-text Reader result")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    try:
        task, original, metadata = load_archived_result(args.source)
        outcome = asyncio.run(replay_via_aip(task, original))
        accepted = bool(
            outcome.status == "completed"
            and outcome.protocol_completed
            and outcome.business_validated
            and not outcome.research_succeeded
            and outcome.backend_mode == "fixture"
            and outcome.error_code is None
            and outcome.result == original
        )
    except Exception as exc:
        print("Replay failed:", type(exc).__name__, str(exc)[:250])
        return 1

    output_dir = ROOT / "outputs" / (
        "reader-aip-replay-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    records = {
        "business_task_v02.json": task.model_dump(mode="json"),
        "aip_outcome.json": outcome.to_dict(),
        "replay_assessment.json": {**metadata, "aipReplayAccepted": accepted},
    }
    for name, content in records.items():
        (output_dir / name).write_text(
            json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    print("mode: ARCHIVED_FULLTEXT_REPLAY_NO_API")
    print("AIP status:", outcome.status)
    print("protocolCompleted:", outcome.protocol_completed)
    print("businessValidated:", outcome.business_validated)
    print("archivedAnalysisLevel:", original["items"][0]["data"]["analysisLevel"])
    print("archivedEvidenceCount:", metadata["evidenceCount"])
    print("localEvidenceUrls:", metadata["localEvidenceUrlCount"])
    print("researchExecutedDuringReplay: False")
    print("researchSucceeded:", outcome.research_succeeded, "(expected False for replay)")
    print("aipReplayAccepted:", accepted)
    print("Evidence:", output_dir)
    if outcome.error_code:
        print("errorCode:", outcome.error_code)
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
