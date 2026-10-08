import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


AGENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from agents.literature.researchmesh_literature import (  # noqa: E402
    LiteratureAgent,
    LiteratureSearchRequest,
    SemanticScholarProvider,
)
from agents.literature.researchmesh_literature.schemas import PaperRecord  # noqa: E402


EXAMPLES_DIR = AGENT_ROOT / "examples"


def _write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metrics(
    request: LiteratureSearchRequest,
    response,
    started_at: str,
    finished_at: str,
    elapsed_ms: float,
    retry_events: list[dict[str, object]],
) -> dict:
    return {
        "requestStartedAt": started_at,
        "requestFinishedAt": finished_at,
        "latencyMs": round(elapsed_ms, 3),
        "query": request.query,
        "maxResults": request.limit,
        "returnedCount": len(response.papers),
        "cacheHit": response.metrics.cacheHit,
        "retryCount": len(retry_events),
        "retryEvents": list(retry_events),
    }


def _validate_papers(response) -> None:
    if response.schemaVersion != "paper_records.v1":
        raise RuntimeError("Unexpected output schema version")
    required_fields = set(PaperRecord.model_fields)
    for paper in response.papers:
        missing = required_fields - set(paper.model_dump())
        if missing:
            raise RuntimeError(f"Paper record is missing fields: {sorted(missing)}")


async def _run_search(
    agent: LiteratureAgent,
    request: LiteratureSearchRequest,
    retry_events: list[dict[str, object]],
):
    retry_events.clear()
    started_at = _now()
    timer = time.perf_counter()
    response = await agent.literature_search(request)
    elapsed_ms = (time.perf_counter() - timer) * 1000
    finished_at = _now()
    _validate_papers(response)
    return response, _metrics(
        request, response, started_at, finished_at, elapsed_ms, retry_events
    )


async def main() -> int:
    api_key_configured = bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY"))
    if not api_key_configured:
        print("SEMANTIC_SCHOLAR_API_KEY is not configured")
        return 0

    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    retry_events: list[dict[str, object]] = []
    provider = SemanticScholarProvider(retry_observer=retry_events.append)
    agent = LiteratureAgent(provider)

    success_request = LiteratureSearchRequest(
        requestId=f"literature-live-{uuid.uuid4().hex}",
        query="AlphaEvolve",
        limit=5,
    )
    _write_json(
        EXAMPLES_DIR / "literature_success_input.json",
        success_request.model_dump(mode="json"),
    )
    success_response, success_metrics = await _run_search(
        agent, success_request, retry_events
    )
    if success_response.status != "success" or not success_response.papers:
        print("Semantic Scholar live search did not return AlphaEvolve papers")
        return 1
    _write_json(
        EXAMPLES_DIR / "literature_success_output.json",
        success_response.model_dump(mode="json"),
    )
    _write_json(
        EXAMPLES_DIR / "literature_success_metrics.json", success_metrics
    )

    empty_request = LiteratureSearchRequest(
        requestId=f"literature-empty-{uuid.uuid4().hex}",
        query=f"researchmesh-no-result-{uuid.uuid4().hex}",
        limit=5,
    )
    _write_json(
        EXAMPLES_DIR / "literature_empty_input.json",
        empty_request.model_dump(mode="json"),
    )
    empty_response, empty_metrics = await _run_search(
        agent, empty_request, retry_events
    )
    _write_json(
        EXAMPLES_DIR / "literature_empty_output.json",
        empty_response.model_dump(mode="json"),
    )
    _write_json(EXAMPLES_DIR / "literature_empty_metrics.json", empty_metrics)

    print(json.dumps(success_metrics, ensure_ascii=False, indent=2))
    print(f"firstPaperTitle={success_response.papers[0].title}")
    print(f"firstPaperHasDoi={bool(success_response.papers[0].doi)}")
    print(f"firstPaperHasPdfUrl={bool(success_response.papers[0].pdfUrl)}")
    if empty_response.papers:
        print(
            "warning: random query returned "
            f"{len(empty_response.papers)} real provider-ranked result(s)"
        )
    else:
        print("warning: random query returned no papers")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Literature live test interrupted")
        raise SystemExit(130)
    except Exception as exc:
        print(f"Literature live test failed ({type(exc).__name__})")
        raise SystemExit(1)
