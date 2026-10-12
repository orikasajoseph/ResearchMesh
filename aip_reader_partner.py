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

ROOT = Path(__file__).resolve().parent


def make_reader_executor(worker_call, expected_task):
    started, cached = False, None
    async def execute(payload):
        nonlocal started, cached
        if to_reader_request(payload) != expected_task:
            raise LocalBackendFailure("INVALID_REQUEST")
        if cached is not None:
            return deepcopy(cached)
        if started:
            raise LocalBackendFailure("READER_PREVIOUS_EXECUTION_UNCERTAIN")
        started = True  # never repeat a paid worker for another start/taskId
        journal = await worker_call()
        if journal.get("phase") != "completed" or journal.get("task") != expected_task:
            code = ((journal.get("outcome") or {}).get("error") or {}).get("code", "READER_WORKER_FAILED")
            raise LocalBackendFailure(code)
        if not isinstance(journal.get("outcome"), dict) or journal["outcome"].get("error") is not None:
            raise LocalBackendFailure("INVALID_READER_RESULT")
        normalized = normalize_reader_result(expected_task, journal["outcome"]["result"])
        if normalized["error"] or normalized["diagnostics"].get("analysisLevel") != "fulltext":
            raise LocalBackendFailure("INVALID_READER_RESULT")
        cached = deepcopy(normalized["result"])
        return deepcopy(cached)
    return execute


def create_live_app():
    output = Path(os.environ["RESEARCHMESH_AIP_RECORD_DIR"]).resolve()
    if not output.is_dir() or output.parent != ROOT / "outputs":
        raise ValueError("INVALID_READER_RECORD_DIRECTORY")
    input_path = output / "reader_input.json"
    expected_task = to_reader_request(json.loads(input_path.read_text(encoding="utf-8"))["payload"])
    python = Path(os.environ["RESEARCHMESH_READER_PYTHON"]).resolve()
    root = Path(os.environ["RESEARCHMESH_READER_ROOT"]).resolve()
    resume = os.environ.get("RESEARCHMESH_READER_RESUME_JOURNAL")
    async def call_worker():
        args = [str(python), "-X", "utf8", str(ROOT / "reader_aip_worker.py"), "--input", str(input_path),
                "--reader-root", str(root), "--record-dir", str(output), "--execute"]
        if resume:
            args.extend(["--resume-journal", resume])
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
            raise LocalBackendFailure("READER_WORKER_FAILED")
        return json.loads(journal_path.read_text(encoding="utf-8"))
    return create_local_business_app(execute=make_reader_executor(call_worker, expected_task),
                                    partner_aic=os.environ["RESEARCHMESH_LOCAL_PARTNER_AIC"],
                                    background_execution=True, backend_timeout_seconds=660, mode="local-reader-live")
