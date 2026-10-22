"""Offline paper/Repository handoff tests; no real Reader, Git, model or PDF."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, AsyncMock
from uuid import uuid4

import httpx
from aip_business_partner import create_local_business_app
from aip_repository_partner import make_repository_executor
from input_binding import bind_task_input
from reader_repository_handoff import build_handoff, verify_manifest, check_reader_products
from repository_adapter import to_repository_request, RepositoryAdapterError
from repository_paper_context import prepare_repository_task, compare_notes, attach_comparison, comparison_valid, digest, MODE
from run_aip_repository import selected_plan, invoke_repository, assess, render_report
from tests.test_repository_live_chain import synthetic_result


def paper():
    return {'paperId': 'offline-p1', 'title': 'Offline synthetic LoRA note', 'doi': None,
            'url': 'https://arxiv.org/abs/2106.09685', 'pdfUrl': 'https://arxiv.org/pdf/2106.09685', 'repositoryUrls': []}


def note():
    return {'source': paper(), 'analysisLevel': 'fulltext', 'method': 'Low rank adaptation with AdamW.',
            'findings': 'GPT results are not validated by this offline example.',
            'limitations': 'Unknownoptimizer was not evaluated.', 'evidence': [
                {'sourceId': 'offline-p1', 'url': paper()['pdfUrl'], 'locator': 'Synthetic page 1',
                 'supports': 'Synthetic fixture only, no actual paper reading.'}]}


def checkpoint():
    result = {'schemaVersion': '0.2', 'subtaskId': 'S2', 'taskType': 'paper_read', 'summary': 'Synthetic offline Reader',
              'warnings': [], 'items': [{'kind': 'paper_note', 'data': note()}]}
    return {'scope': 'VERIFIED_PREVIOUS_REAL_AIP_READER_NOT_REPLAYED', 'readerRecord': {'result': result,
            'aipTaskId': 'offline-previous-reader'}, 'readerResultSha256': digest(result)}


def payload():
    t = build_handoff(checkpoint(), 'https://github.com/pypa/sampleproject')['plan']['tasks'][0]
    bound = bind_task_input(t, {})
    return {k: v for k, v in bound.items() if k not in ('requiredSkill', 'dependsOn')}


def static_result():
    result = synthetic_result()
    result['items'][0]['data']['evidence'][0]['text'] = 'Low rank adaptation uses AdamW. Paper https://arxiv.org/abs/2106.09685.'
    return result


class PaperContextTests(unittest.TestCase):
    def test_old_native_adapter_still_rejects_context_not_silently_stripped(self):
        with self.assertRaisesRegex(ValueError, 'PAPER_CONTEXT_NOT_SUPPORTED'): to_repository_request(payload())

    def test_composite_splits_native_request_and_preserves_original_context(self):
        value = payload(); before = deepcopy(value)
        static, native, notes = prepare_repository_task(value)
        self.assertEqual(value, before); self.assertEqual(notes, [note()])
        self.assertEqual(native['input'], {'repositoryUrls': ['https://github.com/pypa/sampleproject']})
        self.assertEqual(native['constraints'], {'maxRepositories': 1, 'executeCode': False})
        self.assertFalse(static['constraints']['requirePaperNotes'])

    def test_source_mismatch_is_rejected(self):
        value = payload(); value['input']['papers'][0]['url'] = 'https://arxiv.org/abs/1706.03762'
        with self.assertRaisesRegex(ValueError, 'SOURCE_MISMATCH'): prepare_repository_task(value)

    def test_mock_abstract_and_no_evidence_cannot_be_fulltext_context(self):
        for change in ('mock', 'abstract', 'no-evidence'):
            value = payload(); n = value['input']['paperNotes'][0]
            if change == 'mock': n['isMock'] = True
            elif change == 'abstract': n['analysisLevel'] = 'abstract'
            else: n['evidence'] = []
            with self.subTest(change=change), self.assertRaises(ValueError): prepare_repository_task(value)

    def test_missing_notes_and_multiple_notes_not_ignored(self):
        for notes in ([], [note(), note()]):
            value = payload(); value['input']['paperNotes'] = notes
            with self.assertRaises(ValueError): prepare_repository_task(value)

    def test_oversize_context_not_truncated_or_silently_dropped(self):
        value = payload(); value['input']['paperNotes'][0]['method'] = 'x' * 160_001
        with self.assertRaisesRegex(ValueError, 'CONTEXT_TOO_LARGE'): prepare_repository_task(value)

    def test_existing_security_flags_still_enforced(self):
        for flag in ('executeCode', 'allowRepositorySearch'):
            value = payload(); value['constraints'][flag] = True
            with self.assertRaises(ValueError): prepare_repository_task(value)

    def test_context_changes_output_and_retains_both_evidence_sides(self):
        value = payload(); result = attach_comparison(static_result(), value, value['input']['paperNotes'])
        comparison = result['items'][0]['data']['paperComparison']
        self.assertEqual(comparison['mode'], MODE)
        self.assertEqual(comparison['papers'][0]['noteSha256'], digest(note()))
        self.assertEqual(comparison['papers'][0]['paperEvidence'], note()['evidence'])
        self.assertEqual(comparison['papers'][0]['repositoryIdentifierMentions'], [{'identifier': '2106.09685', 'repositoryEvidenceId': 'E1'}])
        row = comparison['papers'][0]['rows'][0]
        self.assertEqual(row['paperText'], note()['method'])
        self.assertEqual(row['matches'][0]['repositoryEvidenceId'], 'E1')
        self.assertFalse(row['semanticClaimVerified'])
        other = note(); other['method'] = 'Xylophonical oddtoken.'
        changed = compare_notes([other], result['items'][0]['data']['evidence'])
        self.assertNotEqual(comparison, changed)
        self.assertEqual(changed['papers'][0]['rows'][0]['matches'], [])

    def test_matching_terms_do_not_verify_a_negated_claim(self):
        n = note(); n['method'] = 'AdamW is NOT supported.'
        comparison = compare_notes([n], static_result()['items'][0]['data']['evidence'])
        row = comparison['papers'][0]['rows'][0]
        self.assertTrue(row['matches']); self.assertFalse(row['semanticClaimVerified'])
        self.assertFalse(comparison['reproductionVerified'])

    def test_identifier_matching_does_not_accept_numeric_prefix(self):
        evidence = static_result()['items'][0]['data']['evidence']
        evidence[0]['text'] = 'Different ID 2106.096850'
        self.assertEqual(compare_notes([note()], evidence)['papers'][0]['repositoryIdentifierMentions'], [])

    def test_comparison_cannot_be_forged_by_just_echoing_digest(self):
        value = payload(); result = attach_comparison(static_result(), value, [note()])
        self.assertTrue(comparison_valid(result, value))
        result['items'][0]['data']['paperComparison']['papers'][0]['rows'][0]['matches'][0]['terms'] = ['invented']
        self.assertFalse(comparison_valid(result, value))

    def test_missing_paper_comparison_fails_context_business_acceptance(self):
        value = payload(); result = static_result()
        outcome = {'executionRecords': [{'succeeded': True, 'aipState': 'completed'}], 'results': [result]}
        backend = {'scope': 'ISOLATED_CODEWIKI_STATIC_NO_LLM', 'phase': 'completed', 'task': value,
            'result': result, 'error': None, 'diagnostics': {'llmEnabled': False, 'modelCalls': 0,
                'pythonNetworkAttemptsBlocked': 0, 'targetCodeExecuted': False, 'targetDependenciesInstalled': False,
                'paperContextCompared': True}}
        self.assertFalse(assess(outcome, backend, value)['passed'])


class ReaderHandoffTests(unittest.TestCase):
    def test_original_dependency_is_preserved_but_only_s3_submitted(self):
        original = checkpoint(); before = deepcopy(original)
        handoff = build_handoff(original, 'https://github.com/pypa/sampleproject')
        self.assertEqual(original, before)
        self.assertEqual(handoff['dependentTaskBeforeBinding']['dependsOn'], ['S2'])
        self.assertEqual(handoff['boundTaskBeforeDispatch']['input']['paperNotes'], [note()])
        self.assertEqual(handoff['plan']['tasks'][0]['dependsOn'], [])
        self.assertEqual(handoff['previousReaderAipTaskId'], 'offline-previous-reader')
        selected_plan(handoff['plan'])

    def test_checkpoint_result_digest_change_rejected(self):
        value = checkpoint(); value['readerRecord']['result']['summary'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'): build_handoff(value, 'https://github.com/pypa/sampleproject')

    def test_manifest_detects_modified_source_and_never_rewrites_it(self):
        from hashlib import sha256
        with TemporaryDirectory() as directory:
            p = Path(directory) / 'source.json'; p.write_text('original', encoding='utf-8')
            manifest = [{'path': str(p), 'sha256': sha256(p.read_bytes()).hexdigest()}]
            verify_manifest(manifest); p.write_text('changed', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'): verify_manifest(manifest)
            self.assertEqual(p.read_text(encoding='utf-8'), 'changed')

    def test_reader_envelope_product_must_equal_saved_result(self):
        result = checkpoint()['readerRecord']['result']
        response = {'aipMessages': [{'response': {'result': {'products': [
            {'dataItems': [{'type': 'data', 'data': result}]}]}}}]}
        check_reader_products(response, result)
        with self.assertRaisesRegex(ValueError, 'PRODUCT_MISMATCH'): check_reader_products(response, {'changed': True})
        with self.assertRaisesRegex(ValueError, 'PRODUCT_REQUIRED'): check_reader_products({'aipMessages': []}, result)


class HandoffAipTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_context_is_transmitted_in_seven_field_aip_task(self):
        plan = build_handoff(checkpoint(), 'https://github.com/pypa/sampleproject')['plan']
        value = payload(); result = attach_comparison(static_result(), value, [note()])
        calls = []
        async def execute(task): calls.append(deepcopy(task)); return result
        aic = 'offline-paper-repo-' + str(uuid4())
        app = create_local_business_app(execute=execute, partner_aic=aic,
                         mode='local-repository-live', background_execution=True)
        with patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('external network forbidden')):
            outcome = await invoke_repository(plan, 'http://127.0.0.1/rpc', aic, transport=httpx.ASGITransport(app=app))
        self.assertTrue(outcome['executionRecords'][0]['succeeded'])
        self.assertEqual(calls, [value]); self.assertEqual(len(calls[0]), 7)
        self.assertEqual(calls[0]['input']['paperNotes'], [note()])
        self.assertNotIn('dependsOn', calls[0]); self.assertNotIn('requiredSkill', calls[0])
        backend = {'scope': 'ISOLATED_CODEWIKI_STATIC_NO_LLM', 'phase': 'completed', 'task': value, 'result': result,
                   'error': None, 'diagnostics': {'llmEnabled': False, 'modelCalls': 0, 'pythonNetworkAttemptsBlocked': 0,
                   'targetCodeExecuted': False, 'targetDependenciesInstalled': False, 'paperContextCompared': True}}
        acceptance = assess(outcome, backend, value)
        self.assertTrue(acceptance['passed']); self.assertTrue(acceptance['paperContextCompared'])
        self.assertFalse(acceptance['semanticReproductionAnalysis'])
        self.assertIn('不证明算法实现', render_report(outcome, acceptance))


if __name__ == '__main__': unittest.main()
