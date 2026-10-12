from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.repository.researchmesh_repository import RepositoryAgent, RepositoryEnricher
from common.llm.researchmesh_llm import LLMProvider


PUBLIC_REPOSITORY = "https://github.com/pypa/sampleproject.git"


def request(subtask_id: str, url: str) -> dict:
    return {
        "schemaVersion": "0.2",
        "subtaskId": subtask_id,
        "taskType": "repository_analyze",
        "goal": "Statically analyze the supplied repository without executing it",
        "input": {"repositoryUrls": [url]},
        "constraints": {"maxRepositories": 1, "executeCode": False},
        "outputSchema": "code_records.v1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repository Agent v0.1 live test")
    parser.add_argument("--tiny-repo", type=Path, required=True)
    parser.add_argument("--public-url", default=PUBLIC_REPOSITORY)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    shared_provider = LLMProvider()
    agent = RepositoryAgent(enricher=RepositoryEnricher(shared_provider))
    tiny = agent.analyze_local(
        request("live-tiny", "https://local.invalid/tiny-repo.git"),
        args.tiny_repo,
        repository_url="https://local.invalid/tiny-repo.git",
    )
    public = agent.analyze(request("live-public", args.public_url))
    payload = {
        "tiny": tiny.model_dump(mode="json"),
        "public": public.model_dump(mode="json"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(_summary(payload), ensure_ascii=False, indent=2))
    return 0 if tiny.items and public.items else 1


def _summary(payload: dict) -> dict:
    output = {}
    for name, result in payload.items():
        if not result["items"]:
            output[name] = {"passed": False, "warnings": result["warnings"]}
            continue
        diagnostics = result["items"][0]["data"]["diagnostics"]
        output[name] = {
            "passed": True,
            "metrics": diagnostics["metrics"],
            "counts": diagnostics["counts"],
            "warnings": result["warnings"],
        }
    return output


if __name__ == "__main__":
    raise SystemExit(main())
