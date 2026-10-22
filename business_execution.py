"""Stable protocol-free boundary for Wu's ACS/ADP/AMQP adapter.

Input is exactly the existing seven-field Task, output exactly the six-field
ResearchResult. No discovery, AIC, Group, protocol status, retry or dedup store
is created here. The platform owns persistent (sessionId, taskId, Agent AIC)
deduplication and injects one of the existing business backends.
"""
import asyncio
from copy import deepcopy
import math

from aip_business_partner import validate_local_task
from aip.literature_audit import task_event
from common.skills import SKILLS
from reader_adapter import normalize_reader_result
from repository_adapter import _repo_identity
from result_contract import validate_result

BACKEND_TIMEOUTS = {"literature.search": 120, "paper.analyze": 660, "repository.analyze": 180}
SAFE_BACKEND_CODES = {"INVALID_REQUEST", "FULL_TEXT_UNAVAILABLE", "RATE_LIMITED", "PROVIDER_UNAVAILABLE",
                      "NO_RESULTS", "BACKEND_UNAVAILABLE", "READER_WORKER_FAILED", "READER_TIMEOUT",
                      "READER_PREVIOUS_EXECUTION_UNCERTAIN", "REPOSITORY_WORKER_FAILED",
                      "REPOSITORY_PREVIOUS_EXECUTION_UNCERTAIN", "REPOSITORY_CLONE_FAILED",
                      "STATIC_ANALYSIS_FAILED", "REPOSITORY_EMPTY_RESULT", "PDF_DOWNLOAD_FAILED",
                      "PDF_SOURCE_UNVERIFIED", "VERIFIED_PDF_CACHE_UNAVAILABLE"}


class BusinessExecutionError(RuntimeError):
    """Fixed error codes only; platform maps these to official AIP, not JSON status."""
    def __init__(self, code, *, remote_execution_uncertain=False):
        self.code = code
        self.remote_execution_uncertain = remote_execution_uncertain
        super().__init__(code)


def validate_business_result(task, result, *, repository_analysis_mode="static"):
    """Correlate standardized output before the platform may complete the task."""
    validate_result(result, task)
    if not result["items"] and task["taskType"] != "literature_search":
        raise ValueError("EMPTY_ANALYSIS_RESULT")
    if any(item["data"].get("isMock") is True for item in result["items"]):
        raise ValueError("MOCK_NOT_LIVE_BUSINESS_RESULT")
    if task["taskType"] == "literature_search":
        if len(result["items"]) > task["constraints"].get("maxResults", 5):
            raise ValueError("SEARCH_RESULT_LIMIT_EXCEEDED")
    elif task["taskType"] == "paper_read":
        normalized = normalize_reader_result(task, result)
        if normalized["error"] or normalized["diagnostics"].get("analysisLevel") != "fulltext":
            raise ValueError("FULLTEXT_RESULT_REQUIRED")
    else:
        if len(result["items"]) != 1:
            raise ValueError("SINGLE_REPOSITORY_RESULT_REQUIRED")
        data = result["items"][0]["data"]
        if _repo_identity(data["url"]) != _repo_identity(task["input"]["repositoryUrls"][0]):
            raise ValueError("REPOSITORY_RESULT_SOURCE_MISMATCH")
        enhancement = data.get("enrichment", {})
        if repository_analysis_mode == "static" and enhancement.get("requested"):
            raise ValueError("REPOSITORY_MODE_MISMATCH")
        if repository_analysis_mode == "enhanced":
            if enhancement.get("requested") is not True:
                raise ValueError("REPOSITORY_MODE_MISMATCH")
            if (enhancement.get("status") == "completed" and task["input"].get("paperNotes")
                    and not data.get("paperCodeRelations")):
                raise ValueError("PAPER_CODE_DUAL_EVIDENCE_REQUIRED")
            if (enhancement.get("status") != "completed" and
                    "LLM_ENRICHMENT_UNAVAILABLE" not in result["warnings"] and
                    enhancement.get("status") != "no_supported_claims"):
                raise ValueError("ENHANCEMENT_FAILURE_WARNING_REQUIRED")


async def execute_business_task(task, *, skill, backend, allow_execution=False,
                                repository_analysis_mode="static", timeout_seconds=None):
    """Platform-facing stable call. Backend must be async and return normalized JSON.

    Creation/import never starts providers. Default execution is denied. No
    retries here, even after timeout/cancel; failure may already have incurred
    charges. Do not construct a new executor for every duplicate platform Start.
    """
    if type(allow_execution) is not bool or not allow_execution:
        raise BusinessExecutionError("BUSINESS_EXECUTION_NOT_AUTHORIZED")
    if repository_analysis_mode not in {"static", "enhanced"} or not callable(backend):
        raise BusinessExecutionError("INVALID_BUSINESS_CONFIGURATION")
    try:
        payload = validate_local_task(deepcopy(task))
    except (ValueError, TypeError, KeyError):
        raise BusinessExecutionError("INVALID_BUSINESS_TASK") from None
    if SKILLS[payload["taskType"]] != skill:
        raise BusinessExecutionError("BUSINESS_SKILL_MISMATCH")
    timeout = timeout_seconds if timeout_seconds is not None else (
        300 if skill == "repository.analyze" and repository_analysis_mode == "enhanced" else BACKEND_TIMEOUTS[skill])
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise BusinessExecutionError("INVALID_BUSINESS_TIMEOUT")

    async def invoke():
        task_event("BACKEND_STARTED")
        try:
            result = await backend(deepcopy(payload))
        except BaseException as error:
            task_event("BACKEND_FINISHED", outcome="cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
            raise
        task_event("BACKEND_FINISHED", outcome="success")
        return result

    try:
        result = await asyncio.wait_for(invoke(), timeout)
    except asyncio.CancelledError:
        # Protocol cancellation is not proof that a remote provider cancelled.
        raise
    except asyncio.TimeoutError:
        raise BusinessExecutionError("BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED", remote_execution_uncertain=True) from None
    except Exception as error:
        code = getattr(error, "code", None)
        raise BusinessExecutionError(code if code in SAFE_BACKEND_CODES else "BUSINESS_BACKEND_FAILED",
                                     remote_execution_uncertain=True) from None
    try:
        validate_business_result(payload, result, repository_analysis_mode=repository_analysis_mode)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise BusinessExecutionError("INVALID_BUSINESS_RESULT", remote_execution_uncertain=True) from None
    return deepcopy(result)
