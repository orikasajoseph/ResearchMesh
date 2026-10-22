"""Offline real Wrapper/Enricher + fake providers; no API, PDF or target code.

Not a live enrichment acceptance record. Actual HTTP clients are blocked here.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from aip_business_partner import create_local_business_app, LocalBackendFailure
from aip_repository_partner import make_repository_executor, repository_worker_env
from common.models import BUSINESS_TASK_FIELDS
from repository_adapter import load_repository
from repository_enhancement import prepare_enhanced_request, enrich_static_result
from repository_enhancement_contract import project_paper_notes, validate_enhancement_evidence
from repository_enhanced_worker import SCOPE, live_provider, run as worker_run
from result_contract import validate_result
from run_aip_repository import (build_plan, invoke_repository, assess_enhanced,
                                render_enhanced_report, main)
from tests.test_repository_support import native
from tests.test_reader_repository_handoff import payload as context_payload

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = load_repository(ROOT / 'agents/repository')


def raw_static(payload):
    data = native()
    data['subtaskId'] = payload['subtaskId']
    record = data['items'][0]['data']
    record['overview'] = None
    record['diagnostics'].update(
        codewikiVersion='2.0.0', codewikiCommit='f0975ea19b17568ceb55cd5422206e1a1ae13c0b',
        counts={'componentCount': 1, 'dependencyEdgeCount': 0, 'artifactCount': 0, 'evidenceCount': 1},
        metrics={'cloneMs': 0.0, 'staticAnalysisMs': 0.0, 'llmEnrichmentMs': 0.0, 'totalMs': 0.0})
    record['structure'] = [{'filePath': 'README.md', 'components': [
        {'componentId': 'README.md::readme', 'name': 'README', 'componentType': 'artifact', 'evidenceId': 'E1'}]}]
    return WRAPPER.RepositoryAnalyzeResult.model_validate(data)


def observations():
    return {'llmEnabled': True, 'modelCalls': 0, 'successfulModelResponses': 0,
            'modelUsage': [], 'pythonNetworkAttemptsBlocked': 0,
            'targetCodeExecuted': False, 'targetDependenciesInstalled': False}


class FakeProvider:
    def __init__(self, observed, mode='ok'):
        self.observed, self.mode = observed, mode

    def generate_json(self, messages, *, schema=None):
        self.observed['modelCalls'] += 1
        if self.mode == 'timeout':
            raise RuntimeError('DO_NOT_LEAK_TEST_SECRET')
        self.observed['successfulModelResponses'] += 1
        self.observed['modelUsage'].append({'promptTokens': 3, 'completionTokens': 2, 'totalTokens': 5})
        bundle = json.loads(messages[-1]['content'].split('Evidence bundle:\n')[1])
        raw = {'overview': {'text': 'README declares project structure.', 'evidenceIds': ['E1']},
               'reproductionBarriers': []}
        if bundle.get('paperNotes'):
            paper_id = bundle['paperNotes'][0]['evidence'][0]['evidenceId']
            raw['paperCodeRelations'] = [{'claim': 'Compare the supplied method with this README; needs human review.',
                                          'paperEvidenceIds': [paper_id], 'codeEvidenceIds': ['E1']}]
            if self.mode == 'bad-paper': raw['paperCodeRelations'][0]['paperEvidenceIds'] = ['invented']
            if self.mode == 'bad-code': raw['paperCodeRelations'][0]['codeEvidenceIds'] = ['invented']
            if self.mode == 'execution': raw['paperCodeRelations'][0]['claim'] = '已运行并成功复现论文。'
        return raw


def enhanced(payload=None, mode='ok'):
    payload = payload or context_payload()
    observed = observations()
    raw = raw_static(payload)
    result = enrich_static_result(payload, raw, WRAPPER, lambda obs: FakeProvider(obs, mode), observed)
    return result, observed, raw


def outcome_and_journal(payload=None, mode='ok'):
    payload = payload or context_payload()
    result, diagnostic, _ = enhanced(payload, mode)
    outcome = {'results': [result], 'executionRecords': [{'succeeded': True, 'aipState': 'completed'}]}
    backend = {'scope': SCOPE, 'phase': 'completed', 'task': payload,
               'result': result, 'error': None, 'diagnostics': diagnostic}
    return outcome, backend


class NoNetworkTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(httpx.HTTPTransport, 'handle_request', side_effect=AssertionError('no network')))
        self.enterContext(patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('no network')))


class DualModeTests(NoNetworkTests):
    def test_seven_fields_same_schema_and_bound_source_retained(self):
        payload = context_payload(); before = deepcopy(payload)
        _, request = prepare_enhanced_request(payload, WRAPPER)
        self.assertEqual(set(request.model_dump()), set(BUSINESS_TASK_FIELDS))
        self.assertEqual(request.schemaVersion, '0.2')
        self.assertEqual(request.taskType, 'repository_analyze')
        self.assertEqual(request.outputSchema, 'code_records.v1')
        self.assertEqual(request.input.paperNotes[0].source.paperId, payload['input']['paperNotes'][0]['source']['paperId'])
        self.assertEqual(payload, before)

    def test_ids_match_upstream_projection_algorithm(self):
        from hashlib import sha256
        note = context_payload()['input']['paperNotes'][0]; e = note['evidence'][0]
        identity = json.dumps([note['source']['paperId'], 0, e['url'], e['locator'], e['supports']], ensure_ascii=False)
        self.assertEqual(project_paper_notes([note])[0]['evidence'][0]['evidenceId'],
                         'P-' + sha256(identity.encode()).hexdigest()[:16])

    def test_projection_bounded_without_mutating_reader_result(self):
        note = context_payload()['input']['paperNotes'][0]
        note['method'] *= 200
        note['evidence'] *= 8
        before = deepcopy(note)
        selected = project_paper_notes([note])[0]
        self.assertEqual(len(selected['evidence']), 6)
        self.assertLessEqual(len(selected['method']), 2000)
        self.assertEqual(note, before)

    def test_rejects_malformed_evidence_even_after_projection_limit(self):
        note = context_payload()['input']['paperNotes'][0]
        note['evidence'] *= 7
        note['evidence'][-1] = dict(note['evidence'][-1], sourceId='other')
        with self.assertRaisesRegex(ValueError, 'SOURCE_MISMATCH'): project_paper_notes([note])

    def test_rejects_mock_abstract_wrong_source_and_empty_evidence(self):
        for change in ('mock', 'abstract', 'source', 'url', 'empty', 'supports'):
            note = context_payload()['input']['paperNotes'][0]
            if change == 'mock': note['isMock'] = True
            if change == 'abstract': note['analysisLevel'] = 'abstract'
            if change == 'source': note['evidence'][0]['sourceId'] = 'other'
            if change == 'url': note['evidence'][0]['url'] = 'https://example.invalid/other'
            if change == 'empty': note['evidence'] = []
            if change == 'supports': note['evidence'][0]['supports'] = []
            with self.subTest(change=change), self.assertRaises(ValueError): project_paper_notes([note])

    def test_static_and_enhanced_env_are_separate(self):
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'TEST', 'OPENAI_API_KEY': 'OTHER', 'SERVICE_PASSWORD': 'OTHER'}):
            static, enhanced_env = repository_worker_env('static'), repository_worker_env('enhanced')
        self.assertNotIn('DEEPSEEK_API_KEY', static)
        self.assertEqual(enhanced_env['DEEPSEEK_API_KEY'], 'TEST')
        self.assertNotIn('OPENAI_API_KEY', enhanced_env)
        self.assertNotIn('SERVICE_PASSWORD', enhanced_env)
        self.assertEqual(enhanced_env['PYTHON_DOTENV_DISABLED'], '1')
        with self.assertRaises(ValueError): repository_worker_env('auto')

    def test_real_wrapper_enricher_preserves_static_and_both_evidence_sides(self):
        result, observed, before = enhanced()
        data = result['items'][0]['data']
        self.assertEqual(data['structure'], before.items[0].data.model_dump()['structure'])
        self.assertIsNone(before.items[0].data.overview)
        self.assertEqual(observed['enrichmentStatus'], 'completed')
        self.assertEqual(observed['modelCalls'], 1)
        self.assertIsInstance(data['evidence'][0]['supports'], str)
        self.assertIsInstance(data['paperEvidence'][0]['supports'], str)
        self.assertEqual(len(data['paperCodeRelations']), 1)
        validate_result(result, context_payload())

    def test_bad_ids_and_execution_claims_removed_not_accepted(self):
        for mode in ('bad-paper', 'bad-code', 'execution'):
            result, observed, _ = enhanced(mode=mode)
            self.assertEqual(result['items'][0]['data']['paperCodeRelations'], [])
            self.assertIn('UNSUPPORTED_PAPER_CODE_CLAIM', result['warnings'])
            self.assertEqual(observed['enrichmentStatus'], 'no_supported_claims')

    def test_llm_failure_retains_static_and_does_not_leak_error(self):
        result, observed, before = enhanced(mode='timeout')
        self.assertEqual(result['items'][0]['data']['structure'], before.items[0].data.model_dump()['structure'])
        self.assertIn('LLM_ENRICHMENT_UNAVAILABLE', result['warnings'])
        self.assertEqual(observed['enrichmentStatus'], 'unavailable')
        self.assertNotIn('TEST_SECRET', json.dumps(result))

    def test_repository_only_enhancement_does_not_invent_paper_relation(self):
        task = build_plan('https://github.com/pypa/sampleproject')['tasks'][0]
        payload = {k: task[k] for k in BUSINESS_TASK_FIELDS}
        result, observed, _ = enhanced(payload)
        self.assertEqual(result['items'][0]['data']['paperEvidence'], [])
        self.assertEqual(result['items'][0]['data']['paperCodeRelations'], [])
        self.assertEqual(observed['enrichmentStatus'], 'completed')

    def test_leader_rejects_tampering_on_either_evidence_side(self):
        for change in ('paper-id', 'code-id', 'source', 'claim', 'remove-paper', 'duplicate-paper'):
            result, _, _ = enhanced(); data = result['items'][0]['data']
            if change == 'paper-id': data['paperCodeRelations'][0]['paperEvidenceIds'] = ['invented']
            if change == 'code-id': data['paperCodeRelations'][0]['codeEvidenceIds'] = ['invented']
            if change == 'source': data['paperEvidence'][0]['supports'] = 'tampered'
            if change == 'claim': data['paperCodeRelations'][0]['claim'] = 'We reproduced the paper.'
            if change == 'remove-paper': data['paperEvidence'] = []
            if change == 'duplicate-paper': data['paperEvidence'] *= 2
            with self.subTest(change=change), self.assertRaises(ValueError): validate_result(result, context_payload())

    def test_assessment_distinguishes_static_success_from_enhancement_failure(self):
        outcome, backend = outcome_and_journal(mode='timeout')
        acceptance = assess_enhanced(outcome, backend, context_payload())
        self.assertTrue(acceptance['protocolCompleted'])
        self.assertTrue(acceptance['businessReady'])
        self.assertFalse(acceptance['enhancementSucceeded'])
        self.assertFalse(acceptance['passed'])

    def test_assessment_refuses_wrong_scope_result_task_or_excess_calls(self):
        for change in ('scope', 'task', 'result', 'mock', 'budget', 'executed', 'unsuccessful-response'):
            outcome, backend = outcome_and_journal()
            # Detach persisted backend from the simulated AIP product.
            backend = deepcopy(backend)
            if change == 'scope': backend['scope'] = 'ISOLATED_CODEWIKI_STATIC_NO_LLM'
            if change == 'task': backend['task']['goal'] += 'wrong'
            if change == 'result': backend['result']['summary'] += 'wrong'
            if change == 'mock': backend['result']['items'][0]['data']['isMock'] = True
            if change == 'budget': backend['diagnostics']['modelCalls'] = 3
            if change == 'executed': backend['diagnostics']['targetCodeExecuted'] = True
            if change == 'unsuccessful-response': backend['diagnostics']['successfulModelResponses'] = 0
            with self.subTest(change=change): self.assertFalse(assess_enhanced(outcome, backend, context_payload())['passed'])

    def test_enhanced_report_does_not_claim_zero_model_calls_or_reproduction(self):
        outcome, backend = outcome_and_journal()
        acceptance = assess_enhanced(outcome, backend, context_payload())
        report = render_enhanced_report(outcome, acceptance)
        self.assertTrue(acceptance['passed'])
        self.assertNotIn('DeepSeek 增强关闭', report)
        self.assertNotIn('本轮关闭 LLM', report)
        self.assertIn('模型请求尝试数：1', report)
        self.assertIn('没有执行目标代码', report)
        self.assertIn('P-', report)

    def test_partial_report_labels_enhancement_incomplete(self):
        outcome, backend = outcome_and_journal(mode='timeout')
        report = render_enhanced_report(outcome, assess_enhanced(outcome, backend, context_payload()))
        self.assertIn('增强未完成', report)

    def test_explicit_mode_without_execute_is_preflight_only(self):
        source = build_plan('https://github.com/pypa/sampleproject')
        with TemporaryDirectory() as directory, patch('run_aip_repository.ROOT', Path(directory)):
            root = Path(directory); python = root / 'python.exe'; python.touch()
            wrapper = root / 'wrapper'; package = wrapper / 'researchmesh_repository'
            package.mkdir(parents=True); (package / '__init__.py').touch()
            with patch('run_aip_repository.build_plan', return_value=source), \
                    patch('run_aip_repository.subprocess.run', return_value=SimpleNamespace(returncode=0)) as process, \
                    patch('run_aip_repository.live_partner') as partner:
                code = main(['--repository', 'https://github.com/pypa/sampleproject', '--analysis-mode', 'enhanced',
                             '--repository-python', str(python), '--wrapper-root', str(wrapper)])
                self.assertEqual(code, 0); partner.assert_not_called()
                self.assertNotIn('--execute', process.call_args.args[0])
                self.assertIn('repository_enhanced_worker.py', ' '.join(process.call_args.args[0]))

    def test_static_mode_rejects_env_file_before_subprocess(self):
        with patch('run_aip_repository.subprocess.run') as process:
            self.assertEqual(main(['--repository', 'https://github.com/pypa/sampleproject', '--env-file', 'secret.env']), 1)
        process.assert_not_called()

    def test_worker_preflight_never_constructs_llm_or_reads_env(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); (root / 'repository_input.json').write_text(json.dumps(context_payload()), encoding='utf-8')
            args = SimpleNamespace(record_dir=root, wrapper_root=root, env_file=root / '.env', execute=False)
            with patch('repository_enhanced_worker.require_record_directory', return_value=root), \
                    patch('repository_enhanced_worker.runtime_check', return_value=WRAPPER), \
                    patch('repository_enhanced_worker.importlib.util.find_spec', return_value=object()), \
                    patch('repository_enhanced_worker.live_provider') as provider:
                self.assertEqual(worker_run(args), 0)
            provider.assert_not_called()
            record = json.loads((root / 'repository_preflight.json').read_text())
            self.assertEqual(record['diagnostics']['modelCalls'], 0)
            self.assertFalse(record['keyChecked'])

    def test_worker_checkpoints_static_before_fake_enrichment(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); payload = context_payload()
            (root / 'repository_input.json').write_text(json.dumps(payload), encoding='utf-8')
            args = SimpleNamespace(record_dir=root, wrapper_root=root, env_file=None, execute=True)
            observed_checkpoint = []
            def provider_factory(obs):
                observed_checkpoint.append((root / 'static_checkpoint.json').is_file())
                return FakeProvider(obs)
            with patch('repository_enhanced_worker.require_record_directory', return_value=root), \
                    patch('repository_enhanced_worker.runtime_check', return_value=WRAPPER), \
                    patch('repository_enhanced_worker.importlib.util.find_spec', return_value=object()), \
                    patch.object(WRAPPER.RepositoryAgent, 'analyze', return_value=raw_static(payload)), \
                    patch('repository_enhanced_worker.live_provider', side_effect=provider_factory):
                self.assertEqual(worker_run(args), 0)
            record = json.loads((root / 'backend_outcome.json').read_text())
            self.assertEqual(observed_checkpoint, [True])
            self.assertEqual(record['scope'], SCOPE)
            self.assertEqual(record['phase'], 'completed')
            self.assertEqual(record['diagnostics']['modelCalls'], 1)

    def test_counted_shared_provider_bounded_json_retry_and_usage(self):
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":1}'))],
                                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5), model='fake', id='offline')
        create = Mock(return_value=reply)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'OFFLINE_FAKE_KEY'}), \
                patch('common.llm.researchmesh_llm.provider.OpenAI', return_value=client):
            observed = observations(); provider = live_provider(observed)
            self.assertEqual(provider.generate_json([{'role': 'user', 'content': 'offline'}]), {'answer': 1})
            self.assertEqual(observed['modelCalls'], 1)
            self.assertEqual(observed['modelUsage'][0]['totalTokens'], 5)
            reply.choices[0].message.content = ''
            with self.assertRaises(Exception): provider.generate_json([{'role': 'user', 'content': 'offline'}])
            self.assertEqual(create.call_count, 2)
            self.assertEqual(observed['modelCalls'], 2)

    def test_shared_provider_transport_failure_does_not_retry_paid_request(self):
        create = Mock(side_effect=RuntimeError('OFFLINE_CONNECTION_FAILURE'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'OFFLINE_FAKE_KEY'}), \
                patch('common.llm.researchmesh_llm.provider.OpenAI', return_value=client):
            observed = observations(); provider = live_provider(observed)
            with self.assertRaisesRegex(RuntimeError, 'NO_AUTOMATIC_RETRY'):
                provider.generate_json([{'role': 'user', 'content': 'offline'}])
        self.assertEqual(create.call_count, 1)
        self.assertEqual(observed['modelCalls'], 1)


class EnhancedAipTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_sdk_start_get_complete_uses_enhanced_result(self):
        payload = context_payload(); outcome, backend = outcome_and_journal(payload)
        worker = AsyncMock(return_value=backend)
        execute = make_repository_executor(worker, payload, analysis_mode='enhanced')
        aic = 'offline-repository-enhanced'
        app = create_local_business_app(execute=execute, partner_aic=aic,
                                        mode='local-repository-live', background_execution=True)
        plan = build_plan('https://github.com/pypa/sampleproject')
        task = deepcopy(payload); task.update(requiredSkill='repository.analyze', dependsOn=[])
        plan['tasks'] = [task]
        with patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('no network')):
            actual = await invoke_repository(plan, 'http://127.0.0.1/rpc', aic, transport=httpx.ASGITransport(app=app))
        self.assertTrue(assess_enhanced(actual, backend, payload)['passed'])
        self.assertEqual(worker.await_count, 1)
        self.assertEqual(actual['aipMessages'][-1]['command']['command'], 'complete')
        self.assertEqual(actual['results'], outcome['results'])

    async def test_duplicate_does_not_repeat_paid_worker(self):
        payload = context_payload(); _, backend = outcome_and_journal(payload)
        worker = AsyncMock(return_value=backend)
        execute = make_repository_executor(worker, payload, analysis_mode='enhanced')
        first = await execute(payload); second = await execute(payload)
        self.assertEqual(first, second); self.assertEqual(worker.await_count, 1)

    async def test_wrong_mode_scope_and_uncertain_failure_do_not_retry(self):
        payload = context_payload(); _, backend = outcome_and_journal(payload)
        worker = AsyncMock(return_value=backend)
        execute = make_repository_executor(worker, payload)  # default must stay static
        with self.assertRaisesRegex(LocalBackendFailure, 'WORKER_FAILED'): await execute(payload)
        with self.assertRaisesRegex(LocalBackendFailure, 'PREVIOUS_EXECUTION_UNCERTAIN'): await execute(payload)
        self.assertEqual(worker.await_count, 1)


class ReaderReuseCliTests(NoNetworkTests):
    def test_external_existing_cache_and_enhanced_report_without_reader_replay(self):
        from tests.test_reader_repository_handoff import checkpoint
        from run_reader_repository import main as reuse_main
        saved = checkpoint()
        saved.update(readerSource='offline-existing-reader', sourceManifest=[{'path': 'offline-manifest', 'sha256': 'fixture'}])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'original-pdf-cache'
            def fake_repository(command):
                output = Path(command[command.index('--record-dir') + 1])
                output.mkdir(parents=True)
                (output / 'acceptance.json').write_text(json.dumps({
                    'passed': True, 'protocolCompleted': True, 'paperContextAnalyzed': True,
                    'enhancementSucceeded': True, 'modelCalls': 1}), encoding='utf-8')
                (output / 'outcome.json').write_text('{}', encoding='utf-8')
                (output / 'report.md').write_text('Offline child report only.', encoding='utf-8')
                self.assertIn('--analysis-mode', command)
                self.assertEqual(command[command.index('--analysis-mode') + 1], 'enhanced')
                return 0
            with patch('run_reader_repository.ROOT', root), \
                    patch('run_reader_repository.load_reader_source', return_value=saved) as loader, \
                    patch('run_reader_repository.verify_manifest') as verify, \
                    patch('run_reader_repository.repository_main', side_effect=fake_repository) as child:
                result = reuse_main(['--reader-source', 'offline-existing-reader', '--reader-cache', str(cache),
                                     '--repository', 'https://github.com/pypa/sampleproject',
                                     '--analysis-mode', 'enhanced', '--execute'])
                self.assertEqual(result, 0)
                self.assertEqual(loader.call_args.kwargs['cache_dir'], cache.resolve())
                self.assertEqual(child.call_count, 1)
                self.assertEqual(verify.call_count, 2)
            run = next((root / 'outputs').iterdir())
            report = (run / 'report.md').read_text(encoding='utf-8')
            self.assertIn('显式开启 DeepSeek', report)
            self.assertNotIn('关闭 DeepSeek', report)
            acceptance = json.loads((run / 'acceptance.json').read_text(encoding='utf-8'))
            self.assertEqual(acceptance['newReaderCalls'], 0)
            self.assertEqual(acceptance['newSearchCalls'], 0)
            self.assertEqual(acceptance['newPlannerCalls'], 0)
            self.assertEqual(acceptance['newModelCalls'], 1)


if __name__ == '__main__':
    unittest.main()
