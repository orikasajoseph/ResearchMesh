"""Local-only official AIP Partner for Zhang's unmodified RepositoryAgent."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from aip_business_partner import LocalBackendFailure, create_local_business_app


ROOT = Path(__file__).resolve().parent
_FAILURE_CODES = frozenset({
    "REPOSITORY_UNAVAILABLE", "INVALID_REPOSITORY_URL",
    "REPOSITORY_CLONE_FAILED", "STATIC_ANALYSIS_FAILED",
})


def _paper_notes_for_repository(notes):
    """Validate paper_notes.v1 and send bounded claims with stable evidence IDs."""
    from agents.paper_reader.researchmesh_paper_reader.schemas import PaperNoteData

    if not isinstance(notes, list) or len(notes) > 3:
        raise ValueError("INVALID_PAPER_NOTES")
    projected = []
    for raw in notes:
        if not isinstance(raw, dict) or raw.get("isMock") is True:
            raise ValueError("INVALID_PAPER_NOTES")
        note = PaperNoteData.model_validate(raw)
        source = note.source.model_dump(mode="json")
        evidence = []
        for index, item in enumerate(note.evidence[:6]):
            if item.sourceId != source["paperId"] or item.url != (source["pdfUrl"] or source["url"]):
                raise ValueError("PAPER_EVIDENCE_SOURCE_MISMATCH")
            identity = json.dumps([source["paperId"], index, item.url, item.locator,
                                   item.supports], ensure_ascii=False)
            evidence.append({
                "evidenceId": "P-" + sha256(identity.encode("utf-8")).hexdigest()[:16],
                "sourceId": item.sourceId, "url": item.url, "locator": item.locator[:300],
                "supports": item.supports[:1200],
                "text": item.text[:1200] if item.text else None,
            })
        projected.append({"source": source, "method": note.method[:2000],
                          "findings": note.findings[:2000],
                          "limitations": note.limitations[:2000],
                          "analysisLevel": note.analysisLevel, "evidence": evidence})
    return projected


def repository_request(payload):
    """Keep the seven-field envelope; remove only Leader binding fields inside it."""
    from agents.repository.researchmesh_repository.schemas import RepositoryAnalyzeRequest

    if payload["taskType"] != "repository_analyze":
        raise LocalBackendFailure("INVALID_REQUEST")
    request = {
        "schemaVersion": payload["schemaVersion"],
        "subtaskId": payload["subtaskId"],
        "taskType": payload["taskType"],
        "goal": payload["goal"],
        "input": {"repositoryUrls": payload["input"]["repositoryUrls"],
                  "paperNotes": _paper_notes_for_repository(payload["input"].get("paperNotes", []))},
        "constraints": {
            "maxRepositories": payload["constraints"].get("maxRepositories", 1),
            "executeCode": payload["constraints"]["executeCode"],
        },
        "outputSchema": payload["outputSchema"],
    }
    return RepositoryAnalyzeRequest.model_validate(request)


def make_repository_executor(agent, record_dir=None):
    """Inject a test agent or the real one; no direct CodeWiki access here."""
    def event(name, **details):
        if record_dir is None:
            return
        record = {"event": name, "at": datetime.now(timezone.utc).isoformat(), **details}
        with (Path(record_dir) / "partner_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def execute(payload):
        request = repository_request(payload)
        event("PARTNER_TASK_ACCEPTED", subtaskId=request.subtaskId, taskType=request.taskType)
        try:
            result = await asyncio.to_thread(agent.analyze, request)
        except Exception as exc:
            event("REPOSITORY_BACKEND_FAILED", exceptionType=type(exc).__name__)
            raise LocalBackendFailure("BACKEND_UNAVAILABLE") from None
        data = result.model_dump(mode="json")
        if not data["items"]:
            code = next((warning.split(":", 1)[0] for warning in data["warnings"]
                         if warning.split(":", 1)[0] in _FAILURE_CODES), "BACKEND_UNAVAILABLE")
            event("REPOSITORY_BUSINESS_FAILED", code=code)
            raise LocalBackendFailure(code)
        record = data["items"][0]["data"]
        diagnostics = record["diagnostics"]
        event("REPOSITORY_STATIC_COMPLETED", counts=diagnostics["counts"],
              staticAnalysisMs=diagnostics["metrics"]["staticAnalysisMs"])
        if "LLM_ENRICHMENT_UNAVAILABLE" not in data["warnings"]:
            event("REPOSITORY_ENRICHMENT_COMPLETED",
                  llmEnrichmentMs=diagnostics["metrics"]["llmEnrichmentMs"])
        else:
            event("REPOSITORY_ENRICHMENT_UNAVAILABLE")
        return data

    return execute


def create_live_app():
    from agents.repository.researchmesh_repository import RepositoryAgent

    record_dir = Path(os.environ["RESEARCHMESH_AIP_RECORD_DIR"]).resolve()
    if not record_dir.is_dir() or ROOT not in record_dir.parents:
        raise ValueError("INVALID_REPOSITORY_RECORD_DIRECTORY")
    return create_local_business_app(
        execute=make_repository_executor(RepositoryAgent(), record_dir),
        partner_aic=os.environ["RESEARCHMESH_LOCAL_PARTNER_AIC"],
        backend_timeout_seconds=180,
        background_execution=True,
        mode="local-repository-live",
    )
