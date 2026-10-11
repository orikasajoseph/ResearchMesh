"""Local SDK-based Partner; no platform registration or research claims."""

import os

from fastapi import FastAPI
from acps_sdk.aip import Product, TaskState, TextDataItem, StructuredDataItem
from pydantic import ValidationError
from .contracts import ResearchTask, transport_only_result
from .execution import ResearchExecutor, BackendFailure, execute_checked, validate_timeout
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers, DefaultHandlers, TaskManager, add_aip_rpc_router,
)

PARTNER_AIC = os.getenv("PARTNER_AIC", "example-partner-aic")


async def start(command, existing):
    """Compatibility entry used by W1 exports; default remains transport-only."""
    return await _start(command, existing)


async def _start(command, existing, *, execute=None, backend_timeout_seconds=20.0):
    if existing is not None:
        return existing
    structured = [item for item in command.dataItems or [] if isinstance(item, StructuredDataItem)]
    if structured:
        try:
            if len(structured) != 1:
                raise ValueError("Expected exactly one structured task")
            payload = ResearchTask.model_validate(structured[0].data)
        except (ValidationError, ValueError) as error:
            task = TaskManager.create_task(
                command, initial_state=TaskState.Rejected,
                data_items=[TextDataItem(text=str(error))],
            )
            task.senderId = PARTNER_AIC
            return task
        initial = TaskState.Working if execute is not None else TaskState.AwaitingCompletion
        task = TaskManager.create_task(command, initial_state=initial)
        task.senderId = PARTNER_AIC
        if execute is None:
            result = transport_only_result(payload)
        else:
            try:
                result = await execute_checked(execute, payload, timeout_seconds=backend_timeout_seconds)
            except BackendFailure as error:
                # Cancellation can arrive through a separate RPC while execution awaits.
                if task.status.state == TaskState.Working:
                    TaskManager.update_task_status(task.taskId, TaskState.Failed,
                                                   [TextDataItem(text=str(error))])
                return TaskManager.get_task(task.taskId)
            if task.status.state != TaskState.Working:
                return TaskManager.get_task(task.taskId)
        TaskManager.set_products(task.taskId, [Product(
            id=f"result-{task.taskId}", name="researchmesh-result",
            dataItems=[StructuredDataItem(data=result.model_dump())],
        )])
        if execute is not None and task.status.state == TaskState.Working:
            TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
        return TaskManager.get_task(task.taskId)
    text = next((item.text for item in command.dataItems or []
                 if isinstance(item, TextDataItem)), "")
    task = TaskManager.create_task(
        command,
        initial_state=TaskState.AwaitingCompletion if text.strip() else TaskState.Rejected,
    )
    task.senderId = PARTNER_AIC
    if text.strip():
        # Text commands use a transport-only echo Product.
        TaskManager.set_products(task.taskId, [Product(
            id=f"echo-{task.taskId}", name="local-transport-test",
            dataItems=[TextDataItem(text=f"AIP transport received: {text}")],
        )])
    return TaskManager.get_task(task.taskId)


def create_local_app(
    *, execute: ResearchExecutor | None = None, backend_timeout_seconds: float = 20.0,
) -> FastAPI:
    """Inject Zhang's async backend in a loopback-only integration application.

    Start awaits the bounded backend; background lifecycle is not implemented.
    Identity verification is still disabled. This is not a deployment factory.
    """
    validate_timeout(backend_timeout_seconds)
    local_app = FastAPI(title="ResearchMesh local AIP integration")

    async def configured_start(command, existing):
        return await _start(command, existing, execute=execute,
                            backend_timeout_seconds=backend_timeout_seconds)

    add_aip_rpc_router(local_app, "/rpc", CommandHandlers(
        on_start=configured_start, on_get=DefaultHandlers.get,
        on_complete=DefaultHandlers.complete, on_cancel=DefaultHandlers.cancel,
        on_continue=DefaultHandlers.continue_,
    ), identity_binding_enabled=False)

    @local_app.get("/health")
    async def health():
        return {"status": "ok", "aic": PARTNER_AIC, "mode": "local-only"}

    return local_app


app = create_local_app()
