"""Opt-in two-phase worker: guarded static facts, then bounded shared LLM.

The original static-only worker remains untouched. This worker must be selected
explicitly; --execute alone in the main CLI still means static-only.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from aip_record_paths import require_record_directory
from repository_adapter import normalize_repository_result
from repository_aip_worker import runtime_check, static_only_guard, PIN
from repository_enhancement import prepare_enhanced_request, enrich_static_result
from repository_paper_context import prepare_repository_task

ROOT = Path(__file__).resolve().parent
SCOPE = 'ISOLATED_CODEWIKI_WITH_EXPLICIT_LLM_ENHANCEMENT'


def live_provider(observations):
    """At most two actual HTTP attempts (JSON repair only), no network retries.

Token usage covers successful HTTP responses exposed by shared LLMProvider;
failed/timed-out requests may be billed without returning usage. No cost claim.
"""
    from common.llm.researchmesh_llm import LLMProvider
    from common.llm.researchmesh_llm.config import LLMConfig

    class CountedProvider(LLMProvider):
        def _chat(self, *args, **kwargs):
            if observations['modelCalls'] >= 2:
                raise RuntimeError('MODEL_CALL_BUDGET_EXHAUSTED')
            observations['modelCalls'] += 1
            try:
                response = super()._chat(*args, **kwargs)
            except Exception as error:
                # Shared generate_json retries LLMResponseError, including network
                # failures. Only an empty response is allowed that lightweight retry.
                from common.llm.researchmesh_llm.exceptions import LLMResponseError
                if isinstance(error, LLMResponseError) and str(error) in {
                        'The model returned empty content', 'The model returned no choices'}:
                    raise
                raise RuntimeError('MODEL_REQUEST_FAILED_NO_AUTOMATIC_RETRY') from None
            observations['successfulModelResponses'] += 1
            if response.usage is not None:
                observations['modelUsage'].append(response.usage.model_dump(mode='json'))
            return response

    config = LLMConfig(api_key=os.environ.get('DEEPSEEK_API_KEY', ''),
                       base_url=os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
                       model=os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'),
                       timeout_seconds=60, max_retries=0)
    return CountedProvider(config=config)


def run(args):
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    output = require_record_directory(args.record_dir, ROOT, 'repository')
    observations = {'llmEnabled': True, 'modelCalls': 0, 'successfulModelResponses': 0,
                    'modelUsage': [], 'usageMayExcludeFailedRequests': True,
                    'pythonNetworkAttemptsBlocked': 0, 'targetCodeExecuted': False,
                    'targetDependenciesInstalled': False, 'enrichmentStatus': 'not_started'}
    journal = {'scope': SCOPE, 'phase': 'preflight', 'task': None, 'result': None,
               'error': None, 'diagnostics': observations}
    path = output / ('backend_outcome.json' if args.execute else 'repository_preflight.json')
    try:
        payload = json.loads((output / 'repository_input.json').read_text(encoding='utf-8'))
        journal['task'] = payload
        static_payload, _, _ = prepare_repository_task(payload)
        with static_only_guard(observations):
            wrapper = runtime_check(args.wrapper_root)
            _, request = prepare_enhanced_request(payload, wrapper)
            if importlib.util.find_spec('openai') is None:
                raise ValueError('ENHANCED_LLM_DEPENDENCY_MISSING')
            if not args.execute:
                journal.update(phase='preflight-completed', passed=True, codewikiCommit=PIN,
                               keyChecked=False)
            else:
                journal['phase'] = 'running-static'
                path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
                def forbidden(*a, **kw):
                    raise RuntimeError('LLM_FORBIDDEN_IN_STATIC_PHASE')
                with patch.object(wrapper.RepositoryEnricher, 'enrich', side_effect=forbidden):
                    raw = wrapper.RepositoryAgent(enrichment_enabled=False).analyze(request)
                if not raw.items:
                    codes = ('REPOSITORY_CLONE_FAILED', 'STATIC_ANALYSIS_FAILED', 'INVALID_REPOSITORY_URL', 'REPOSITORY_UNAVAILABLE')
                    raise ValueError(next((code for code in codes if any(w.startswith(code) for w in raw.warnings)), 'REPOSITORY_EMPTY_RESULT'))
                if observations['pythonNetworkAttemptsBlocked']:
                    raise ValueError('STATIC_PHASE_NETWORK_ATTEMPT')
        if args.execute:
            # Static result checkpoint survives a paid-phase timeout/interruption.
            static_raw = raw.model_copy(deep=True)
            static_raw.items[0].data.paperEvidence = []
            static_result = normalize_repository_result(static_payload, static_raw, wrapper)
            (output / 'static_checkpoint.json').write_text(json.dumps({
                'scope': 'STATIC_CHECKPOINT_BEFORE_OPTIONAL_LLM', 'result': static_result,
                'modelCalls': 0, 'notAipCompletedProof': True}, ensure_ascii=False, indent=2), encoding='utf-8')
            journal.update(phase='running-enhancement', result=static_result)
            path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
            try:
                if args.env_file:
                    from dotenv import dotenv_values
                    env_file = args.env_file.resolve()
                    if not env_file.is_file():
                        raise ValueError('ENHANCED_ENV_FILE_MISSING')
                    # Only permit LLM settings, never copy all secrets into the worker.
                    for key, value in dotenv_values(env_file, interpolate=False).items():
                        if key in {'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL'} and value:
                            os.environ[key] = value
                provider_factory = live_provider
            except Exception:
                def provider_factory(observations):
                    raise ValueError('ENHANCED_CONFIGURATION_UNAVAILABLE')
            journal['result'] = enrich_static_result(payload, raw, wrapper, provider_factory, observations)
            journal['phase'] = 'completed'
    except Exception as error:
        allowed = {'REPOSITORY_PYTHON_312_REQUIRED', 'PINNED_CODEWIKI_REQUIRED',
                   'ENHANCED_LLM_DEPENDENCY_MISSING', 'REPOSITORY_CLONE_FAILED',
                   'STATIC_ANALYSIS_FAILED', 'REPOSITORY_EMPTY_RESULT'}
        code = getattr(error, 'code', None) or (str(error) if str(error) in allowed else 'REPOSITORY_WORKER_FAILED')
        journal.update(phase='failed', error={'code': code, 'exceptionType': type(error).__name__})
    path.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Repository enhanced worker:', journal['phase'], (journal['error'] or {}).get('code', ''))
    return 0 if journal['error'] is None else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-dir', type=Path, required=True)
    parser.add_argument('--wrapper-root', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--execute', action='store_true')
    raise SystemExit(run(parser.parse_args()))
