"""One local AIP Repository Partner, explicit static/enhanced isolated backends.

Both are real business modes, not fake-vs-production protocols. Static is the
safe default; enhanced requires explicit configuration and may incur charges.
Platform ACS/ADP/AMQP integration must adapt this business seam, not the CLI.
"""
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


def make_repository_executor(worker_call, expected_payload=None, *, analysis_mode='static', prepare_task=None):
    if analysis_mode not in {'static', 'enhanced'}:
        raise ValueError('INVALID_REPOSITORY_ANALYSIS_MODE')
    if expected_payload is not None:
        prepare_repository_task(expected_payload)
    started, cached, active_task = False, None, None
    async def execute(payload):
        nonlocal started, cached, active_task
        prepare_repository_task(payload)
        if expected_payload is not None and payload != expected_payload:
            raise LocalBackendFailure('INVALID_REQUEST')
        if active_task is not None and payload != active_task:
            raise LocalBackendFailure('REPOSITORY_PREVIOUS_EXECUTION_UNCERTAIN')
        if cached is not None:
            return deepcopy(cached)
        if started:
            raise LocalBackendFailure('REPOSITORY_PREVIOUS_EXECUTION_UNCERTAIN')
        started = True
        active_task = deepcopy(payload)
        if prepare_task is not None:
            await prepare_task(deepcopy(active_task))
        journal = await worker_call()
        scope = ('ISOLATED_CODEWIKI_STATIC_NO_LLM' if analysis_mode == 'static'
                 else 'ISOLATED_CODEWIKI_WITH_EXPLICIT_LLM_ENHANCEMENT')
        if journal.get('task') != active_task or journal.get('scope') != scope:
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


def repository_worker_env(analysis_mode):
    """Keep static env credential-free; enhanced env gets only named LLM settings."""
    if analysis_mode not in {'static', 'enhanced'}:
        raise ValueError('INVALID_REPOSITORY_ANALYSIS_MODE')
    env = static_worker_env()
    if analysis_mode == 'enhanced':
        for key in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL'):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def create_repository_business_executor(*, record_dir, repository_python, wrapper_root,
                                       analysis_mode='static', dynamic=True, env_file=None):
    """One guarded per-task backend; no clone, model or protocol at construction."""
    if type(dynamic) is not bool:
        raise ValueError('INVALID_REPOSITORY_DYNAMIC_MODE')
    environment = repository_worker_env(analysis_mode)
    output = require_record_directory(record_dir, ROOT, 'repository')
    input_path = output / 'repository_input.json'
    payload = None if dynamic else json.loads(input_path.read_text(encoding='utf-8'))
    python = Path(repository_python).resolve()
    wrapper = Path(wrapper_root).resolve()
    if analysis_mode == 'static' and env_file:
        raise ValueError('ENV_FILE_REQUIRES_ENHANCED_MODE')
    async def worker():
        script = 'repository_aip_worker.py' if analysis_mode == 'static' else 'repository_enhanced_worker.py'
        command = [str(python), '-X', 'utf8', str(ROOT / script),
                   '--wrapper-root', str(wrapper), '--record-dir', str(output), '--execute']
        if analysis_mode == 'enhanced' and env_file:
            command += ['--env-file', str(Path(env_file).resolve())]
        with (output / 'repository_worker.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
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
    async def prepare_task(task):
        with input_path.open('x', encoding='utf-8') as stream:
            json.dump(task, stream, ensure_ascii=False, indent=2)
    return make_repository_executor(worker, payload, analysis_mode=analysis_mode,
                                    prepare_task=prepare_task if dynamic else None)


def create_live_app():
    analysis_mode = os.environ.get('RESEARCHMESH_REPOSITORY_ANALYSIS_MODE', 'static')
    output = require_record_directory(os.environ['RESEARCHMESH_AIP_RECORD_DIR'], ROOT, 'repository')
    executor = create_repository_business_executor(
        record_dir=output,
        repository_python=os.environ['RESEARCHMESH_REPOSITORY_PYTHON'],
        wrapper_root=os.environ['RESEARCHMESH_AIP_WRAPPER_ROOT'], analysis_mode=analysis_mode,
        dynamic=os.environ.get('RESEARCHMESH_REPOSITORY_DYNAMIC_INPUT') == '1',
        env_file=os.environ.get('RESEARCHMESH_REPOSITORY_ENV_FILE') or None)
    return create_local_business_app(execute=executor,
                    partner_aic=os.environ['RESEARCHMESH_LOCAL_PARTNER_AIC'],
                    background_execution=True, backend_timeout_seconds=180 if analysis_mode == 'static' else 300,
                    mode='local-repository-live')
