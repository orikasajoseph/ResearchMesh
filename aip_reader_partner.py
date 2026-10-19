"""Official AIP Partner in project .venv; Reader runs in its existing runtime."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

from aip_business_partner import create_local_business_app, LocalBackendFailure
from reader_adapter import normalize_reader_result, to_reader_request
from local_process import stop_owned_process
from aip_record_paths import require_record_directory

ROOT = Path(__file__).resolve().parent


def make_reader_executor(worker_call, expected_task=None, *, prepare_task=None):
    started, cached, active_task = False, None, None
    async def execute(payload):
        nonlocal started, cached, active_task
        task = to_reader_request(payload)
        if expected_task is not None and task != expected_task:
            raise LocalBackendFailure("INVALID_REQUEST")
        if active_task is not None and task != active_task:
            raise LocalBackendFailure("READER_PREVIOUS_EXECUTION_UNCERTAIN")
        if cached is not None:
            return deepcopy(cached)
        if started:
            raise LocalBackendFailure("READER_PREVIOUS_EXECUTION_UNCERTAIN")
        started = True  # never repeat a paid worker for another start/taskId
        active_task = deepcopy(task)
        if prepare_task is not None:
            await prepare_task(task)
        journal = await worker_call()
        if journal.get("phase") != "completed" or journal.get("task") != task:
            code = ((journal.get("outcome") or {}).get("error") or {}).get("code", "READER_WORKER_FAILED")
            raise LocalBackendFailure(code)
        if not isinstance(journal.get("outcome"), dict) or journal["outcome"].get("error") is not None:
            raise LocalBackendFailure("INVALID_READER_RESULT")
        normalized = normalize_reader_result(task, journal["outcome"]["result"])
        if normalized["error"] or normalized["diagnostics"].get("analysisLevel") != "fulltext":
            raise LocalBackendFailure("INVALID_READER_RESULT")
        cached = deepcopy(normalized["result"])
        return deepcopy(cached)
    return execute


def create_live_app():
    try:
        output = require_record_directory(os.environ["RESEARCHMESH_AIP_RECORD_DIR"], ROOT, "reader")
    except ValueError:
        raise ValueError("INVALID_READER_RECORD_DIRECTORY") from None
    input_path = output / "reader_input.json"
    dynamic = os.environ.get("RESEARCHMESH_READER_DYNAMIC_INPUT") == "1"
    expected_task = None if dynamic else to_reader_request(json.loads(input_path.read_text(encoding="utf-8"))["payload"])
    python = Path(os.environ["RESEARCHMESH_READER_PYTHON"]).resolve()
    root = Path(os.environ["RESEARCHMESH_READER_ROOT"]).resolve()
    env_file = Path(os.environ.get("RESEARCHMESH_READER_ENV_FILE", ROOT / ".env")).resolve()
    runtime_home = os.environ.get("RESEARCHMESH_READER_RUNTIME_HOME")
    resume = os.environ.get("RESEARCHMESH_READER_RESUME_JOURNAL")
    async def call_worker():
        args = [str(python), "-X", "utf8", str(ROOT / "reader_aip_worker.py"), "--input", str(input_path),
                "--reader-root", str(root), "--record-dir", str(output),
                "--env-file", str(env_file), "--execute"]
        if runtime_home:
            args.extend(["--runtime-home", str(Path(runtime_home).resolve())])
        if resume:
            args.extend(["--resume-journal", resume])
        if dynamic:
            args.extend(["--bound-task", str(output / "bound_task.json")])
        # Popen polling avoids Windows selector-loop subprocess incompatibilities.
        with (output / "reader_worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(args, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                while process.poll() is None:
                    await asyncio.sleep(0.25)
            finally:
                stop_owned_process(process)
        journal_path = output / "reader_journal.json"
        if not journal_path.is_file():
            failure_path = output / "worker_failure.json"
            if failure_path.is_file():
                try:
                    code = json.loads(failure_path.read_text(encoding="utf-8"))["error"]
                    if isinstance(code, str) and code.isupper() and len(code) <= 80:
                        raise LocalBackendFailure(code)
                except (ValueError, KeyError, TypeError, OSError):
                    pass
            raise LocalBackendFailure("READER_WORKER_FAILED")
        return json.loads(journal_path.read_text(encoding="utf-8"))
    async def prepare_task(task):
        # PDF parsing and pypdf run in the existing Reader Python, not Planner's venv.
        with (output / "bound_task.json").open("x", encoding="utf-8") as stream:
            json.dump(task, stream, ensure_ascii=False)

    return create_local_business_app(execute=make_reader_executor(
                                        call_worker, expected_task,
                                        prepare_task=prepare_task if dynamic else None),
                                    partner_aic=os.environ["RESEARCHMESH_LOCAL_PARTNER_AIC"],
                                    background_execution=True, backend_timeout_seconds=660, mode="local-reader-live")
