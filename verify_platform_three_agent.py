"""Offline acceptance for the formal three-Agent orchestration core."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from platform_three_agent_orchestrator import execute_platform_chain


ROOT = Path(__file__).resolve().parent
PDF = "https://arxiv.org/pdf/2106.09685"
REPOSITORY = "https://github.com/microsoft/LoRA"


def paper():
    return {"paperId": "arxiv:2106.09685", "title": "LoRA: Low-Rank Adaptation of Large Language Models",
            "doi": None, "url": "https://arxiv.org/abs/2106.09685", "pdfUrl": PDF,
            "repositoryUrls": [REPOSITORY]}


def stage_result(task):
    common = {"schemaVersion": "0.2", "subtaskId": task["subtaskId"],
              "taskType": task["taskType"], "warnings": []}
    if task["taskType"] == "literature_search":
        return {**common, "summary": "Offline Literature contract fixture",
                "items": [{"kind": "paper", "data": paper()}]}
    if task["taskType"] == "paper_read":
        return {**common, "summary": "Offline Reader contract fixture",
                "items": [{"kind": "paper_note", "data": {
                    "source": deepcopy(task["input"]["papers"][0]), "method": "Low-rank adapters",
                    "findings": "Reduced trainable parameters", "limitations": "Selected tasks only",
                    "analysisLevel": "fulltext", "evidence": [{"sourceId": "arxiv:2106.09685",
                        "url": PDF, "locator": "page 3", "supports": "Base weights are frozen."}],
                }}]}
    return {**common, "summary": "Offline Repository contract fixture",
            "items": [{"kind": "repository", "data": {
                "url": REPOSITORY, "license": "UNKNOWN", "isMock": False,
                "relationEvidence": "Repository URL propagated from the selected paper source.",
                "evidence": [{"sourceId": "E1", "url": REPOSITORY + "/blob/" + "a" * 40 + "/README.md#L1",
                              "locator": "README.md:L1", "supports": "README describes the project."}],
            }}]}


def accepted_record(stage, task):
    return {"scope": "OFFLINE_FORMAL_CONTRACT_FIXTURE", "task": deepcopy(task),
            "result": stage_result(task), "passed": True, "protocolCompleted": True,
            "businessReady": True, "resultValidated": True, "cleanupSucceeded": True,
            "remoteStateUnknown": False, "error": None, "groupId": "group-" + stage,
            "sessionId": "session-" + stage, "taskId": "task-" + stage,
            "partnerAic": "aic-" + stage}


class OfflineRunner:
    def __init__(self):
        self.calls = []

    async def __call__(self, stage, task):
        self.calls.append(stage)
        return accepted_record(stage, deepcopy(task))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path,
                        default=ROOT / "examples/platform_three_agent_chain_v02.json")
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    output = ROOT / "outputs/platform-three-agent-offline" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    runner = OfflineRunner()
    first = asyncio.run(execute_platform_chain(plan, output, runner))
    second = asyncio.run(execute_platform_chain(plan, output, runner))
    checks = {
        "completed": first["state"] == "completed",
        "dynamicS1ToS2ToS3": [row["stage"] for row in first["stages"]]
            == ["literature_search", "paper_read", "repository_analyze"],
        "distinctGroups": len({row["groupId"] for row in first["stages"]}) == 3,
        "distinctTasks": len({row["taskId"] for row in first["stages"]}) == 3,
        "noAutomaticRetry": first["automaticRetries"] == 0,
        "resumeSkipsCompleted": len(runner.calls) == 3 and second["state"] == "completed",
        "truthfulOfflineScope": first["formalPlatformExecuted"] is False,
    }
    acceptance = {"passed": all(checks.values()),
                  "scope": "OFFLINE_FORMAL_ORCHESTRATION_CORE_FIXTURES_NO_NETWORK_NO_MODEL",
                  "checks": checks, "recordDirectory": str(output)}
    (output / "acceptance.json").write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(acceptance, ensure_ascii=False, indent=2))
    return 0 if acceptance["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
