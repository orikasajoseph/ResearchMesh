"""Real CodeWiki static smoke + fake enrichment. Never a live API acceptance.

Run with .venv-repository Python. Generates only a temporary tiny local fixture;
does not git clone, download PDFs, read .env, execute target code or call an API.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from repository_adapter import normalize_repository_result
from repository_aip_worker import static_only_guard, runtime_check
from repository_enhancement import prepare_enhanced_request, enrich_static_result


def main():
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TIKTOKEN_CACHE_DIR'] = str(Path(sys.prefix) / 'runtime-cache/tiktoken')
    payload = {'schemaVersion': '0.2', 'subtaskId': 'offline-S3', 'taskType': 'repository_analyze',
               'goal': 'Offline local CodeWiki and fake enhancement regression, not scientific evidence.',
               'input': {'repositoryUrls': ['https://github.com/pypa/sampleproject']},
               'constraints': {'maxRepositories': 1, 'executeCode': False, 'allowRepositorySearch': False,
                               'requirePaperNotes': False}, 'outputSchema': 'code_records.v1'}
    observed = {'llmEnabled': True, 'modelCalls': 0, 'successfulModelResponses': 0,
                'modelUsage': [], 'pythonNetworkAttemptsBlocked': 0,
                'targetCodeExecuted': False, 'targetDependenciesInstalled': False}
    fake_calls = []
    class FakeProvider:
        def generate_json(self, messages, *, schema=None):
            # These are fake provider calls, deliberately not added to modelCalls.
            fake_calls.append(True)
            bundle = json.loads(messages[0]['content'].split('Evidence bundle:\n')[1])
            eid = bundle['evidence'][0]['evidenceId']
            return {'overview': {'text': 'Offline fake summary of local fixture.', 'evidenceIds': [eid]},
                    'reproductionBarriers': []}
    with TemporaryDirectory(prefix='rm_dual_static_') as directory:
        root = Path(directory)
        (root / 'main.py').write_text('from service import calculate\n\ndef main():\n    return calculate(2, 3)\n', encoding='utf-8')
        (root / 'service.py').write_text('def calculate(a, b):\n    return a + b\n', encoding='utf-8')
        (root / 'pyproject.toml').write_text('[project]\nname="tiny"\nversion="0.1"\n[project.scripts]\ntiny-repo="main:main"\n', encoding='utf-8')
        (root / 'README.md').write_text('# Offline fixture\nStatic parser test only.\n', encoding='utf-8')
        with static_only_guard(observed):
            wrapper = runtime_check(ROOT / 'agents/repository')
            static_payload, request = prepare_enhanced_request(payload, wrapper)
            raw = wrapper.RepositoryAgent(enrichment_enabled=False).analyze_local(
                request, root, repository_url=payload['input']['repositoryUrls'][0], commit_hash='a' * 40)
            if not raw.items: raise RuntimeError('STATIC_SMOKE_FAILED')
            static_result = normalize_repository_result(static_payload, raw, wrapper)
            enriched = enrich_static_result(payload, raw, wrapper, lambda obs: FakeProvider(), observed)
        before, after = static_result['items'][0]['data'], enriched['items'][0]['data']
        checks = {'realCodeWikiStaticComponents': before['diagnostics']['counts']['componentCount'] > 0,
                  'dependencyParsed': bool(before['dependencies']),
                  'entrypointParsed': any(e['name'] == 'tiny-repo' for e in before['entrypoints']),
                  'staticModeNoOverview': before['overview'] is None,
                  'fakeEnhancementAddedOverview': after['overview'] is not None,
                  'staticFactsUnchanged': all(before[k] == after[k] for k in ('structure', 'dependencies', 'entrypoints')),
                  # Normalization legitimately adds model claims to supports. The
                  # underlying source IDs, locations and source text must not change.
                  'evidenceSourceFragmentsUnchanged': [
                      {k: e[k] for k in ('sourceId', 'url', 'locator', 'text')} for e in before['evidence']
                  ] == [{k: e[k] for k in ('sourceId', 'url', 'locator', 'text')} for e in after['evidence']],
                  'noExternalNetworkAttempts': observed['pythonNetworkAttemptsBlocked'] == 0,
                  'noRealModelCalls': observed['modelCalls'] == 0,
                  'oneFakeProviderCall': len(fake_calls) == 1}
    output = ROOT / 'outputs' / ('repository-dual-smoke-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    record = {'passed': all(checks.values()), 'scope': 'OFFLINE_REAL_CODEWIKI_LOCAL_FIXTURE_FAKE_LLM',
              'checks': checks, 'realModelCalls': 0, 'fakeProviderCalls': len(fake_calls),
              'realRepositoryCloned': False, 'platformUsed': False, 'liveEnhancementVerified': False}
    (output / 'acceptance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2)); print('记录：', output)
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
