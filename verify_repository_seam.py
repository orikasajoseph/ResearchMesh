"""Explicit offline real Zhang Wrapper + synthetic providers + official SDK.

No CodeWiki installation, clone, model, API, PDF, ADP or MQ. Does not claim A4.
"""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from acps_sdk.aip import AipRpcClient
from aip_business_partner import create_local_business_app
from aip_lifecycle import AipTaskRunner, sdk_sender
from common.models import BUSINESS_TASK_FIELDS
from planner_contract import validate_plan
from repository_adapter import load_repository, to_repository_request, normalize_repository_result
from run_aip_business import save_json

ROOT = Path(__file__).resolve().parent


def fixture_agent(wrapper, folder, calls):
    import importlib
    fetcher = importlib.import_module(wrapper.__name__ + '.fetcher')
    class Fetcher:
        @contextmanager
        def fetch(self, url):
            calls.append('fake-fetch-only')
            yield fetcher.FetchedRepository(folder, url, 'a' * 40, 0)
    class Static:
        def analyze(self, path):
            calls.append('fake-static-only')
            return wrapper.CodeWikiStaticResult(components={
                'main.py::main': {'name': 'main', 'component_type': 'function', 'relative_path': 'main.py',
                                  'source_code': 'def main(): return 1', 'start_line': 1, 'end_line': 1, 'depends_on': []}},
                component_index=[], artifact_index={'nodes': 0}, static_analysis_ms=0)
    return wrapper.RepositoryAgent(fetcher=Fetcher(), codewiki_provider=Static(), enrichment_enabled=False)


async def run(wrapper_root):
    wrapper = load_repository(wrapper_root)
    plan = json.loads((ROOT / 'examples/repository_s3_week3.json').read_text(encoding='utf-8'))
    validate_plan(plan)
    payload = {k: plan['tasks'][0][k] for k in BUSINESS_TASK_FIELDS}
    calls = []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder / 'main.py').write_text('def main(): return 1\n', encoding='utf-8')
        (folder / 'README.md').write_text('# Offline synthetic repository\n', encoding='utf-8')
        agent = fixture_agent(wrapper, folder, calls)
        async def execute(task):
            native = to_repository_request(task)
            raw = agent.analyze(wrapper.RepositoryAnalyzeRequest.model_validate(native))
            return normalize_repository_result(task, raw, wrapper, fixture=True)
        aic = 'offline-repository-' + str(uuid4())
        app = create_local_business_app(execute=execute, partner_aic=aic,
                                       background_execution=True, mode='local-repository-fixture')
        client = AipRpcClient('http://127.0.0.1/rpc', 'offline-leader', identity_binding_enabled=False,
                              transport=httpx.ASGITransport(app=app))
        try:
            runner = AipTaskRunner(sdk_sender(client), 'offline-leader', aic, allow_mock_results=True, poll_seconds=.001)
            with patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('external network forbidden')):
                execution = await runner.run(payload, 'offline-repository-session')
        finally:
            await client.close()
    passed = execution['state'] == 'completed' and execution['simulatedResult'] is True and calls == ['fake-fetch-only', 'fake-static-only']
    record = {'passed': passed, 'scope': 'OFFLINE_REAL_REPOSITORY_WRAPPER_FAKE_PROVIDERS_OFFICIAL_AIP',
              'protocolCompleted': execution['state'] == 'completed', 'businessReady': False,
              'realResearchExecuted': False, 'codewikiExecuted': False, 'modelCalled': False,
              'platformDiscoveryUsed': False, 'sharedSchemaChanged': False,
              'sourceHashes': {p.name: sha256(p.read_bytes()).hexdigest() for p in (Path(wrapper_root) / 'researchmesh_repository').glob('*.py')}}
    output = ROOT / 'outputs' / datetime.now().strftime('repository-seam-offline-%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'plan.json', plan); save_json(output / 'payload.json', payload)
    save_json(output / 'execution.json', execution); save_json(output / 'acceptance.json', record)
    print(json.dumps(record, ensure_ascii=False, indent=2)); print('记录：', output)
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wrapper-root', type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.wrapper_root))


if __name__ == '__main__':
    raise SystemExit(main())
