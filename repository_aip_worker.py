"""Isolated CodeWiki worker: no dotenv, LLM, Python network, or target execution."""
import argparse
from contextlib import contextmanager
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

from aip_record_paths import require_record_directory
from repository_adapter import load_repository, to_repository_request, normalize_repository_result
from repository_paper_context import prepare_repository_task, attach_comparison

ROOT = Path(__file__).resolve().parent
PIN = 'f0975ea19b17568ceb55cd5422206e1a1ae13c0b'


@contextmanager
def static_only_guard(observations):
    """Git child may clone HTTPS; Python providers must never contact a model/API."""
    def denied(*args, **kwargs):
        observations['pythonNetworkAttemptsBlocked'] += 1
        raise RuntimeError('STATIC_WORKER_PYTHON_NETWORK_FORBIDDEN')
    with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), \
            patch.object(socket, 'create_connection', denied):
        yield


def runtime_check(wrapper_root):
    if sys.version_info[:2] != (3, 12):
        raise ValueError('REPOSITORY_PYTHON_312_REQUIRED')
    distribution = metadata.distribution('codewiki')
    source = json.loads(distribution.read_text('direct_url.json') or '{}')
    if distribution.version != '2.0.0' or source.get('vcs_info', {}).get('commit_id') != PIN:
        raise ValueError('PINNED_CODEWIKI_REQUIRED')
    wrapper = load_repository(wrapper_root)
    # These are the exact no-LLM entrypoints used by Zhang's provider.
    from codewiki.mcp.tools.analysis import handle_analyze_repo
    from codewiki.mcp.session import SessionStore
    return wrapper


def run(args):
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TIKTOKEN_CACHE_DIR'] = str(Path(sys.prefix) / 'runtime-cache/tiktoken')
    output = require_record_directory(args.record_dir, ROOT, 'repository')
    observations = {'llmEnabled': False, 'modelCalls': 0, 'pythonNetworkAttemptsBlocked': 0,
                    'targetCodeExecuted': False, 'targetDependenciesInstalled': False}
    journal = {'scope': 'ISOLATED_CODEWIKI_STATIC_NO_LLM', 'phase': 'preflight',
               'task': None, 'result': None, 'error': None, 'diagnostics': observations}
    path = output / ('backend_outcome.json' if args.execute else 'repository_preflight.json')
    try:
        payload = json.loads((output / 'repository_input.json').read_text(encoding='utf-8'))
        static_payload, request, paper_notes = prepare_repository_task(payload)
        journal['task'] = payload
        with static_only_guard(observations):
            wrapper = runtime_check(args.wrapper_root)
            native = wrapper.RepositoryAnalyzeRequest.model_validate(request)
            if args.execute:
                journal['phase'] = 'running'
                path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
                # Double guard: enrichment disabled and any accidental enrichment is an error.
                def forbidden_enrichment(*args, **kwargs):
                    observations['modelCalls'] += 1  # attempted, blocked before any provider
                    raise RuntimeError('LLM_FORBIDDEN')
                with patch.object(wrapper.RepositoryEnricher, 'enrich', side_effect=forbidden_enrichment):
                    agent = wrapper.RepositoryAgent(enrichment_enabled=False)
                    raw = agent.analyze(native)
                observations['gitStages'] = list(getattr(agent, 'git_diagnostics', []))
                if not raw.items:
                    codes = ('REPOSITORY_CLONE_FAILED', 'STATIC_ANALYSIS_FAILED', 'INVALID_REPOSITORY_URL', 'REPOSITORY_UNAVAILABLE')
                    raise ValueError(next((code for code in codes if any(w.startswith(code) for w in raw.warnings)), 'REPOSITORY_EMPTY_RESULT'))
                result = normalize_repository_result(static_payload, raw, wrapper)
                result = attach_comparison(result, payload, paper_notes)
                observations['paperContextCompared'] = bool(paper_notes)
                metrics = result['items'][0]['data']['diagnostics']['metrics']
                if (observations['modelCalls'] or observations['pythonNetworkAttemptsBlocked'] or
                        metrics['llmEnrichmentMs'] != 0 or result['items'][0]['data']['overview'] is not None):
                    raise ValueError('UNEXPECTED_LLM_ENRICHMENT')
                journal.update(phase='completed', result=result)
            else:
                journal.update(phase='preflight-completed', passed=True, codewikiCommit=PIN)
    except Exception as error:
        allowed = {'REPOSITORY_PYTHON_312_REQUIRED', 'PINNED_CODEWIKI_REQUIRED', 'REPOSITORY_CLONE_FAILED',
                   'STATIC_ANALYSIS_FAILED', 'INVALID_REPOSITORY_URL', 'REPOSITORY_UNAVAILABLE',
                   'REPOSITORY_EMPTY_RESULT', 'UNEXPECTED_LLM_ENRICHMENT'}
        code = getattr(error, 'code', None) or (str(error) if str(error) in allowed else 'REPOSITORY_WORKER_FAILED')
        journal.update(phase='failed', error={'code': code, 'exceptionType': type(error).__name__})
    path.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Repository worker:', journal['phase'], (journal['error'] or {}).get('code', ''))
    return 0 if journal['error'] is None else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-dir', type=Path, required=True)
    parser.add_argument('--wrapper-root', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    raise SystemExit(run(parser.parse_args()))
