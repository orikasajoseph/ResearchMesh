"""Resolver + real AIP executor scheduling. Inject transport; never default to Mock."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter

from aip_executor import AipAgentExecutor
from aip_lifecycle import AipExecutionError
from input_binding import bind_task_input, InputBindingError
from orchestrator import LocalOrchestrator, make_task_payload
from resolver.base import NoMatchingAgentError


class AipOrchestrator(LocalOrchestrator):
    """Reuse dependency waves; this class itself does not certify ADP registration."""
    async def _execute(self, task, dependencies):
        record = {"subtaskId": task["subtaskId"], "requiredSkill": task["requiredSkill"],
                  "dependsOn": list(task["dependsOn"]), "agentId": None, "succeeded": False,
                  "skipped": False, "error": None, "warnings": [], "evidence": [],
                  "resolveMs": 0., "executionMs": 0., "aipTaskId": None, "aipState": None,
                  "startedAt": datetime.now(timezone.utc).isoformat()}
        resolve_started, execution_started, handle = perf_counter(), None, None
        execution_attempted = False
        try:
            handle = await asyncio.wait_for(self.resolver.resolve(task["requiredSkill"]), self.task_timeout_seconds)
            record["resolveMs"] = round((perf_counter() - resolve_started) * 1000, 3)
            if task["requiredSkill"] not in handle.capabilities or not isinstance(handle.executor, AipAgentExecutor):
                raise ValueError("MATCHED_AIP_EXECUTOR_REQUIRED")
            record["agentId"] = handle.agent_id
            if handle.executor.runner.partner_aic != handle.agent_id:
                raise ValueError("RESOLVER_AIP_IDENTITY_MISMATCH")
            bound = bind_task_input(task, dependencies)
            record["boundInput"] = deepcopy(bound["input"])
            # Runner has its own bounded timeout and preserves remote uncertainty.
            execution_started = perf_counter()
            execution_attempted = True
            result = await handle.executor.execute(make_task_payload(bound), deepcopy(dependencies))
            record.update(succeeded=True, warnings=list(result["warnings"]))
            record["evidence"] = [deepcopy(e) for item in result["items"] for e in item["data"].get("evidence", [])]
            return record, deepcopy(result)
        except NoMatchingAgentError:
            record["error"] = {"code": "NO_MATCHING_AGENT"}
        except InputBindingError:
            record["error"] = {"code": "INPUT_INSUFFICIENT"}
        except asyncio.TimeoutError:
            record["error"] = {"code": "RESOLVE_TIMEOUT"}
        except AipExecutionError as error:
            record["error"] = {"code": error.code}
        except Exception as error:
            code = getattr(error, "code", "AIP_INTEGRATION_ERROR")
            allowed = {"ADP_TIMEOUT", "ADP_TRANSPORT_ERROR", "ADP_INVALID_RESPONSE", "ADP_SERVER_ERROR",
                       "ADP_EXECUTOR_CONFIGURATION_REQUIRED", "ADP_INVALID_EXECUTOR"}
            record["error"] = {"code": code if code in allowed else "AIP_INTEGRATION_ERROR"}
        finally:
            record["finishedAt"] = datetime.now(timezone.utc).isoformat()
            if execution_started is not None:
                record["executionMs"] = round((perf_counter() - execution_started) * 1000, 3)
            elif record["resolveMs"] == 0:
                record["resolveMs"] = round((perf_counter() - resolve_started) * 1000, 3)
            if execution_attempted and handle and isinstance(handle.executor, AipAgentExecutor):
                receipt = handle.executor.execution_receipt(task["subtaskId"])
                if receipt:
                    record["aipReceipt"] = receipt
                    execution = receipt.get("execution")
                    if execution:
                        record.update(aipTaskId=execution["taskId"], aipState=execution["state"])
                    else:
                        trace = receipt.get("trace", {})
                        record.update(aipTaskId=trace.get("taskId"), remoteStateUnknown=trace.get("remoteStateUnknown", False))
        return record, None

    async def run_plan(self, plan):
        result = await super().run_plan(plan)
        result.update(demoOnly=False, executionMode="official-aip-injected-discovery",
                      platformDiscoveryVerified=False, realResearchVerified=False)
        # SDK completion proves protocol, not that an injected backend did real research.
        for record in result["executionRecords"]:
            record.setdefault("aipTaskId", None); record.setdefault("aipState", None)
            record.setdefault("warnings", []); record.setdefault("evidence", [])
        return result
