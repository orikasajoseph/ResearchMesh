from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents.paper_reader.researchmesh_paper_reader import (
    PaperQAProvider,
    PaperReaderAgent,
)


DEFAULT_PDF = ROOT.parent / "paper-qa-clean" / "tests" / "stub_data" / "paper.pdf"
OUTPUT = ROOT / "agents" / "paper_reader" / "output" / "paper_reader_live.json"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def payload(subtask_id: str, pdf_url: str) -> dict:
    return {
        "schemaVersion": "0.2",
        "subtaskId": subtask_id,
        "taskType": "paper_read",
        "goal": "Produce one evidence-grounded structured paper note.",
        "input": {
            "papers": [
                {
                    "paperId": "10.1021/acs.jctc.2c01235",
                    "title": "A Perspective on Explanations of Molecular Prediction Models",
                    "doi": "10.1021/acs.jctc.2c01235",
                    "url": "https://doi.org/10.1021/acs.jctc.2c01235",
                    "pdfUrl": pdf_url,
                    "repositoryUrls": [],
                }
            ]
        },
        "constraints": {"maxPapers": 1},
        "outputSchema": "paper_notes.v1",
    }


async def fetch_local_pdf(url: str) -> bytes:
    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def main() -> int:
    if not DEFAULT_PDF.is_file():
        print(f"Live PDF is missing: {DEFAULT_PDF}")
        return 2

    handler = partial(QuietHandler, directory=str(DEFAULT_PDF.parent))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pdf_url = f"http://127.0.0.1:{server.server_port}/{DEFAULT_PDF.name}"

    try:
        startup_started = time.perf_counter()
        provider = PaperQAProvider(pdf_fetcher=fetch_local_pdf)
        agent = PaperReaderAgent(provider)
        startup_finished = time.perf_counter()

        run1_started = time.perf_counter()
        run1 = await agent.run(payload("paper-reader-live-1", pdf_url))
        run1_finished = time.perf_counter()
        cold_analysis = agent.last_provider_analysis

        run2_started = time.perf_counter()
        run2 = await agent.run(payload("paper-reader-live-2", pdf_url))
        run2_finished = time.perf_counter()
        warm_analysis = agent.last_provider_analysis

        for result in (run1, run2):
            if not result.items or result.items[0].data.analysisLevel != "fulltext":
                raise RuntimeError("live full-text Paper Reader result is incomplete")
            if not result.items[0].data.evidence:
                raise RuntimeError("live full-text Paper Reader returned no evidence")
            if "pqac-" in result.model_dump_json():
                raise RuntimeError("business result leaked a pqac context ID")

        output = {
            "status": "PASS",
            "embeddingModelLoadCount": provider.embedding_model_load_count,
            "startupMs": round((startup_finished - startup_started) * 1000, 3),
            "embeddingModelLoadMs": provider.embedding_model_load_ms,
            "coldTaskMs": round((run1_finished - run1_started) * 1000, 3),
            "warmTaskMs": round((run2_finished - run2_started) * 1000, 3),
            "coldProviderTimings": cold_analysis.timings,
            "warmProviderTimings": warm_analysis.timings,
            "warmResult": run2.model_dump(mode="json"),
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in output.items() if k != "warmResult"}, ensure_ascii=False, indent=2))
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Paper Reader live test interrupted")
        raise SystemExit(130)
    except Exception as exc:
        print(f"Paper Reader live test failed ({type(exc).__name__}): {exc}")
        raise SystemExit(1)
