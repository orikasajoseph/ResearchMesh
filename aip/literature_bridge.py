"""ResearchMesh Task v0.1 <-> Literature Agent v0.1, without new wire fields.

Local-only first-stage backend for the existing official AIP Partner.
Real network search is performed only when execute_literature() runs with
its default backend. Offline tests supply a fake invocation.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from .wu_delivery.contracts import ResearchTask, ResearchResult
from .wu_delivery.execution import BackendFailure


def build_literature_request(task: ResearchTask) -> dict[str, Any]:
    """Make the dedicated frozen LiteratureSearchRequest payload.

    AIP taskId/sessionId and Planner requiredSkill/dependsOn are intentionally
    excluded. The v0.1 plan puts query in input and maxResults in constraints.
    """
    if task.taskType != 'literature_search' or task.outputSchema != 'paper_records.v1':
        raise ValueError('ONLY_LITERATURE_SEARCH_SUPPORTED')
    lower = task.constraints.get('yearFrom')
    upper = task.constraints.get('yearTo')
    for year in (lower, upper):
        if year is not None and (isinstance(year, bool) or not isinstance(year, int) or year < 1800 or year > 2200):
            raise ValueError('INVALID_YEAR_FILTER')
    if lower is not None and upper is not None and lower > upper:
        raise ValueError('INVALID_YEAR_RANGE')
    max_results = task.constraints.get('maxResults', 5)
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 100:
        raise ValueError('INVALID_MAX_RESULTS')
    year = (f'{lower or ""}-{upper or ""}' if lower != upper else str(lower)) if (lower is not None or upper is not None) else None
    return {
        'schemaVersion': 'literature_search.v0.1',
        'requestId': task.subtaskId,
        'query': task.input['query'],
        'filters': {'year': year},
        'limit': max_results,
    }


def _repository_url(raw: str) -> bool:
    """Temporary safety filter: CodeWiki's first phase only accepts GitHub repo URLs."""
    if not isinstance(raw, str):
        return False
    parsed = urlparse(raw)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username:
        return False
    parts = [part for part in parsed.path.strip('/').split('/') if part]
    return len(parts) == 2 and all(part not in ('.', '..') for part in parts)


def map_literature_response(task: ResearchTask, response: Any) -> ResearchResult:
    """Map PaperRecordsResponse into ResearchMesh's seven-field result envelope.

    Normalized provider errors become safe AIP backend failures, except
    NO_RESULTS, which represents a completed search with an empty set.
    """
    payload = response.model_dump(mode='json') if hasattr(response, 'model_dump') else response
    if not isinstance(payload, dict) or payload.get('schemaVersion') != 'paper_records.v1':
        raise BackendFailure('LITERATURE_INVALID_RESPONSE')
    if payload.get('requestId') != task.subtaskId:
        raise BackendFailure('LITERATURE_REQUEST_ID_MISMATCH')
    error = payload.get('error') or {}
    status = payload.get('status')
    if status == 'failure':
        code = error.get('code')
        if code == 'NO_RESULTS':
            result = ResearchResult(
                schemaVersion=task.schemaVersion, subtaskId=task.subtaskId,
                taskType=task.taskType, summary='检索执行完成，但没有找到匹配的论文。',
                items=[], warnings=['NO_RESULTS'],
            )
            return result
        # Never pass provider error messages or secret-bearing URLs into AIP logs.
        allowed = {'RATE_LIMITED', 'PROVIDER_UNAVAILABLE', 'INVALID_REQUEST'}
        raise BackendFailure('LITERATURE_' + (code if code in allowed else 'PROVIDER_FAILED'))
    if status != 'success' or error:
        raise BackendFailure('LITERATURE_INVALID_RESPONSE')
    papers = payload.get('papers')
    if not isinstance(papers, list) or not papers:
        raise BackendFailure('LITERATURE_INVALID_RESPONSE')
    items = []
    warnings = []
    for paper in papers:
        if not isinstance(paper, dict):
            raise BackendFailure('LITERATURE_INVALID_PAPER')
        paper = dict(paper)
        # Current frozen Wrapper sometimes puts arXiv/PDF links here. Do not
        # feed these to CodeWiki as evidence of a GitHub repository.
        original = paper.get('repositoryUrls') or []
        if not isinstance(original, list):
            raise BackendFailure('LITERATURE_INVALID_PAPER')
        vetted = list(dict.fromkeys(link for link in original if _repository_url(link)))
        if len(vetted) != len(original) and 'UNVERIFIED_REPOSITORY_URLS_DROPPED' not in warnings:
            warnings.append('UNVERIFIED_REPOSITORY_URLS_DROPPED')
        paper['repositoryUrls'] = vetted
        if not isinstance(paper.get('title'), str) or not paper['title'].strip():
            raise BackendFailure('LITERATURE_MISSING_TITLE')
        if not (paper.get('doi') or paper.get('url') or paper.get('pdfUrl')):
            raise BackendFailure('LITERATURE_MISSING_LOCATOR')
        items.append({'kind': 'paper', 'data': paper})
    return ResearchResult(
        schemaVersion=task.schemaVersion, subtaskId=task.subtaskId,
        taskType=task.taskType, summary=f'通过 Semantic Scholar 检索到 {len(items)} 篇论文。',
        items=items, warnings=warnings,
    )


async def execute_literature(
    task: ResearchTask,
    *,
    invoke: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
) -> ResearchResult:
    """The async executor injected into create_local_app(execute=...).

    Tests inject a fake invoke(payload). Production-local invocation uses
    Zhang's frozen Pydantic models and async LiteratureAgent.
    """
    request_payload = build_literature_request(task)
    if invoke is None:
        from agents.literature.researchmesh_literature import LiteratureAgent, LiteratureSearchRequest
        request = LiteratureSearchRequest.model_validate(request_payload)
        response = await LiteratureAgent().literature_search(request)
    else:
        response = await invoke(request_payload)
    return map_literature_response(task, response)
