"""Local official-AIP Reader S2 -> Repository S3. Default is a free preflight.

The reader input must already reference a verified PDF in outputs/pdf-cache.
--execute explicitly permits PaperQA and Repository DeepSeek requests. No retry.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import subprocess

from acps_sdk.aip import AipRpcClient
from dotenv import load_dotenv
import httpx

from aip_lifecycle import sdk_sender
from aip_orchestrator import AipOrchestrator
from planner_contract import validate_plan
from reader_adapter import to_reader_request
from resolver.local_aip import LocalAipResolver
from run_aip_literature import live_partner
from run_aip_repository import save_json


ROOT = Path(__file__).resolve().parent


def s2_s3_plan(reader_payload, repository_urls):
    reader = to_reader_request(reader_payload)
    if reader["subtaskId"] != "S2":
        raise ValueError("READER_SUBTASK_MUST_BE_S2")
    paper = reader["input"]["papers"][0]
    declared_urls = repository_urls or paper["repositoryUrls"]
    plan = {
        "schemaVersion": "0.2",
        "userQuery": f"Read {paper['url'] or paper['doi'] or paper['pdfUrl']} and inspect "
                     + ", ".join(declared_urls),
        "needsClarification": False, "clarificationQuestion": None,
        "tasks": [
            {**reader, "requiredSkill": "paper.analyze", "dependsOn": []},
            {"schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
             "requiredSkill": "repository.analyze", "dependsOn": ["S2"],
             "goal": "Compare the paper note with static repository evidence; do not claim reproduction",
             "input": {"papers": [], "paperNotes": [], "repositoryUrls": repository_urls},
             "constraints": {"maxRepositories": 1, "executeCode": False,
                             "allowRepositorySearch": False, "requirePaperNotes": True},
             "outputSchema": "code_records.v1"},
        ],
    }
    validate_plan(plan)
    return plan


async def run_plan(plan, reader_endpoint, repository_endpoint):
    reader_url, reader_aic = reader_endpoint
    repository_url, repository_aic = repository_endpoint
    reader_client = AipRpcClient(reader_url, "local-s2s3-reader-leader",
        identity_binding_enabled=False, transport=httpx.AsyncHTTPTransport(trust_env=False))
    repository_client = AipRpcClient(repository_url, "local-s2s3-repository-leader",
        identity_binding_enabled=False, transport=httpx.AsyncHTTPTransport(trust_env=False))
    try:
        resolver = LocalAipResolver({
            "paper.analyze": (reader_aic, sdk_sender(reader_client), 675, 1),
            "repository.analyze": (repository_aic, sdk_sender(repository_client), 180, .5),
        })
        return await AipOrchestrator(resolver, task_timeout_seconds=700).run_plan(plan)
    finally:
        await reader_client.close()
        await repository_client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader-input", type=Path, required=True)
    parser.add_argument("--reader-python", type=Path, required=True)
    parser.add_argument("--repository-python", type=Path, required=True)
    parser.add_argument("--reader-root", type=Path, default=ROOT / "agents/paper_reader")
    parser.add_argument("--reader-env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--reader-runtime-home", type=Path)
    parser.add_argument("--repository-url", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.reader_python.is_file() or not args.repository_python.is_file():
        raise ValueError("PARTNER_PYTHON_NOT_FOUND")
    source = json.loads(args.reader_input.read_text(encoding="utf-8"))
    plan = s2_s3_plan(source["payload"], args.repository_url)
    output = ROOT / "outputs" / datetime.now().strftime("aip-research-s2s3-%Y%m%d-%H%M%S-%f")
    reader_dir, repository_dir = output / "reader", output / "repository"
    reader_dir.mkdir(parents=True, exist_ok=False)
    repository_dir.mkdir()
    save_json(output / "plan.json", plan)
    save_json(reader_dir / "reader_input.json", source)
    worker = [str(args.reader_python.resolve()), "-X", "utf8", str(ROOT / "reader_aip_worker.py"),
              "--input", str(reader_dir / "reader_input.json"), "--reader-root", str(args.reader_root.resolve()),
              "--record-dir", str(reader_dir), "--env-file", str(args.reader_env_file.resolve())]
    reader_env = {"RESEARCHMESH_READER_PYTHON": str(args.reader_python.resolve()),
                  "RESEARCHMESH_READER_ROOT": str(args.reader_root.resolve()),
                  "RESEARCHMESH_READER_ENV_FILE": str(args.reader_env_file.resolve())}
    if args.reader_runtime_home:
        cache = str(args.reader_runtime_home.resolve())
        worker += ["--runtime-home", cache]
        reader_env["RESEARCHMESH_READER_RUNTIME_HOME"] = cache
    with (reader_dir / "preflight.log").open("w", encoding="utf-8") as log:
        preflight = subprocess.run(worker, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=90, check=False)
    if preflight.returncode:
        print("Reader free preflight failed; record:", output)
        return 1
    if not args.execute:
        print("S2->S3 free preflight PASS; no AIP task or model call. Record:", output)
        return 0
    load_dotenv(args.reader_env_file.resolve(), override=False)
    with live_partner(args.reader_root.resolve(), reader_dir,
                      factory="aip_reader_partner:create_live_app", mode="local-reader-live",
                      extra_env=reader_env, aic_prefix="local-reader-") as reader_endpoint:
        with live_partner(ROOT / "agents/repository", repository_dir,
                          factory="aip_repository_partner:create_live_app", mode="local-repository-live",
                          python_executable=args.repository_python.resolve(),
                          aic_prefix="local-repository-") as repository_endpoint:
            result = asyncio.run(run_plan(plan, reader_endpoint, repository_endpoint))
    save_json(output / "leader_result.json", result)
    succeeded = [record["succeeded"] for record in result["executionRecords"]]
    print("S2->S3", "PASS" if succeeded == [True, True] else "FAIL", "record:", output)
    return 0 if succeeded == [True, True] else 1


if __name__ == "__main__":
    raise SystemExit(main())
