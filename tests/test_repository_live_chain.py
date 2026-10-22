"""Offline regression only: synthetic outputs, no Git/CodeWiki/LLM/network."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from aip_business_partner import create_local_business_app, LocalBackendFailure
from aip_repository_partner import make_repository_executor, static_worker_env
from input_binding import bind_task_input
from repository_adapter import normalize_repository_result
from repository_aip_worker import static_only_guard, run as worker_run
from run_aip_repository import build_plan, selected_plan, invoke_repository, assess, render_report, main
from tests.test_repository_support import task, native, WRAPPER


def bound_payload():
    value = bind_task_input(task(), {})
    return {k: v for k, v in value.items() if k not in ('requiredSkill', 'dependsOn')}


def synthetic_result():
    raw = native()
    raw['items'][0]['data']['diagnostics'].update(
        metrics={'cloneMs': 0, 'staticAnalysisMs': 0, 'llmEnrichmentMs': 0, 'totalMs': 0},
        counts={'componentCount': 1, 'dependencyEdgeCount': 0, 'artifactCount': 0, 'evidenceCount': 1})
    raw['items'][0]['data']['overview'] = None
    return normalize_repository_result(bound_payload(), raw, WRAPPER)


def journal(result=None):
    return {'scope': 'ISOLATED_CODEWIKI_STATIC_NO_LLM', 'phase': 'completed', 'task': bound_payload(),
            'result': result or synthetic_result(), 'error': None,
            'diagnostics': {'llmEnabled': False, 'modelCalls': 0, 'pythonNetworkAttemptsBlocked': 0,
                            'targetCodeExecuted': False, 'targetDependenciesInstalled': False}}


class RepositoryEntryTests(unittest.TestCase):
    def test_url_builds_single_frozen_task_without_llm(self):
        value = build_plan('https://github.com/pypa/sampleproject')
        self.assertEqual(value['tasks'][0]['taskType'], 'repository_analyze')
        self.assertEqual(value['tasks'][0]['requiredSkill'], 'repository.analyze')
        self.assertFalse(value['tasks'][0]['constraints']['executeCode'])

    def test_invalid_private_local_and_credential_urls_rejected(self):
        for url in ('http://127.0.0.1/repo', 'file:///tmp/repo', 'https://user:secret@github.com/pypa/sampleproject',
                    'https://github.com/pypa/sampleproject?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError): build_plan(url)

    def test_requires_independent_single_task(self):
        p = build_plan('https://github.com/pypa/sampleproject')
        p['tasks'].append(deepcopy(p['tasks'][0])); p['tasks'][-1]['subtaskId'] = 'S4'
        with self.assertRaises(ValueError): selected_plan(p)

    def test_no_execution_before_explicit_flag(self):
        source = build_plan('https://github.com/pypa/sampleproject')
        with TemporaryDirectory() as directory, patch('run_aip_repository.ROOT', Path(directory)):
            root = Path(directory)
            package = root / 'agents/repository/researchmesh_repository'; package.mkdir(parents=True)
            (package / '__init__.py').write_text('', encoding='utf-8')
            python = root / '.venv-repository/Scripts/python.exe'; python.parent.mkdir(parents=True); python.touch()
            with patch('run_aip_repository.build_plan', return_value=source), \
                    patch('run_aip_repository.subprocess.run', return_value=SimpleNamespace(returncode=0)) as process, \
                    patch('run_aip_repository.live_partner') as partner:
                self.assertEqual(main(['--repository', 'https://github.com/pypa/sampleproject']), 0)
                self.assertEqual(process.call_count, 1)
                self.assertNotIn('--execute', process.call_args.args[0])
                partner.assert_not_called()

    def test_worker_environment_drops_credentials_disables_dotenv(self):
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'TEST_SECRET', 'OPENAI_API_KEY': 'TEST_SECRET',
                                      'SERVICE_PASSWORD': 'TEST_SECRET'}, clear=False):
            env = static_worker_env()
        self.assertNotIn('DEEPSEEK_API_KEY', env); self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('SERVICE_PASSWORD', env); self.assertEqual(env['PYTHON_DOTENV_DISABLED'], '1')

    def test_worker_guard_denies_python_connections(self):
        observations = {'pythonNetworkAttemptsBlocked': 0}
        with static_only_guard(observations):
            with self.assertRaisesRegex(RuntimeError, 'NETWORK_FORBIDDEN'):
                socket.create_connection(('example.invalid', 443))
        self.assertEqual(observations['pythonNetworkAttemptsBlocked'], 1)

    def test_assessment_cannot_turn_protocol_only_into_business_success(self):
        outcome = {'executionRecords': [{'succeeded': True, 'aipState': 'completed'}], 'results': [synthetic_result()]}
        self.assertTrue(assess(outcome, journal(), bound_payload())['passed'])
        for change in ('no-journal', 'wrong-task', 'llm', 'network', 'mock', 'wrong-result'):
            record = journal(); output = deepcopy(outcome)
            if change == 'no-journal': record = None
            elif change == 'wrong-task': record['task']['subtaskId'] = 'other'
            elif change == 'llm': record['diagnostics']['modelCalls'] = 1
            elif change == 'network': record['diagnostics']['pythonNetworkAttemptsBlocked'] = 1
            elif change == 'mock':
                output['results'][0]['items'][0]['data']['isMock'] = True
                record['result'] = output['results'][0]
            else: record['result']['summary'] = 'different'
            with self.subTest(change=change): self.assertFalse(assess(output, record, bound_payload())['passed'])

    def test_report_states_no_execution_or_reproduction(self):
        outcome = {'results': [synthetic_result()]}
        report = render_report(outcome, {'passed': True, 'protocolCompleted': True, 'businessReady': True})
        self.assertIn('没有执行目标代码', report)
        self.assertIn('未生成（本轮关闭 LLM）', report)
        self.assertIn('/blob/' + 'a' * 40, report)

    def test_worker_failure_records_safe_code_not_raw_message(self):
        with TemporaryDirectory() as directory, patch.dict('os.environ', {}, clear=False):
            folder = Path(directory)
            (folder / 'repository_input.json').write_text(json.dumps(bound_payload()), encoding='utf-8')
            args = SimpleNamespace(record_dir=folder, wrapper_root=folder, execute=True)
            with patch('repository_aip_worker.require_record_directory', return_value=folder), \
                    patch('repository_aip_worker.runtime_check', side_effect=RuntimeError('TEST_SECRET')):
                self.assertEqual(worker_run(args), 1)
            text = (folder / 'backend_outcome.json').read_text(encoding='utf-8')
            self.assertNotIn('TEST_SECRET', text)
            self.assertIn('REPOSITORY_WORKER_FAILED', text)


class RepositoryLeaderChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_leader_resolves_skill_and_sends_seven_fields(self):
        calls = []
        async def execute(payload):
            calls.append(deepcopy(payload)); return synthetic_result()
        aic = 'offline-test-' + str(uuid4())
        app = create_local_business_app(execute=execute, partner_aic=aic,
                        mode='local-repository-live', background_execution=True)
        with patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('external network forbidden')):
            outcome = await invoke_repository(build_plan('https://github.com/pypa/sampleproject'),
                       'http://127.0.0.1/rpc', aic, transport=httpx.ASGITransport(app=app))
        self.assertTrue(outcome['executionRecords'][0]['succeeded'])
        self.assertEqual(outcome['executionRecords'][0]['aipState'], 'completed')
        self.assertEqual(outcome['executionRecords'][0]['agentId'], aic)
        expected = bind_task_input(build_plan('https://github.com/pypa/sampleproject')['tasks'][0], {})
        expected = {k: v for k, v in expected.items() if k not in ('requiredSkill', 'dependsOn')}
        self.assertEqual(calls, [expected])
        self.assertEqual(len(calls[0]), 7)
        commands = [x['command']['command'] for x in outcome['aipMessages']]
        self.assertIn('get', commands); self.assertEqual(commands[-1], 'complete')
        self.assertFalse(outcome['platformDiscoveryVerified']); self.assertFalse(outcome['plannerCalled'])

    async def test_backend_failure_does_not_get_completed(self):
        async def execute(payload): raise LocalBackendFailure('STATIC_ANALYSIS_FAILED')
        aic = 'offline-test-' + str(uuid4())
        app = create_local_business_app(execute=execute, partner_aic=aic,
                        mode='local-repository-live', background_execution=True)
        outcome = await invoke_repository(build_plan('https://github.com/pypa/sampleproject'),
                  'http://127.0.0.1/rpc', aic, transport=httpx.ASGITransport(app=app))
        self.assertFalse(outcome['executionRecords'][0]['succeeded'])
        self.assertEqual(outcome['results'], [])

    async def test_worker_runs_only_once_for_matching_bound_payload(self):
        call = AsyncMock(return_value=journal())
        execute = make_repository_executor(call, bound_payload())
        self.assertEqual(await execute(bound_payload()), await execute(bound_payload()))
        self.assertEqual(call.await_count, 1)

    async def test_worker_wrong_task_and_failed_journal_rejected(self):
        execute = make_repository_executor(AsyncMock(return_value=journal()), bound_payload())
        changed = bound_payload(); changed['goal'] = 'changed'
        with self.assertRaisesRegex(LocalBackendFailure, 'INVALID_REQUEST'): await execute(changed)
        record = journal(); record.update(phase='failed', error={'code': 'STATIC_ANALYSIS_FAILED'})
        execute = make_repository_executor(AsyncMock(return_value=record), bound_payload())
        with self.assertRaisesRegex(LocalBackendFailure, 'STATIC_ANALYSIS_FAILED'): await execute(bound_payload())
        with self.assertRaisesRegex(LocalBackendFailure, 'PREVIOUS_EXECUTION_UNCERTAIN'): await execute(bound_payload())

    async def test_remote_invocation_is_forbidden(self):
        with self.assertRaisesRegex(ValueError, 'LOOPBACK_RPC_REQUIRED'):
            await invoke_repository(build_plan('https://github.com/pypa/sampleproject'), 'https://example.invalid/rpc', 'test')


if __name__ == '__main__': unittest.main()
