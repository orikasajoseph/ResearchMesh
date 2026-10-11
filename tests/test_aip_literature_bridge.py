"""Offline, no API Key/SDK/network required: frozen Literature v0.1 mapping tests."""
import asyncio
import json
from pathlib import Path
import unittest

from aip.literature_bridge import build_literature_request, execute_literature, map_literature_response
from aip.wu_delivery.contracts import ResearchTask
from aip.wu_delivery.execution import BackendFailure
from result_contract import validate_result

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'agents' / 'literature' / 'examples'

def task(constraints=None):
    return ResearchTask.model_validate({
        'schemaVersion': '0.1', 'subtaskId': 'S1', 'taskType': 'literature_search',
        'goal': 'find papers', 'input': {'query': 'AlphaEvolve'},
        'constraints': constraints or {}, 'outputSchema': 'paper_records.v1',
    })

def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))

class LiteratureBridgeTests(unittest.TestCase):
    def test_request_mapping_and_range(self):
        request = build_literature_request(task({'yearFrom': 2023, 'yearTo': 2026, 'maxResults': 3}))
        self.assertEqual(request['query'], 'AlphaEvolve')
        self.assertEqual(request['filters']['year'], '2023-2026')
        self.assertEqual(request['limit'], 3)
        self.assertEqual(request['schemaVersion'], 'literature_search.v0.1')
        self.assertNotIn('taskId', request)

    def test_bad_limit_rejected(self):
        with self.assertRaisesRegex(ValueError, 'MAX_RESULTS'):
            build_literature_request(task({'maxResults': True}))

    def test_success_fixture_and_validation(self):
        search_task = task()
        response = fixture('literature_success_output.json')
        response['requestId'] = search_task.subtaskId
        result = map_literature_response(search_task, response)
        self.assertTrue(result.items)
        self.assertTrue(all(item.kind == 'paper' for item in result.items))
        self.assertTrue(all(not any(url.startswith('https://arxiv.org') for url in item.data['repositoryUrls']) for item in result.items))
        validate_result(result.model_dump(mode='json'), search_task.model_dump(mode='json'))

    def test_empty_fixture_is_not_transport_only_or_provider_error(self):
        search_task = task()
        response = fixture('literature_empty_output.json')
        response['requestId'] = search_task.subtaskId
        result = map_literature_response(search_task, response)
        self.assertEqual(result.items, [])
        self.assertEqual(result.warnings, ['NO_RESULTS'])
        self.assertNotIn('TRANSPORT_TEST_ONLY', result.warnings)
        validate_result(result.model_dump(mode='json'), search_task.model_dump(mode='json'))

    def test_provider_error_preserves_safe_code(self):
        response = {'schemaVersion': 'paper_records.v1', 'requestId': 'S1', 'status': 'failure',
                    'error': {'code': 'RATE_LIMITED', 'message': 'secret/API_KEY=123'}}
        with self.assertRaisesRegex(BackendFailure, '^LITERATURE_RATE_LIMITED$'):
            map_literature_response(task(), response)

    def test_fake_async_executor(self):
        async def mock_invoke(payload):
            self.assertEqual(payload['requestId'], 'S1')
            response = fixture('literature_success_output.json')
            response['requestId'] = 'S1'
            return response
        result = asyncio.run(execute_literature(task(), invoke=mock_invoke))
        self.assertTrue(result.items)

    def test_wrong_correlation_rejected(self):
        response = fixture('literature_success_output.json')
        with self.assertRaisesRegex(BackendFailure, 'REQUEST_ID_MISMATCH'):
            map_literature_response(task(), response)

if __name__ == '__main__':
    unittest.main()
