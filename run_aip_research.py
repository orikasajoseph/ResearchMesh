"""One local AIP search/read entry. No external work without --execute.

Supports exactly one independent search followed by one single-paper Reader.
No ADP, platform identity, repository execution or automatic paid stage retry.
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager

from aip_reader_inputs import read_json, prepare_reader_input
from assemble_aip_evidence import assemble, render
from local_process import stop_owned_process
from planner_contract import validate_plan
from run_aip_business import save_json
from run_aip_literature import select_search_task

ROOT = Path(__file__).resolve().parent


@contextmanager
def checkpoint_lock(output):
    """Fail closed if another process is using this checkpoint, including a stale lock."""
    path = output / "pipeline.lock"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def pair(plan):
    validate_plan(plan)
    if plan["needsClarification"]:
        raise ValueError("PLAN_NEEDS_CLARIFICATION")
    if len(plan["tasks"]) != 2:
        raise ValueError("ONLY_SEARCH_THEN_SINGLE_READER_SUPPORTED")
    search, reader = plan["tasks"]
    select_search_task(plan, search["subtaskId"])
    if (reader["taskType"] != "paper_read" or reader["dependsOn"] != [search["subtaskId"]]
            or reader["constraints"].get("maxPapers") != 1 or reader["input"].get("papers")):
        raise ValueError("SINGLE_DEPENDENT_READER_REQUIRED")
    return search["subtaskId"], reader["subtaskId"]


def persist(output, state):
    temporary = output / "pipeline.json.tmp"
    save_json(temporary, state)
    temporary.replace(output / "pipeline.json")


def run_command(command, log, timeout):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            return process.wait(timeout=timeout)
        finally:
            stop_owned_process(process)


def stage(output, state, name, command, *, timeout, runner=run_command):
    previous = state["stages"].get(name)
    if previous:
        if previous["state"] == "completed":
            return
        # An interrupted paid call may have finished remotely. Never blindly replay.
        raise ValueError("STAGE_NOT_SAFE_TO_REPLAY_" + name.upper())
    state["stages"][name] = {"state": "running", "startedAt": datetime.now().astimezone().isoformat()}
    persist(output, state)
    print("正在执行：", name, "；详细进度见记录目录对应日志。", flush=True)
    code = runner(command, output / (name + ".log"), timeout)
    state["stages"][name].update(state="completed" if code == 0 else "failed", exitCode=code,
        finishedAt=datetime.now().astimezone().isoformat())
    persist(output, state)
    if code:
        raise ValueError("STAGE_FAILED_" + name.upper())


def execute_pipeline(output, state, args, *, runner=run_command, planner=None):
    plan_file = output / "plan.json"
    if not plan_file.is_file():
        if not state.get("query"):
            raise ValueError("MISSING_SAVED_PLAN")
        if state["stages"].get("planner"):
            raise ValueError("PLANNER_NOT_SAFE_TO_REPLAY")
        state["stages"]["planner"] = {"state": "running"}
        persist(output, state)
        if planner is None:
            from run_planner import create_plan
            planner = create_plan
        print("正在生成真实计划；会消耗模型额度。", flush=True)
        plan, metadata = asyncio.run(planner(state["query"], llm_adapter=args.llm_adapter))
        save_json(plan_file, plan)
        save_json(output / "planner_metadata.json", metadata)
        state["stages"]["planner"] = {"state": "completed"}
        persist(output, state)
    plan = read_json(plan_file)
    digest = hashlib.sha256(plan_file.read_bytes()).hexdigest()
    if state.get("planSha256") and state["planSha256"] != digest:
        raise ValueError("PLAN_CHECKPOINT_CHANGED")
    state["planSha256"] = digest
    search_id, reader_id = pair(plan)  # reject extra tasks before search/model reading
    persist(output, state)
    py = [sys.executable, "-X", "utf8"]
    search = output / "literature"
    stage(output, state, "literature", py + [str(ROOT / "run_aip_literature.py"), "--plan", str(plan_file),
        "--subtask", search_id, "--wrapper-root", str(args.wrapper_root.resolve()),
        "--record-dir", str(search), "--execute"], timeout=190, runner=runner)
    proof = output / "pdf_resolution.json"
    if args.pdf_proof:
        # Supplied proof is reused or fails closed. No surprise fallback downloads.
        prepare_reader_input(search, args.pdf_proof, cache_dir=ROOT / "outputs/pdf-cache", subtask_id=reader_id)
        if proof.exists() and read_json(proof) != read_json(args.pdf_proof):
            suffix = hashlib.sha256(args.pdf_proof.read_bytes()).hexdigest()[:16]
            proof = output / ("provided-pdf-" + suffix + ".json")
        if not proof.exists():
            save_json(proof, read_json(args.pdf_proof))
    else:
        stage(output, state, "pdf", [str(args.reader_python.resolve()), "-X", "utf8",
            str(ROOT / "aip_pdf_worker.py"), "--source", str(search), "--output", str(proof),
            "--cache", str(ROOT / "outputs/pdf-cache"), "--search-id", search_id,
            "--reader-id", reader_id, "--execute"], timeout=240, runner=runner)
    prepare_reader_input(search, proof, cache_dir=ROOT / "outputs/pdf-cache", subtask_id=reader_id)
    state["pdfProofPath"] = str(proof)
    persist(output, state)
    reader = output / "reader"
    stage(output, state, "reader", py + [str(ROOT / "run_aip_reader.py"), "--source", str(search),
        "--pdf-proof", str(proof), "--subtask", reader_id, "--reader-root", str(args.reader_root.resolve()),
        "--reader-python", str(args.reader_python.resolve()), "--record-dir", str(reader), "--execute"],
        timeout=780, runner=runner)
    record = assemble(search, reader, cache_dir=ROOT / "outputs/pdf-cache")
    # Keep assembly's historical provenance; add a separate pipeline-level claim.
    record["pipeline"] = {"scope": "LOCAL_AIP_SINGLE_ENTRY_SEARCH_READER", "singleEntryCompleted": True,
        "plannerExecutedByPipeline": "planner" in state["stages"],
        "pdfResolutionExecutedByPipeline": "pdf" in state["stages"],
        "paidStageRequestCounts": None,
        "billingNote": "Assembly newCalls=0 means assembly does not replay tasks; preceding pipeline stages may be charged.",
        "platformDiscoveryUsed": False, "tlsIdentityVerified": False, "repositoryExecuted": False}
    save_json(output / "research_run.json", record)
    (output / "report.md").write_text("# 单入口本地科研流程\n\n本流程无 ADP/梧桐认证；下文复核各阶段记录。\n\n" + render(record), encoding="utf-8")
    state.update(state="completed", finishedAt=datetime.now().astimezone().isoformat())
    persist(output, state)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", type=Path)
    modes.add_argument("--query")
    modes.add_argument("--resume", type=Path)
    parser.add_argument("--reader-root", type=Path)
    parser.add_argument("--reader-python", type=Path)
    parser.add_argument("--wrapper-root", type=Path, default=ROOT / "agents/literature")
    parser.add_argument("--pdf-proof", type=Path)
    parser.add_argument("--llm-adapter", choices=["existing-async", "team"], default="existing-async")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        if args.plan:
            try:
                pair(read_json(args.plan))
            except (ValueError, OSError):
                print("计划不适用于当前单篇链路；未调用 API。")
                return 1
        print("未执行：不调用模型、论文 API、PDF、AIP 或平台。--plan 可离线检查范围。")
        print("仅支持检索→阅读1篇；--execute 会计费，Reader可能多次请求模型。")
        return 0
    if not args.reader_root or not args.reader_python or not args.reader_python.is_file():
        print("先指定 --reader-root 和 --reader-python；未调用 API。")
        return 1
    output = None
    try:
        if args.resume:
            output = args.resume.resolve()
            state = read_json(output / "pipeline.json")
            if state.get("scope") != "LOCAL_AIP_SINGLE_ENTRY_V1":
                raise ValueError("INVALID_PIPELINE_CHECKPOINT")
        else:
            # Ask once in the terminal, never ask the user to type JSON.
            query = None if args.plan else (args.query or input("请输入研究问题（检索后阅读1篇）：").strip())
            if not args.plan and not query:
                raise ValueError("EMPTY_QUERY")
            if args.plan:
                pair(read_json(args.plan))
            output = ROOT / "outputs" / datetime.now().strftime("aip-research-%Y%m%d-%H%M%S-%f")
            output.mkdir(parents=True, exist_ok=False)
            state = {"scope": "LOCAL_AIP_SINGLE_ENTRY_V1", "state": "running", "query": query,
                     "stages": {}, "createdAt": datetime.now().astimezone().isoformat()}
            if args.plan:
                save_json(output / "plan.json", read_json(args.plan))
            persist(output, state)
        print("【单入口本地 AIP 检索→阅读】会计费；不使用 ADP/梧桐，不自动重试失败阶段。", flush=True)
        print("记录：", output, flush=True)
        with checkpoint_lock(output):
            # Reload after acquiring the lock, not before waiting on another run.
            state = read_json(output / "pipeline.json")
            execute_pipeline(output, state, args)
        print("本地链完成，请查看 report.md；科研结论仍需人工核验。")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        if output and output.is_dir():
            # Unknown errors never include raw provider responses or credentials.
            save_json(output / "pipeline_failure.json", {"code": type(error).__name__,
                "automaticPaidRetries": 0, "remoteExecutionMayHaveOccurred": True})
        print("流程停止：", type(error).__name__, "；查看阶段日志。已完成结果保留，不自动重跑付费步骤。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
