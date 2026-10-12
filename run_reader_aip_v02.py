"""Local AIP v0.2 Paper Reader proof, never public or platform registered.

Default offline mode uses a paper with pdfUrl=null: protocol should complete,
but researchSucceeded MUST be false. Pass --live --task <path> to invoke real
PaperQA on a user-supplied accessible PDF; this may call paid DeepSeek API.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
from dotenv import load_dotenv

from agents.paper_reader.researchmesh_paper_reader.agent import PaperReaderAgent
from aip.paper_reader_v02_bridge import (
    business_task_from_planner, create_reader_local_app, invoke_reader_aip,
)
from aip.wu_delivery.structured import ResearchClient

ROOT = Path(__file__).resolve().parent
DEFAULT_TASK = ROOT / "examples" / "reader_aip_no_pdf_v02.json"


class NoNetworkProvider:
    async def analyze(self, paper):
        raise AssertionError("Offline no-PDF test unexpectedly invoked provider")


async def run(task, live):
    agent = PaperReaderAgent() if live else PaperReaderAgent(provider=NoNetworkProvider())
    app = create_reader_local_app(agent, backend_timeout_seconds=300 if live else 20)
    client = ResearchClient(
        partner_url="http://127.0.0.1:8011/rpc", leader_id="reader-local-test-leader",
        identity_binding_enabled=False, transport=httpx.ASGITransport(app=app),
    )
    try:
        return await invoke_reader_aip(
            client, task, timeout_seconds=320 if live else 30,
            backend_mode="real" if live else "fixture",
        )
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Reader v0.2 official SDK AIP smoke test")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK,
                        help="JSON task, possibly with Leader-only requiredSkill/dependsOn")
    parser.add_argument("--live", action="store_true", help="Use actual PaperQA, PDF and LLM (may be paid)")
    args = parser.parse_args()
    try:
        payload = json.loads(args.task.read_text(encoding="utf-8"))
        task = business_task_from_planner(payload)
        if args.live and not task.input.papers[0].pdfUrl:
            print("LIVE_REQUIRES_VERIFIED_PDF_URL: provide an actual PDF URL in --task; no API call made")
            return 2
        if not args.live and task.input.papers[0].pdfUrl:
            print("OFFLINE_EXPECTS_NO_PDF_URL: use the supplied no-PDF example, or opt in to --live")
            return 2
        if args.live:
            load_dotenv(ROOT / ".env")
        outcome = asyncio.run(run(task, args.live))
    except Exception as exc:
        print("Reader AIP invocation/setup failed:", type(exc).__name__)
        return 1

    evidence_dir = ROOT / "outputs" / (
        "reader-aip-v02-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "business_task_v02.json").write_text(
        task.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (evidence_dir / "aip_outcome.json").write_text(
        json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("mode:", "LIVE_PAPERQA" if args.live else "OFFLINE_NO_PDF")
    print("AIP status:", outcome.status)
    print("protocolCompleted:", outcome.protocol_completed)
    print("businessValidated:", outcome.business_validated)
    print("researchSucceeded:", outcome.research_succeeded)
    print("warnings:", outcome.result.get("warnings") if outcome.result else [])
    print("Evidence:", evidence_dir)
    if outcome.error_code:
        print("errorCode:", outcome.error_code)
    # No-PDF is an expected negative business outcome, not a test failure.
    if not args.live:
        ok = (outcome.protocol_completed and outcome.business_validated
              and not outcome.research_succeeded and outcome.result is not None
              and "FULL_TEXT_UNAVAILABLE" in outcome.result.get("warnings", []))
    else:
        ok = outcome.research_succeeded
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
