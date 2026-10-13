"""只使用 HTTP MockTransport，自动测试绝不读取真实 Key 或访问网络。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

try:
    import httpx
    from openai import AsyncOpenAI
    from deepseek_model import (
        DeepSeekCallError, DeepSeekConfigError, DeepSeekModel, DeepSeekSettings, safe_failure,
    )
    HAVE_LLM = True
except ImportError:
    HAVE_LLM = False

from planner import Planner, PlannerError


ROOT = Path(__file__).resolve().parents[1]
SECRET = "private-test-key-not-a-real-credential"


def example():
    return json.loads((ROOT / "examples" / "planner_cases.json").read_text(encoding="utf-8"))[0]["plan"]


def completion(content, finish="stop"):
    return {
        "id": "test-completion", "object": "chat.completion", "created": 0,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@unittest.skipUnless(HAVE_LLM, "安装 requirements-llm.txt 后运行模型适配测试")
class ConfigTests(unittest.TestCase):
    def test_key_is_not_in_repr(self):
        self.assertNotIn(SECRET, repr(DeepSeekSettings(SECRET)))

    def test_missing_and_placeholder_keys_rejected(self):
        for key in ("", " ", "替换为你的真实Key", "your-api-key"):
            with self.subTest(key=key), self.assertRaises(DeepSeekConfigError):
                DeepSeekSettings(key)

    def test_only_official_https_endpoint_is_allowed(self):
        for url in ("http://api.deepseek.com", "https://other.invalid", "https://api.deepseek.com.evil.invalid"):
            with self.assertRaises(DeepSeekConfigError):
                DeepSeekSettings(SECRET, base_url=url)
        DeepSeekSettings(SECRET, base_url="https://api.deepseek.com/v1")

    def test_invalid_model_is_rejected(self):
        with self.assertRaises(DeepSeekConfigError):
            DeepSeekSettings(SECRET, model="unknown")

    def test_invalid_timeout_is_rejected(self):
        for value in (0, -1, 121, float("nan"), float("inf")):
            with self.assertRaises(DeepSeekConfigError):
                DeepSeekSettings(SECRET, timeout_seconds=value)

    def test_invalid_token_limit_is_rejected(self):
        for value in (0, 8193, True, 1024.5):
            with self.assertRaises(DeepSeekConfigError):
                DeepSeekSettings(SECRET, max_tokens=value)

    def test_file_and_process_precedence_without_environment_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY=file-key\nDEEPSEEK_TIMEOUT_SECONDS=45\n", encoding="utf-8")
            process = {"DEEPSEEK_API_KEY": SECRET, "UNRELATED": "ignored"}
            settings = DeepSeekSettings.from_env(path, process)
            self.assertEqual(settings.api_key, SECRET)
            self.assertEqual(settings.timeout_seconds, 45)
            self.assertEqual(process, {"DEEPSEEK_API_KEY": SECRET, "UNRELATED": "ignored"})

    def test_missing_file_without_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DeepSeekConfigError):
                DeepSeekSettings.from_env(Path(directory) / "missing.env", {})

    def test_invalid_number_does_not_expose_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(f"DEEPSEEK_API_KEY={SECRET}\nDEEPSEEK_MAX_TOKENS={SECRET}\n", encoding="utf-8")
            with self.assertRaises(DeepSeekConfigError) as caught:
                DeepSeekSettings.from_env(path, {})
            self.assertNotIn(SECRET, str(caught.exception))


@unittest.skipUnless(HAVE_LLM, "安装 requirements-llm.txt 后运行模型适配测试")
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def make_model(self, handler, settings=None):
        settings = settings or DeepSeekSettings(SECRET)
        client = AsyncOpenAI(
            api_key=SECRET, base_url=settings.base_url, max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
        )
        return DeepSeekModel(settings, client=client)

    async def test_success_runs_through_sdk_and_existing_planner(self):
        plan = example()
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=completion(json.dumps(plan, ensure_ascii=False)))

        async with self.make_model(handler) as model:
            result = await Planner(model).plan(plan["userQuery"])
        self.assertEqual(result, plan)
        body = json.loads(requests[0].content)
        self.assertEqual(requests[0].url.path, "/chat/completions")
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["messages"][1]["content"], plan["userQuery"])
        self.assertIn("JSON Schema", body["messages"][0]["content"])
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertEqual(model.request_count, 1)
        self.assertEqual(model.last_usage["totalTokens"], 30)
        self.assertTrue(model.client.is_closed())

    async def test_connection_is_one_short_non_json_request(self):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=completion("DeepSeek连接成功"))

        async with self.make_model(handler) as model:
            self.assertEqual(await model.check_connection(), "DeepSeek连接成功")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["max_tokens"], 128)
        self.assertNotIn("response_format", bodies[0])

    async def test_bad_json_is_repaired_once_without_sdk_retries(self):
        plan = example()
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            text = "bad JSON" if len(bodies) == 1 else json.dumps(plan)
            return httpx.Response(200, json=completion(text))

        async with self.make_model(handler) as model:
            self.assertEqual(await Planner(model).plan(plan["userQuery"]), plan)
        self.assertEqual(len(bodies), 2)
        self.assertIn("上次输出", bodies[1]["messages"][0]["content"])

    async def test_empty_planner_response_is_repaired_once(self):
        plan = example()
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=completion("" if len(calls) == 1 else json.dumps(plan)))

        async with self.make_model(handler) as model:
            self.assertEqual(await Planner(model).plan(plan["userQuery"]), plan)
        self.assertEqual(len(calls), 2)

    async def test_truncated_output_is_not_accepted(self):
        def handler(request):
            return httpx.Response(200, json=completion(json.dumps(example()), finish="length"))

        async with self.make_model(handler) as model:
            with self.assertRaises(PlannerError) as caught:
                await Planner(model).plan(example()["userQuery"])
            self.assertIn("截断", safe_failure(caught.exception))
            self.assertEqual(model.request_count, 1)

    async def test_http_failures_are_sanitized_and_not_retried(self):
        for code in (400, 401, 402, 403, 404, 429, 500):
            def handler(request):
                return httpx.Response(code, json={"error": {"message": SECRET}})

            async with self.make_model(handler) as model:
                with self.assertRaises(PlannerError) as caught:
                    await Planner(model).plan("检索图神经网络论文")
                message = safe_failure(caught.exception)
                self.assertIn(str(code), message)
                self.assertNotIn(SECRET, message)
                self.assertEqual(model.request_count, 1)

    async def test_network_error_is_sanitized(self):
        def handler(request):
            raise httpx.ConnectError(SECRET, request=request)

        async with self.make_model(handler) as model:
            with self.assertRaises(DeepSeekCallError) as caught:
                await model.check_connection()
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertIn("网络", str(caught.exception))

    async def test_missing_choices_rejected(self):
        def handler(request):
            body = completion("")
            body["choices"] = []
            return httpx.Response(200, json=body)

        async with self.make_model(handler) as model:
            with self.assertRaises(DeepSeekCallError):
                await model.check_connection()

    async def test_empty_connection_reply_rejected(self):
        def handler(request):
            return httpx.Response(200, json=completion(" "))

        async with self.make_model(handler) as model:
            with self.assertRaises(DeepSeekCallError):
                await model.check_connection()

    async def test_schema_failure_is_not_replaced_by_fixture(self):
        invalid = deepcopy(example())
        invalid["tasks"][0]["taskType"] = "invented"

        def handler(request):
            return httpx.Response(200, json=completion(json.dumps(invalid)))

        async with self.make_model(handler) as model:
            with self.assertRaises(PlannerError):
                await Planner(model).plan(invalid["userQuery"])
            self.assertEqual(model.request_count, 2)

    async def test_new_question_is_sent_to_sdk_and_validated(self):
        plan = example()
        plan["userQuery"] = "只检索量子纠错方向的论文，不需要阅读或代码分析"
        plan["tasks"] = [plan["tasks"][0]]
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=completion(json.dumps(plan)))

        async with self.make_model(handler) as model:
            result = await Planner(model).plan(plan["userQuery"])
        self.assertEqual(result["userQuery"], plan["userQuery"])
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(bodies[0]["messages"][1]["content"], plan["userQuery"])

    async def test_adapter_request_is_cancelled_by_planner_timeout(self):
        async def handler(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json=completion(json.dumps(example())))

        async with self.make_model(handler) as model:
            with self.assertRaisesRegex(PlannerError, "超时"):
                await Planner(model, timeout_seconds=0.01).plan(example()["userQuery"])
            self.assertEqual(model.request_count, 1)


@unittest.skipUnless(HAVE_LLM, "安装 requirements-llm.txt 后运行模型适配测试")
class EntryPointTests(unittest.TestCase):
    def test_missing_key_cli_exits_before_prompt_and_network(self):
        import run_deepseek_planner
        output = io.StringIO()
        with patch("sys.argv", ["run_deepseek_planner.py"]), \
                patch("deepseek_model.DeepSeekSettings.from_env", side_effect=DeepSeekConfigError("未配置 Key")), \
                patch("builtins.input") as ask, \
                patch("deepseek_model.AsyncOpenAI") as sdk, redirect_stdout(output):
            self.assertEqual(run_deepseek_planner.main(), 1)
        ask.assert_not_called()
        sdk.assert_not_called()
        self.assertIn("未配置 Key", output.getvalue())

    def test_missing_key_connection_exits_without_network(self):
        import test_deepseek_connection
        with patch("deepseek_model.DeepSeekSettings.from_env", side_effect=DeepSeekConfigError("未配置 Key")), \
                patch("deepseek_model.AsyncOpenAI") as sdk, redirect_stdout(io.StringIO()):
            self.assertEqual(test_deepseek_connection.main(), 1)
        sdk.assert_not_called()

    def test_outputs_are_distinct_and_do_not_include_credentials(self):
        from run_deepseek_planner import save_plan
        metadata = {"plannerMode": "deepseek_live", "agentsExecuted": False, "aipUsed": False}
        with tempfile.TemporaryDirectory() as directory:
            first = save_plan(example(), metadata, Path(directory))
            second = save_plan(example(), metadata, Path(directory))
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads((first / "plan.json").read_text(encoding="utf-8")), example())
            self.assertEqual(json.loads((first / "run_info.json").read_text(encoding="utf-8")), metadata)
            for path in first.iterdir():
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8"))

    def test_unknown_error_is_not_exposed(self):
        self.assertNotIn(SECRET, safe_failure(RuntimeError(SECRET)))

    def test_saved_outputs_are_distinct_even_when_clock_is_identical(self):
        import run_deepseek_planner as entry
        with tempfile.TemporaryDirectory() as directory, patch.object(entry, "datetime") as clock:
            clock.now.return_value.strftime.return_value = "live-planner-fixed-clock"
            first = entry.save_plan(example(), {}, Path(directory))
            second = entry.save_plan(example(), {}, Path(directory))
            self.assertNotEqual(first, second)
            self.assertTrue((first / "plan.json").is_file())
            self.assertTrue((second / "plan.json").is_file())


if __name__ == "__main__":
    unittest.main()
