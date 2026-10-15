"""Zhao-side Repository seam; unchanged Zhang Wrapper and frozen business v0.2.

Based on Wu's 58e4e5d mapping: native repository_record -> repository. This
adapter additionally preserves concrete claims, validates correlation and never
silently sends unsupported paperNotes to a backend that cannot consume them.
"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import sys
from urllib.parse import quote

from common.models import BusinessTask, PlannerTask, BUSINESS_TASK_FIELDS
from input_binding import github_repository_url, validate_task_input
from result_contract import validate_result


class RepositoryAdapterError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def to_repository_request(payload):
    """Normalize an already bound task; do not perform discovery or clone."""
    try:
        model = PlannerTask if isinstance(payload, dict) and ({'requiredSkill', 'dependsOn'} & payload.keys()) else BusinessTask
        task = model.model_validate(payload).model_dump()
        if task['taskType'] != 'repository_analyze' or task['outputSchema'] != 'code_records.v1':
            raise RepositoryAdapterError('UNSUPPORTED_REPOSITORY_TASK')
        if model is PlannerTask and task['requiredSkill'] != 'repository.analyze':
            raise RepositoryAdapterError('REPOSITORY_SKILL_MISMATCH')
        validate_task_input(task, dispatch=True)
        if set(task['constraints']) - {'maxRepositories', 'executeCode', 'allowRepositorySearch', 'requirePaperNotes'}:
            raise RepositoryAdapterError('UNSUPPORTED_REPOSITORY_CONSTRAINT')
        if task['constraints'].get('maxRepositories', 1) != 1 or len(task['input']['repositoryUrls']) != 1:
            raise RepositoryAdapterError('SINGLE_REPOSITORY_REQUIRED')
        # be99b31 only accepts repositoryUrls; it cannot use papers/paperNotes.
        if task['constraints'].get('requirePaperNotes') or task['input'].get('papers') or task['input'].get('paperNotes'):
            raise RepositoryAdapterError('REPOSITORY_PAPER_CONTEXT_NOT_SUPPORTED')
        result = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
        result['input'] = {'repositoryUrls': list(task['input']['repositoryUrls'])}
        result['constraints'] = {'maxRepositories': 1, 'executeCode': False}
        return result
    except RepositoryAdapterError:
        raise
    except (ValueError, TypeError, KeyError):
        raise RepositoryAdapterError('INVALID_REPOSITORY_TASK') from None


def load_repository(root):
    """Load only the explicitly selected external package, no dependency install."""
    package = Path(root).resolve() / 'researchmesh_repository'
    init = package / '__init__.py'
    if not init.is_file():
        raise RepositoryAdapterError('REPOSITORY_ROOT_NOT_FOUND')
    name = '_researchmesh_zhang_repository'
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(existing.__file__).resolve() != init:
            raise RepositoryAdapterError('REPOSITORY_ALREADY_LOADED_FROM_OTHER_ROOT')
        return existing
    spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        for key in list(sys.modules):
            if key == name or key.startswith(name + '.'):
                sys.modules.pop(key, None)
        raise RepositoryAdapterError('REPOSITORY_IMPORT_FAILED') from None
    return module


def _repo_identity(url):
    if not github_repository_url(url):
        raise RepositoryAdapterError('INVALID_REPOSITORY_URL')
    return url.rstrip('/').removesuffix('.git').lower()


def evidence_url(url, commit, path, start=None, end=None):
    _repo_identity(url)
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-fA-F]{40}', commit):
        raise RepositoryAdapterError('INVALID_REPOSITORY_COMMIT')
    if not isinstance(path, str) or not path:
        raise RepositoryAdapterError('INVALID_REPOSITORY_EVIDENCE_PATH')
    path = path.replace('\\', '/')
    if (any(c in path for c in ':?%#') or path.startswith('/') or
        any(part in ('', '.', '..') for part in path.split('/')) or any(ord(c) < 32 for c in path)):
        raise RepositoryAdapterError('INVALID_REPOSITORY_EVIDENCE_PATH')
    if ((start is not None and (type(start) is not int or start < 1)) or
        (end is not None and (start is None or type(end) is not int or end < start))):
        raise RepositoryAdapterError('INVALID_REPOSITORY_EVIDENCE_LINES')
    anchor = '' if start is None else '#L' + str(start) + ('-L' + str(end) if end is not None and end > start else '')
    return url.rstrip('/').removesuffix('.git') + '/blob/' + commit + '/' + quote(path, safe='/') + anchor


def normalize_repository_result(payload, raw, wrapper, *, fixture=False):
    """Native result is copied. Fixture cannot be a real research PASS."""
    if type(fixture) is not bool:
        raise RepositoryAdapterError('INVALID_REPOSITORY_FIXTURE_MODE')
    native_request = to_repository_request(payload)
    try:
        raw = raw.model_dump(mode='json') if hasattr(raw, 'model_dump') else deepcopy(raw)
        native = wrapper.RepositoryAnalyzeResult.model_validate(raw).model_dump(mode='json')
        if any(native[key] != native_request[key] for key in ('schemaVersion', 'subtaskId', 'taskType')):
            raise RepositoryAdapterError('REPOSITORY_RESULT_CORRELATION_MISMATCH')
        if len(native['items']) != 1:
            raise RepositoryAdapterError('REPOSITORY_RESULT_UNAVAILABLE')
        record = native['items'][0]['data']
        identity = record['repository']
        if _repo_identity(identity['url']) != _repo_identity(native_request['input']['repositoryUrls'][0]):
            raise RepositoryAdapterError('REPOSITORY_RESULT_URL_MISMATCH')
        ids = [e['evidenceId'] for e in record['evidence']]
        if not ids or any(not x.strip() for x in ids) or len(set(ids)) != len(ids):
            raise RepositoryAdapterError('INVALID_REPOSITORY_EVIDENCE_IDS')
        claims = {}
        for claim in ([record['overview']] if record['overview'] else []) + record['reproductionBarriers']:
            if not claim['evidenceIds'] or any(x not in ids for x in claim['evidenceIds']):
                raise RepositoryAdapterError('UNKNOWN_REPOSITORY_CLAIM_EVIDENCE')
            for eid in claim['evidenceIds']:
                claims.setdefault(eid, []).append(claim.get('text', claim.get('issue')))
        evidence = []
        if not fixture:
            for e in record['evidence']:
                url = evidence_url(identity['url'], identity['commitHash'], e['filePath'], e['startLine'], e['endLine'])
                locator = e['filePath'] + (':L' + str(e['startLine']) if e['startLine'] is not None else '')
                supports = '; '.join(dict.fromkeys(claims.get(e['evidenceId'], []))) or (
                    f"Static source fragment extracted from {locator}" + (f", symbol {e['symbol']}" if e['symbol'] else '') +
                    '; target code was not executed and reproduction was not verified.')
                evidence.append({'sourceId': e['evidenceId'], 'url': url, 'locator': locator,
                                 'supports': supports, 'text': e['text']})
        data = deepcopy(record)
        data.update(url=identity['url'], license='UNKNOWN', isMock=fixture, evidence=evidence,
                    relationEvidence='Explicit repository input only; paper/repository relationship has not been independently verified.')
        result = {key: deepcopy(native[key]) for key in ('schemaVersion', 'subtaskId', 'taskType', 'summary', 'warnings')}
        result['items'] = [{'kind': 'repository', 'data': data}]
        result['warnings'] += ['LICENSE_NOT_VERIFIED', 'PAPER_REPOSITORY_RELATION_NOT_VERIFIED']
        if fixture:
            result['warnings'].append('SIMULATED_RESEARCH: actual Wrapper with fake static/clone providers; no real analysis')
        result['warnings'] = list(dict.fromkeys(result['warnings']))
        validate_result(result, payload)
        return result
    except RepositoryAdapterError:
        raise
    except (ValueError, TypeError, KeyError):
        raise RepositoryAdapterError('INVALID_REPOSITORY_RESULT') from None
