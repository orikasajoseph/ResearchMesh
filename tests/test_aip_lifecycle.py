import asyncio
import unittest
try:
    from acps_sdk.aip import TaskResult, TaskStatus, TaskState, Product, StructuredDataItem
    from aip_lifecycle import AipTaskRunner, AipExecutionError
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


@unittest.skipUnless(SDK_AVAILABLE, "AIP 测试需要项目 .venv 的官方 SDK 2.2.0")
class AipLifecycleTests(unittest.TestCase):
    def test_official_sdk_rpc_serialization_roundtrip(self):
        import json
        import httpx
        from acps_sdk.aip import AipRpcClient
        from aip_lifecycle import sdk_sender
        async def scenario():
            commands = []
            def handler(request):
                envelope = json.loads(request.content)
                command = envelope["params"]["command"]
                commands.append(command["command"])
                if command["command"] == "start":
                    self.assertEqual(command["dataItems"][0]["type"], "data")
                    self.assertNotIn("requiredCapability", command["dataItems"][0]["data"])
                business = {"schemaVersion": "0.1", "subtaskId": "S1", "taskType": "literature_search",
                            "summary": "No matching papers", "items": [], "warnings": []}
                result = TaskResult(id="r", sentAt=command["sentAt"], senderRole="partner", senderId="partner-test",
                    sessionId=command["sessionId"], taskId=command["taskId"],
                    status=TaskStatus(state=TaskState.AwaitingCompletion if len(commands) == 1 else TaskState.Completed,
                                      stateChangedAt=command["sentAt"]),
                    products=[Product(id="p", dataItems=[StructuredDataItem(data=business)])])
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": envelope["id"], "result": result.model_dump(mode="json")})
            # 明确仅限内存 HTTP 替身；生产不能关闭身份绑定。
            client = AipRpcClient("https://example.invalid/rpc", "leader-test", transport=httpx.MockTransport(handler),
                                  identity_binding_enabled=False)
            try:
                payload = {"schemaVersion": "0.1", "subtaskId": "S1", "taskType": "literature_search", "goal": "search",
                           "input": {"query": "test"}, "constraints": {}, "outputSchema": "paper_records.v1"}
                value = await AipTaskRunner(sdk_sender(client), "leader-test", "partner-test").run(payload, "session")
                self.assertEqual(value["state"], "completed")
                self.assertEqual(commands, ["start", "complete"])
            finally:
                await client.close()
        asyncio.run(scenario())

    def run_case(self, states, *, wrong_id=False, invalid=False, timeout=1, transport_only=False):
        payload = {"schemaVersion": "0.1", "subtaskId": "S1", "taskType": "literature_search",
                   "goal": "search", "input": {"query": "test"}, "constraints": {}, "outputSchema": "paper_records.v1"}
        business = {"schemaVersion": "0.1", "subtaskId": "S1", "taskType": "literature_search",
                    "summary": "No matching papers", "items": [], "warnings": []}
        if invalid:
            business["status"] = "custom-invalid-status"
        if transport_only:
            business["warnings"] = ["TRANSPORT_TEST_ONLY: no research was performed"]
        commands = []
        async def send(command):
            commands.append(command.command.value)
            state = states[min(len(commands) - 1, len(states) - 1)]
            return TaskResult(id="reply", sentAt="2026-10-02T00:00:00Z", senderRole="partner",
                senderId="partner-test", sessionId=command.sessionId,
                taskId="WRONG" if wrong_id else command.taskId,
                status=TaskStatus(state=TaskState(state), stateChangedAt="2026-10-02T00:00:00Z"),
                products=[Product(id="p1", dataItems=[StructuredDataItem(data=business)])])
        result = asyncio.run(AipTaskRunner(send, "leader-test", "partner-test", timeout_seconds=timeout,
                                          poll_seconds=0.001).run(payload, "session-test"))
        return result, commands

    def test_complete_handshake(self):
        result, commands = self.run_case(["accepted", "working", "awaiting-completion", "completed"])
        self.assertEqual(commands, ["start", "get", "get", "complete"])
        self.assertEqual(result["state"], "completed")

    def test_research_runner_rejects_transport_only_result(self):
        with self.assertRaisesRegex(AipExecutionError, "TRANSPORT_ONLY_NOT_RESEARCH"):
            self.run_case(["awaiting-completion", "completed"], transport_only=True)

    def test_product_alone_is_not_success(self):
        with self.assertRaisesRegex(AipExecutionError, "TIMEOUT"):
            self.run_case(["working"], timeout=0.01)

    def test_direct_completion_rejected_by_team_policy(self):
        with self.assertRaisesRegex(AipExecutionError, "HANDSHAKE"):
            self.run_case(["completed"])

    def test_mismatched_task_rejected(self):
        with self.assertRaisesRegex(AipExecutionError, "CORRELATION"):
            self.run_case(["awaiting-completion"], wrong_id=True)

    def test_bad_business_result_not_confirmed(self):
        with self.assertRaisesRegex(AipExecutionError, "INVALID_BUSINESS"):
            self.run_case(["awaiting-completion"], invalid=True)

    def test_partner_terminal_errors(self):
        for state in ("failed", "rejected", "canceled", "awaiting-input"):
            with self.subTest(state=state), self.assertRaises(AipExecutionError):
                self.run_case([state])
