"""Official local AIP Partner; isolated static-only Repository backend."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

from aip_business_partner import create_local_business_app, LocalBackendFailure
from aip_record_paths import require_record_directory
from local_process import stop_owned_process
from repository_adapter import to_repository_request
from repository_paper_context import prepare_repository_task

ROOT = Path(__file__).resolve().parent


def make_repository_executor(worker_call, expected_payload):
    prepare_repository_task(expected_payload)
    started, cached = False, None
    async def execute(payload):
        nonlocal started, cached
        if payload != expected_payload:
            raise LocalBackendFailure('INVALID_REQUEST')
        if cached is not None:
            return deepcopy(cached)
        if started:
            raise LocalBackendFailure('REPOSITORY_PREVIOUS_EXECUTION_UNCERTAIN')
        started = True
        journal = await worker_call()
        if journal.get('task') != expected_payload or journal.get('scope') != 'ISOLATED_CODEWIKI_STATIC_NO_LLM':
            raise LocalBackendFailure('REPOSITORY_WORKER_FAILED')
        if journal.get('phase') != 'completed' or journal.get('error') is not None:
            raise LocalBackendFailure((journal.get('error') or {}).get('code', 'REPOSITORY_WORKER_FAILED'))
        cached = deepcopy(journal['result'])
        return deepcopy(cached)
    return execute


def static_worker_env():
    # No .env load, and no inherited model credentials. Git itself may use the user's network proxy.
    env = {key: value for key, value in os.environ.items()
           if not any(word in key.upper() for word in ('API_KEY', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'SECRET', 'PASSWORD'))}
    env.update(PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               GIT_TERMINAL_PROMPT='0')
    return env


def create_live_app():
    output = require_record_directory(os.environ['RESEARCHMESH_AIP_RECORD_DIR'], ROOT, 'repository')
    payload = json.loads((output / 'repository_input.json').read_text(encoding='utf-8'))
    python = Path(os.environ['RESEARCHMESH_REPOSITORY_PYTHON']).resolve()
    wrapper = Path(os.environ['RESEARCHMESH_AIP_WRAPPER_ROOT']).resolve()
    async def worker():
        command = [str(python), '-X', 'utf8', str(ROOT / 'repository_aip_worker.py'),
                   '--wrapper-root', str(wrapper), '--record-dir', str(output), '--execute']
        with (output / 'repository_worker.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=static_worker_env(), stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                while process.poll() is None:
                    await asyncio.sleep(.1)
            finally:
                stop_owned_process(process)
        path = output / 'backend_outcome.json'
        if not path.is_file():
            raise LocalBackendFailure('REPOSITORY_WORKER_FAILED')
        return json.loads(path.read_text(encoding='utf-8'))
    return create_local_business_app(execute=make_repository_executor(worker, payload),
                    partner_aic=os.environ['RESEARCHMESH_LOCAL_PARTNER_AIC'],
                    background_execution=True, backend_timeout_seconds=180, mode='local-repository-live')
