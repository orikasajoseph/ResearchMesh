"""One local S3 official-AIP integration run; no ADP registration or S2 chain."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from acps_sdk.aip import AipRpcClient, StructuredDataItem, TaskCommand, TaskCommandType
from aip_lifecycle import sdk_sender
from aip_orchestrator import AipOrchestrator
from dotenv import load_dotenv
import httpx
from resolver.local_aip import LocalAipResolver
from run_aip_literature import live_partner


ROOT = Path(__file__).resolve().parent
PLANNER_COMMIT = "3ba828e0755f91e789a829c42f7d5df5f4045034"
REPOSITORY_COMMIT = "be99b31b3c8411ea88a96d08b0337d6049882046"


def repository_plan(urls):
    return {
        "schemaVersion": "0.2", "userQuery": "Analyze the explicitly provided repository",
        "needsClarification": False, "clarificationQuestion": None,
        "tasks": [{
            "schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
            "requiredSkill": "repository.analyze", "dependsOn": [],
            "goal": "Analyze repository structure, dependencies, entrypoints and reproduction barriers",
            "input": {"repositoryUrls": urls},
            "constraints": {"maxRepositories": 1, "executeCode": False, "allowRepositorySearch": False},
            "outputSchema": "code_records.v1",
        }],
    }


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_event(output, event, **details):
    value = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **details}
    with (output / "leader_events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


async def execute_s3(url, aic, output):
    client = AipRpcClient(url, "local-repository-leader", identity_binding_enabled=False,
                          transport=httpx.AsyncHTTPTransport(trust_env=False))
    try:
        base_send = sdk_sender(client)
        async def traced_send(command):
            if command.command == TaskCommandType.Start:
                record_event(output, "AIP_TASK_COMMAND_SENT", taskId=command.taskId)
            response = await base_send(command)
            if response.status.state.value == "awaiting-completion":
                record_event(output, "AIP_TASK_RESULT_RECEIVED", taskId=command.taskId)
            if command.command == TaskCommandType.Complete and response.status.state.value == "completed":
                record_event(output, "AIP_COMPLETE_CONFIRMED", taskId=command.taskId)
            return response
        resolver = LocalAipResolver({"repository.analyze": (aic, traced_send, 180, 0.5)},
                                    on_event=lambda name, **details: record_event(output, name, **details))
        record_event(output, "PLANNER_SUBTASK_CREATED", subtaskId="S3")
        result = await AipOrchestrator(resolver, task_timeout_seconds=180).run_plan(
            repository_plan(["https://github.com/pypa/sampleproject"]))
        if result["executionRecords"][0]["succeeded"]:
            record_event(output, "LEADER_RESULT_VALIDATED", subtaskId="S3")
        return result
    finally:
        await client.close()


async def execute_empty_failure(url, aic):
    """Planner preflight blocks empty URLs; independently verify Partner's official AIP rejection."""
    from planner_contract import validate_plan

    try:
        validate_plan(repository_plan([]))
    except ValueError:
        leader_preflight = "INPUT_INSUFFICIENT"
    else:
        leader_preflight = "UNEXPECTED_ACCEPTANCE"
    client = AipRpcClient(url, "local-repository-leader", identity_binding_enabled=False,
                          transport=httpx.AsyncHTTPTransport(trust_env=False))
    try:
        payload = {key: value for key, value in repository_plan([])["tasks"][0].items()
                   if key not in {"requiredSkill", "dependsOn"}}
        response = await sdk_sender(client)(TaskCommand(
            id=str(uuid4()), sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader", senderId="local-repository-leader",
            sessionId=str(uuid4()), taskId="task-" + str(uuid4()),
            command=TaskCommandType.Start,
            dataItems=[StructuredDataItem(data=payload)],
        ))
        status = response.status
        codes = [item.text for item in status.dataItems or [] if hasattr(item, "text")]
        return {"leaderPreflight": leader_preflight, "partnerState": status.state.value,
                "partnerCodes": codes, "taskResultReceived": True}
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partner-python", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--record-dir", type=Path)
    args = parser.parse_args()
    output = args.record_dir or ROOT / "outputs" / datetime.now().strftime("aip-repository-%Y%m%d-%H%M%S-%f")
    output = output.resolve()
    if output.exists() or ROOT not in output.parents:
        raise ValueError("RECORD_DIR_MUST_BE_NEW_UNDER_PROJECT")
    output.mkdir(parents=True)
    if args.env_file:
        load_dotenv(args.env_file.resolve(), override=False)
    if not args.partner_python.is_file():
        raise ValueError("PARTNER_PYTHON_NOT_FOUND")
    with live_partner(ROOT / "agents/repository", output,
                      factory="aip_repository_partner:create_live_app", mode="local-repository-live",
                      python_executable=args.partner_python.resolve(),
                      aic_prefix="local-repository-") as (url, aic):
        success = asyncio.run(execute_s3(url, aic, output))
        save_json(output / "leader_result.json", success)
        if not success["executionRecords"][0]["succeeded"]:
            print("S3 FAILED", success["executionRecords"][0]["error"], "record:", output)
            return 1
        failure = asyncio.run(execute_empty_failure(url, aic))
        save_json(output / "empty_url_failure.json", failure)
    record = success["executionRecords"][0]
    execution = record["aipReceipt"]["execution"]
    data = success["results"][0]["items"][0]["data"]
    protocol_events = execution["events"]
    partner_events_path = output / "partner_events.jsonl"
    partner_events = [json.loads(line) for line in partner_events_path.read_text(encoding="utf-8").splitlines()]
    leader_events = [json.loads(line) for line in (output / "leader_events.jsonl").read_text(encoding="utf-8").splitlines()]
    trace = {
        "plannerCommit": PLANNER_COMMIT, "repositoryCommit": REPOSITORY_COMMIT,
        "subtaskId": "S3", "taskType": "repository_analyze", "skill": "repository.analyze",
        "aipTaskId": execution["taskId"], "agent": aic, "status": execution["state"],
        "warnings": success["results"][0]["warnings"], "latencyMs": execution["latencyMs"],
        "counts": data["diagnostics"]["counts"],
        "events": sorted([*leader_events, *partner_events], key=lambda event: event["at"]),
        "protocolEvents": protocol_events,
        "partnerEvents": partner_events,
        "failurePath": failure,
    }
    save_json(output / "trace.json", trace)
    passed = (record["succeeded"] and execution["state"] == "completed"
              and any(event["command"] == "complete" and event["state"] == "completed"
                      for event in protocol_events)
              and failure["leaderPreflight"] == "INPUT_INSUFFICIENT"
              and failure["partnerState"] == "rejected"
              and "REPOSITORY_UNAVAILABLE" in failure["partnerCodes"])
    print("S3", "PASS" if passed else "FAIL", "record:", output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
