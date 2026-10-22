"""Bridge a Resolver AgentHandle to the already verified official AIP lifecycle."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from aip_lifecycle import AipTaskRunner, AipExecutionError


class AipAgentExecutor:
    def __init__(self, runner, *, session_id=None):
        if not isinstance(runner, AipTaskRunner) or runner.allow_mock_results:
            raise ValueError("REAL_AIP_RUNNER_REQUIRED")
        self.runner = runner
        self.session_id = session_id or str(uuid4())
        self.receipts = {}

    async def execute(self, payload, dependency_results):
        """dependency_results stays inside Leader; payload must already be bound."""
        started = datetime.now(timezone.utc).isoformat()
        self.receipts.pop(payload["subtaskId"], None)
        try:
            execution = await self.runner.run(deepcopy(payload), self.session_id)
        except AipExecutionError as error:
            self.receipts[payload["subtaskId"]] = {"startedAt": started,
                "finishedAt": datetime.now(timezone.utc).isoformat(), "execution": None,
                "error": {"code": error.code}, "trace": deepcopy(getattr(error, "trace", {}))}
            raise
        self.receipts[payload["subtaskId"]] = {"startedAt": started,
            "finishedAt": datetime.now(timezone.utc).isoformat(), "execution": deepcopy(execution), "error": None}
        return deepcopy(execution["result"])

    def execution_receipt(self, subtask_id):
        return deepcopy(self.receipts.get(subtask_id))
