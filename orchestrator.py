"""Leader 本地调度骨架：依赖、并发、结果检查、局部失败处理。

不实现 AIP 消息或平台发现。执行记录是本地诊断，不是协议状态。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from time import perf_counter
from datetime import datetime, timezone
from input_binding import bind_task_input, InputBindingError
from common.skills import canonical_skill

from common.models import BUSINESS_TASK_FIELDS

from planner_contract import validate_plan
from result_contract import validate_result
from resolver.base import AgentResolver, NoMatchingAgentError


def make_task_payload(task):
    """去掉能力匹配和依赖字段。这里只准备业务内容，并未构造 TaskCommand。"""
    return {field: deepcopy(task[field]) for field in BUSINESS_TASK_FIELDS}


class LocalOrchestrator:
    def __init__(self, resolver: AgentResolver, task_timeout_seconds=5.0):
        if task_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于零")
        self.resolver = resolver
        self.task_timeout_seconds = task_timeout_seconds

    async def _execute(self, task, dependencies):
        record = {
            "subtaskId": task["subtaskId"],
            "requiredSkill": task["requiredSkill"],
            "dependsOn": list(task["dependsOn"]),
            "agentId": None,
            "succeeded": False,
            "skipped": False,
            "error": None,
            "resolveMs": 0.0,
            "executionMs": 0.0,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "aipTaskId": None,
            "aipState": None,
        }
        started = perf_counter()
        try:
            handle = await asyncio.wait_for(
                self.resolver.resolve(task["requiredSkill"]), self.task_timeout_seconds,
            )
            if task["requiredSkill"] not in map(canonical_skill, handle.capabilities):
                raise ValueError("解析器返回了不具备所需能力的执行者")
            record["agentId"] = handle.agent_id
        except NoMatchingAgentError as exc:
            record["error"] = {"code": "NO_MATCHING_AGENT", "message": str(exc)}
            return record, None
        except asyncio.TimeoutError:
            record["error"] = {"code": "RESOLVE_TIMEOUT", "message": "能力解析超时"}
            return record, None
        except Exception as exc:
            record["error"] = {"code": "RESOLVE_ERROR", "message": "能力解析失败；未回显底层异常"}
            return record, None
        finally:
            record["resolveMs"] = round((perf_counter() - started) * 1000, 3)

        started = perf_counter()
        try:
            bound = bind_task_input(task, dependencies)
            record["boundInput"] = deepcopy(bound["input"])
            result = await asyncio.wait_for(
                handle.executor.execute(make_task_payload(bound), deepcopy(dependencies)),
                self.task_timeout_seconds,
            )
            try:
                validate_result(result, task)
            except ValueError as exc:
                record["error"] = {"code": "INVALID_RESULT", "message": str(exc)}
                return record, None
            record["succeeded"] = True
            record["warnings"] = list(result["warnings"])
            return record, deepcopy(result)
        except InputBindingError as exc:
            record["error"] = {"code": "INPUT_INSUFFICIENT", "message": str(exc)}
        except asyncio.TimeoutError:
            record["error"] = {"code": "EXECUTION_TIMEOUT", "message": "模拟执行超时"}
        except Exception as exc:
            record["error"] = {"code": "EXECUTION_ERROR", "message": "执行失败；未回显底层异常"}
        finally:
            record["executionMs"] = round((perf_counter() - started) * 1000, 3)
            record["finishedAt"] = datetime.now(timezone.utc).isoformat()
        return record, None

    async def run_plan(self, plan):
        validate_plan(plan)
        records = {}
        results = {}
        pending = list(plan["tasks"])
        waves = []
        while pending:
            ready = [task for task in pending if all(dep in records for dep in task["dependsOn"])]
            if not ready:
                raise ValueError("任务依赖无法推进")
            waves.append([task["subtaskId"] for task in ready])
            runnable = []
            for task in ready:
                task_id = task["subtaskId"]
                failed_dependencies = [dep for dep in task["dependsOn"] if not records[dep]["succeeded"]]
                if failed_dependencies:
                    records[task_id] = {
                        "subtaskId": task_id, "requiredSkill": task["requiredSkill"],
                        "dependsOn": list(task["dependsOn"]), "agentId": None,
                        "succeeded": False, "skipped": True,
                        "error": {"code": "DEPENDENCY_FAILED", "message": f"上游任务未成功：{', '.join(failed_dependencies)}"},
                        "resolveMs": 0.0, "executionMs": 0.0,
                    }
                else:
                    runnable.append(task)
            # 同一波的任务没有未完成依赖，可并发执行；每项错误独立收集。
            completed = await asyncio.gather(*(
                self._execute(task, {dep: results[dep] for dep in task["dependsOn"]})
                for task in runnable
            ))
            for task, (record, result) in zip(runnable, completed):
                records[task["subtaskId"]] = record
                if result is not None:
                    results[task["subtaskId"]] = result
            ready_ids = {task["subtaskId"] for task in ready}
            pending = [task for task in pending if task["subtaskId"] not in ready_ids]

        return {
            "demoOnly": True,
            "executionMode": "local-mock",
            "userQuery": plan["userQuery"],
            "needsClarification": plan["needsClarification"],
            "clarificationQuestion": plan.get("clarificationQuestion"),
            "results": [results[task["subtaskId"]] for task in plan["tasks"] if task["subtaskId"] in results],
            "executionRecords": [records[task["subtaskId"]] for task in plan["tasks"]],
            "executionWaves": waves,
        }
