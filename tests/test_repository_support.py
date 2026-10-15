"""Zhao seam/SDK/registry tests. No real clone, CodeWiki, API or model."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

import httpx
from acps_sdk.aip import AipRpcClient, StructuredDataItem, TaskCommand, TaskCommandType, TaskState
from aip_business_partner import create_local_business_app
from aip_executor import AipAgentExecutor
from aip_lifecycle import AipTaskRunner, sdk_sender, AipExecutionError
from aip_record_paths import require_record_directory
from common.models import BUSINESS_TASK_FIELDS
from repository_adapter import to_repository_request, normalize_repository_result, evidence_url, RepositoryAdapterError
from resolver.base import AgentHandle, NoMatchingAgentError
from resolver.local import LocalAipResolver
from run_aip_literature import invoke_business


def task():
    return {'schemaVersion': '0.2', 'subtaskId': 'S3', 'taskType': 'repository_analyze', 'goal': 'Static source only',
            'input': {'repositoryUrls': ['https://github.com/pypa/sampleproject']},
            'constraints': {'maxRepositories': 1, 'executeCode': False, 'allowRepositorySearch': False, 'requirePaperNotes': False},
            'outputSchema': 'code_records.v1', 'requiredSkill': 'repository.analyze', 'dependsOn': []}


def payload():
    t = task(); return {k: t[k] for k in BUSINESS_TASK_FIELDS}


def native():
    return {'schemaVersion': '0.2', 'subtaskId': 'S3', 'taskType': 'repository_analyze', 'summary': 'Offline schema fixture',
            'warnings': [], 'items': [{'kind': 'repository_record', 'data': {
                'repository': {'url': 'https://github.com/pypa/sampleproject.git', 'commitHash': 'a' * 40},
                'structure': [], 'dependencies': [], 'entrypoints': [], 'runCommands': [], 'reproductionBarriers': [],
                'overview': {'text': 'README declares this fixture project.', 'evidenceIds': ['E1']},
                'diagnostics': {'provider': 'codewiki-static'}, 'evidence': [
                    {'evidenceId': 'E1', 'filePath': 'README.md', 'sourceType': 'readme', 'symbol': None,
                     'startLine': 1, 'endLine': 2, 'text': 'Synthetic source, not a real repository', 'supports': ['structure']}]
            }}]}


WRAPPER = SimpleNamespace(RepositoryAnalyzeResult=SimpleNamespace(
    model_validate=lambda raw: SimpleNamespace(model_dump=lambda **kwargs: deepcopy(raw))))


class RepositorySupportTests(unittest.TestCase):
    def test_native_request_strips_internal_fields_and_preserves_original(self):
        original = task(); before = deepcopy(original)
        converted = to_repository_request(original)
        self.assertEqual(original, before)
        self.assertEqual(set(converted), set(BUSINESS_TASK_FIELDS))
        self.assertEqual(converted['constraints'], {'maxRepositories': 1, 'executeCode': False})

    def test_multiple_repositories_are_rejected_not_truncated(self):
        original = task(); original['input']['repositoryUrls'].append('https://github.com/pypa/pip')
        with self.assertRaisesRegex(RepositoryAdapterError, 'SINGLE_REPOSITORY_REQUIRED'):
            to_repository_request(original)

    def test_execution_search_unknown_constraints_and_wrong_skill_rejected(self):
        for change in ('execute', 'search', 'unknown', 'skill'):
            value = task()
            if change == 'execute': value['constraints']['executeCode'] = True
            elif change == 'search': value['constraints']['allowRepositorySearch'] = True
            elif change == 'unknown': value['constraints']['runCommands'] = True
            else: value['requiredSkill'] = 'paper.analyze'
            with self.subTest(change=change), self.assertRaises(RepositoryAdapterError): to_repository_request(value)

    def test_empty_url_fails_before_backend(self):
        value = task(); value['input']['repositoryUrls'] = []
        with self.assertRaises(RepositoryAdapterError): to_repository_request(value)

    def test_paper_context_never_silently_discarded(self):
        for key in ('papers', 'paperNotes'):
            value = task()
            value['input'][key] = ([{'url': 'https://arxiv.org/abs/2106.09685'}] if key == 'papers' else [{'findings': 'synthetic'}])
            with self.assertRaisesRegex(RepositoryAdapterError, 'REPOSITORY_PAPER_CONTEXT_NOT_SUPPORTED'):
                to_repository_request(value)

    def test_fixtures_are_explicitly_marked_and_cannot_supply_real_evidence(self):
        value = normalize_repository_result(payload(), native(), WRAPPER, fixture=True)
        self.assertEqual(value['items'][0]['kind'], 'repository')
        self.assertTrue(value['items'][0]['data']['isMock'])
        self.assertEqual(value['items'][0]['data']['evidence'], [])
        self.assertTrue(any(w.startswith('SIMULATED_RESEARCH') for w in value['warnings']))

    def test_normalization_preserves_native_and_maps_concrete_claim_not_category(self):
        raw = native(); before = deepcopy(raw)
        result = normalize_repository_result(payload(), raw, WRAPPER)
        self.assertEqual(raw, before)
        self.assertEqual(result['items'][0]['data']['evidence'][0]['supports'], 'README declares this fixture project.')
        self.assertEqual(result['items'][0]['data']['license'], 'UNKNOWN')

    def test_no_claim_falls_back_to_direct_static_source_statement(self):
        raw = native(); raw['items'][0]['data']['overview'] = None
        result = normalize_repository_result(payload(), raw, WRAPPER)
        self.assertIn('target code was not executed', result['items'][0]['data']['evidence'][0]['supports'])

    def test_mismatched_task_or_repository_is_rejected(self):
        for change in ('task', 'repo'):
            raw = native()
            if change == 'task': raw['subtaskId'] = 'other'
            else: raw['items'][0]['data']['repository']['url'] = 'https://github.com/pypa/pip'
            with self.assertRaises(RepositoryAdapterError): normalize_repository_result(payload(), raw, WRAPPER)

    def test_empty_duplicate_or_unknown_evidence_rejected(self):
        for change in ('empty', 'duplicate', 'unknown'):
            raw = native(); record = raw['items'][0]['data']
            if change == 'empty': record['evidence'] = []
            elif change == 'duplicate': record['evidence'].append(deepcopy(record['evidence'][0]))
            else: record['overview']['evidenceIds'] = ['invented']
            with self.assertRaises(RepositoryAdapterError): normalize_repository_result(payload(), raw, WRAPPER)

    def test_empty_native_result_not_completed_analysis(self):
        raw = native(); raw['items'] = []
        with self.assertRaisesRegex(RepositoryAdapterError, 'REPOSITORY_RESULT_UNAVAILABLE'):
            normalize_repository_result(payload(), raw, WRAPPER)

    def test_permalink_rejects_untrusted_paths_commit_and_line_ranges(self):
        for path in ('../secret', '/root', 'C:/secret', 'a%2Fb', 'a//b', 'a?token=x', 'a#b'):
            with self.assertRaises(RepositoryAdapterError): evidence_url('https://github.com/pypa/sampleproject', 'a' * 40, path)
        for start, end in ((True, 2), (0, 2), (2, 1), (None, 2)):
            with self.assertRaises(RepositoryAdapterError): evidence_url('https://github.com/pypa/sampleproject', 'a' * 40, 'README.md', start, end)
        with self.assertRaises(RepositoryAdapterError): evidence_url('https://github.com/pypa/sampleproject', 'not-a-commit', 'README.md')

    def test_repository_stage_directory_reuses_guard(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); output = root / 'outputs/aip-research-week3/repository'; output.mkdir(parents=True)
            self.assertEqual(require_record_directory(output, root, 'repository'), output.resolve())
            outside = root / 'outside'; outside.mkdir()
            with self.assertRaises(ValueError): require_record_directory(outside, root, 'repository')


class RepositoryAipSupportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.aic = 'test-s3-' + str(uuid4()); self.calls = []
        async def execute(task):
            self.calls.append(deepcopy(task))
            return normalize_repository_result(task, native(), WRAPPER, fixture=True)
        self.app = create_local_business_app(execute=execute, partner_aic=self.aic,
                    mode='local-repository-fixture', background_execution=True)
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients: await client.close()

    def client(self, app=None):
        client = AipRpcClient('http://127.0.0.1/rpc', 'test-leader', identity_binding_enabled=False,
                             transport=httpx.ASGITransport(app=app or self.app))
        self.clients.append(client); return client

    async def test_official_lifecycle_completes_only_with_explicit_fixture_permission(self):
        runner = AipTaskRunner(sdk_sender(self.client()), 'test-leader', self.aic, allow_mock_results=True, poll_seconds=.001)
        result = await runner.run(payload(), 'session')
        self.assertEqual(result['state'], 'completed'); self.assertTrue(result['simulatedResult'])
        self.assertEqual(set(self.calls[0]), set(BUSINESS_TASK_FIELDS))

    async def test_real_invoker_rejects_fixture_no_false_business_pass(self):
        out = await invoke_business(payload(), 'http://127.0.0.1/rpc', self.aic,
                  expected_task_type='repository_analyze', transport=httpx.ASGITransport(app=self.app), poll_seconds=.001)
        self.assertEqual(out['error']['code'], 'INVALID_BUSINESS_RESULT')

    async def test_health_reports_only_repository_skill(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://local') as client:
            health = (await client.get('/health')).json()
        self.assertEqual(health['skills'], ['repository.analyze'])
        self.assertFalse(health['identityVerified'])

    async def test_default_fixture_does_not_accidentally_allow_s3(self):
        async def execute(task): self.fail('must not call backend')
        app = create_local_business_app(execute=execute, partner_aic=self.aic)
        runner = AipTaskRunner(sdk_sender(self.client(app)), 'test-leader', self.aic, allow_mock_results=True)
        with self.assertRaisesRegex(AipExecutionError, 'PARTNER_REJECTED'): await runner.run(payload(), 'session')


class LocalResolverTests(unittest.IsolatedAsyncioTestCase):
    def handle(self, aic='repository-test', skill='repository.analyze'):
        runner = AipTaskRunner(None, 'leader-test', aic)
        return AgentHandle(aic, (skill,), AipAgentExecutor(runner))

    async def test_local_resolution_returns_exact_registered_aip_executor(self):
        handle = self.handle(); resolver = LocalAipResolver([handle])
        self.assertIs(await resolver.resolve('repository.analyze'), handle)
        self.assertIn('NOT_PLATFORM', resolver.scope)

    async def test_missing_or_ambiguous_skill_has_no_fallback(self):
        for handles in ([], [self.handle('one'), self.handle('two')]):
            with self.assertRaises(NoMatchingAgentError): await LocalAipResolver(handles).resolve('repository.analyze')

    async def test_wrong_identity_duplicate_and_unknown_skill_rejected(self):
        bad = AgentHandle('different', ('repository.analyze',), self.handle().executor)
        for handles in ([bad], [self.handle(), self.handle()], [self.handle(skill='guess')]):
            with self.assertRaises(ValueError): LocalAipResolver(handles)
