"""One guarded Repository static execution per NEW durable Group task.

No platform protocol objects or identity here. No clone/model calls on import.
The calling Group handler must first reserve (Agent AIC, sessionId, taskId)
in DurableTaskStore, then transition it to `working` before this runs.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from aip_repository_partner import create_repository_business_executor
from business_execution import execute_business_task
from common.models import BusinessTask
from aip_business_partner import validate_local_task
from repository_paper_context import prepare_repository_task

ROOT = Path(__file__).resolve().parents[1]


class RepositoryStaticExecutor:
    """Create a fresh isolated static CodeWiki subprocess backend per task."""

    def __init__(self, *, repository_python: Path, wrapper_root: Path) -> None:
        self.repository_python = Path(repository_python).resolve()
        self.wrapper_root = Path(wrapper_root).resolve()

    def validate_runtime_paths(self) -> None:
        if not self.repository_python.is_file():
            raise ValueError("REPOSITORY_PYTHON_NOT_FOUND")
        if not (self.wrapper_root / "researchmesh_repository" / "__init__.py").is_file():
            raise ValueError("REPOSITORY_WRAPPER_NOT_FOUND")

    async def __call__(self, task: BusinessTask) -> dict:
        payload = task.model_dump(mode="json") if hasattr(task, "model_dump") else deepcopy(task)
        # Synchronous input validation occurs BEFORE any record dir/process creation.
        checked = validate_local_task(deepcopy(payload))
        if checked["taskType"] != "repository_analyze" or checked["outputSchema"] != "code_records.v1":
            raise ValueError("REPOSITORY_TASK_REQUIRED")
        prepare_repository_task(checked)
        self.validate_runtime_paths()

        output_root = ROOT / "outputs"
        output_root.mkdir(parents=True, exist_ok=True)
        output = output_root / ("repository-group-" + uuid4().hex)
        output.mkdir(mode=0o700, exist_ok=False)

        # This worker is deliberately SINGLE USE, so a different task cannot
        # reuse an in-memory executor, an input file, or a previous worker result.
        backend = create_repository_business_executor(
            record_dir=output,
            repository_python=self.repository_python,
            wrapper_root=self.wrapper_root,
            analysis_mode="static",
            dynamic=True,
            env_file=None,
        )
        return await execute_business_task(
            checked,
            skill="repository.analyze",
            backend=backend,
            repository_analysis_mode="static",
            allow_execution=True,
            timeout_seconds=180,
        )
