"""Explicit live Literature-only local Partner factory; no LLM or Reader.

Imported safely without reading keys; uvicorn --factory calls create_live_app
only from the user's explicit run_aip_literature.py --execute workflow.
"""
from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path

from aip_business_partner import create_local_business_app, LocalBackendFailure, validate_local_task
from literature_adapter import AdapterError, to_wrapper_request
from run_literature import execute_live_task, prepare_live_wrapper
from aip_record_paths import require_record_directory

ROOT = Path(__file__).resolve().parent


def make_literature_executor(wrapper_root, *, prepared=None, on_outcome=None, retry=True):
    async def execute(payload):
        validate_local_task(payload)
        if payload["taskType"] != "literature_search":
            raise LocalBackendFailure("INVALID_REQUEST")
        to_wrapper_request(payload, "precheck")
        try:
            outcome = await execute_live_task(payload, wrapper_root, prepared=prepared,
                                              request_source="aip-literature-partner", retry=retry)
        except asyncio.CancelledError:
            if on_outcome is not None:
                on_outcome({"result": None, "error": {"code": "LITERATURE_BACKEND_CANCELED"},
                            "diagnostics": {"stage": "literature_backend", "providerResultKnown": False}})
            raise
        except AdapterError as error:
            outcome = {"result": None, "error": {"code": error.code}, "diagnostics": {}}
        except Exception as error:
            outcome = {"result": None, "error": {"code": "LITERATURE_RUNTIME_ERROR"},
                       "diagnostics": {"exceptionType": type(error).__name__}}
        if on_outcome is not None:
            on_outcome(deepcopy(outcome))
        if outcome["error"] is not None:
            raise LocalBackendFailure(outcome["error"]["code"])
        return outcome["result"]
    return execute


def create_live_app():
    """Called only in an owned loopback child, keys never passed on command line."""
    root = Path(os.environ["RESEARCHMESH_AIP_WRAPPER_ROOT"]).resolve()
    # The parent owns either a legacy run or a nested single-entry stage.
    output = require_record_directory(os.environ["RESEARCHMESH_AIP_RECORD_DIR"], ROOT, "literature")
    prepared = prepare_live_wrapper(root)  # no network, only local Key and imports
    def save(outcome):
        (output / "backend_outcome.json").write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    single_request = os.environ.get("RESEARCHMESH_S1_SINGLE_REQUEST") == "1"
    execute = make_literature_executor(root, prepared=prepared, on_outcome=save,
                                       retry=not single_request)
    return create_local_business_app(execute=execute,
                                    partner_aic=os.environ["RESEARCHMESH_LOCAL_PARTNER_AIC"],
                                    backend_timeout_seconds=120, background_execution=True,
                                    mode="local-literature-live")
