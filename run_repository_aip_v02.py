"""Run Repository v0.2 through official ACPS SDK on in-process ASGI.

Default: fixture only, NO clone, NO CodeWiki, NO API charges.
Use --real --allow-git-clone only with a Python 3.12 / CodeWiki environment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

from aip.repository_v02_bridge import (
    business_task_from_planner, create_repository_local_app, invoke_repository_aip,
)
from aip.wu_delivery.structured import ResearchClient
from agents.repository.researchmesh_repository import RepositoryAgent


async def run(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parent
    task_path = Path(args.task) if args.task else root / "examples/repository_aip_fixture_v02.json"
    source = json.loads(task_path.read_text(encoding="utf-8-sig"))
    task = business_task_from_planner(source)
    mode = "real" if args.real else "fixture"
    if args.real and not args.allow_git_clone:
        raise SystemExit("Real execution requires BOTH --real and --allow-git-clone")

    # This backend context is deliberately scoped to the SDK invocation.
    with tempfile.TemporaryDirectory(prefix="researchmesh-repo-fixture-") as tmp:
        if args.real:
            # CodeWiki is an optional dependency, imported lazily by Zhang's provider.
            # LLM enrichment disabled to prevent accidental paid API calls.
            agent = RepositoryAgent(enrichment_enabled=False)
        else:
            from agents.repository.tests.test_repository_agent import FakeFetcher, FakeStaticProvider
            folder = Path(tmp)
            (folder / "README.md").write_text("# Offline fixture\n", encoding="utf-8")
            (folder / "pyproject.toml").write_text("[project]\nname='offline-fixture'\n",
                                                          encoding="utf-8")
            agent = RepositoryAgent(fetcher=FakeFetcher(folder),
                                    codewiki_provider=FakeStaticProvider(),
                                    enrichment_enabled=False)

        app = create_repository_local_app(agent, backend_mode=mode,
                                          backend_timeout_seconds=args.backend_timeout)
        client = ResearchClient(
            partner_url="http://127.0.0.1:8766/rpc",
            leader_id="local-repository-leader",
            identity_binding_enabled=False,
            transport=httpx.ASGITransport(app=app),
        )
        try:
            outcome = await invoke_repository_aip(
                client, task, timeout_seconds=args.aip_timeout, backend_mode=mode,
            )
        finally:
            await client.close()

    directory = root / "outputs" / ("repository-aip-v02-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "aip_outcome.json").write_text(
        json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "business_task_v02.json").write_text(
        json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    print("mode:", "LIVE_CODEWIKI_STATIC_NO_LLM" if args.real else "OFFLINE_FIXTURE_NO_REAL_RESEARCH")
    print("AIP status:", outcome.status)
    print("protocolCompleted:", outcome.protocol_completed)
    print("businessValidated:", outcome.business_validated)
    print("researchSucceeded:", outcome.research_succeeded)
    print("errorCode:", outcome.error_code)
    print("warnings:", (outcome.result or {}).get("warnings", []))
    print("Evidence:", directory)
    if not outcome.protocol_completed or not outcome.business_validated:
        raise SystemExit(1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", help="Planner task JSON, defaults to included fixture")
    p.add_argument("--real", action="store_true", help="Perform real repository static analysis")
    p.add_argument("--allow-git-clone", action="store_true", help="Explicitly allow network git clone")
    p.add_argument("--backend-timeout", type=float, default=120.0)
    p.add_argument("--aip-timeout", type=float, default=150.0)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
