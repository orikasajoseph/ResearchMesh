import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, sentinel

import httpx
import litellm
from lmi.llms import LiteLLMModel
from openai import AsyncOpenAI

from model_http_audit import AuditedAsyncHttpClient, AuditedLiteLLMHTTPHandler, ModelHttpAudit


IDENTITY = {
    "group_id": "group-test",
    "session_id": "session-test",
    "task_id": "task-test",
    "worker_run_id": "run-test",
}


class FailingStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise httpx.ReadError("private response body detail")
        yield b""  # pragma: no cover

    async def aclose(self):
        return None


class ModelHttpAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "model_requests.jsonl"
        self.audit = ModelHttpAudit(self.path, **IDENTITY)

    def tearDown(self):
        self.temporary.cleanup()

    def events(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    async def request(self, handler, *, follow_redirects=False):
        client = AuditedAsyncHttpClient(
            self.audit,
            transport=httpx.MockTransport(handler),
            follow_redirects=follow_redirects,
        )
        try:
            return await client.get(
                "https://provider.invalid/chat/completions",
                headers={"Authorization": "Bearer private-key"},
            )
        finally:
            await client.aclose()

    async def test_success_has_paired_events_status_and_provider_request_id(self):
        response = await self.request(lambda request: httpx.Response(
            200, headers={"x-request-id": "provider-safe-id"}, json={"ok": True}
        ))
        self.assertEqual(response.status_code, 200)
        started, finished = self.events()
        self.assertEqual(started["event"], "MODEL_HTTP_STARTED")
        self.assertEqual(finished["event"], "MODEL_HTTP_FINISHED")
        self.assertEqual(started["requestId"], finished["requestId"])
        self.assertEqual(finished["httpStatus"], 200)
        self.assertTrue(finished["responseReceived"])
        self.assertEqual(finished["providerRequestId"], "provider-safe-id")
        self.assertIsNone(started["logicalCallSequence"])
        self.assertIsNone(started["operation"])

    async def test_multiple_requests_have_unique_ids_and_monotonic_sequences(self):
        client = AuditedAsyncHttpClient(
            self.audit, transport=httpx.MockTransport(lambda request: httpx.Response(200))
        )
        try:
            await client.get("https://provider.invalid/one")
            await client.get("https://provider.invalid/two")
        finally:
            await client.aclose()
        starts = [event for event in self.events() if event["event"] == "MODEL_HTTP_STARTED"]
        self.assertEqual([event["requestSequence"] for event in starts], [1, 2])
        self.assertEqual(len({event["requestId"] for event in starts}), 2)
        self.assertEqual(self.audit.request_count, 2)

    async def test_http_errors_retain_actual_status(self):
        for status in (404, 429, 500, 503):
            with self.subTest(status=status):
                await self.request(lambda request, code=status: httpx.Response(code, text="private body"))
        finishes = [event for event in self.events() if event["event"] == "MODEL_HTTP_FINISHED"]
        self.assertEqual([event["httpStatus"] for event in finishes], [404, 429, 500, 503])
        self.assertTrue(all(event["responseReceived"] for event in finishes))

    async def test_connection_exception_is_safe(self):
        def fail(request):
            raise httpx.ConnectError("private host and credential", request=request)
        with self.assertRaises(httpx.ConnectError):
            await self.request(fail)
        finished = self.events()[-1]
        self.assertFalse(finished["responseReceived"])
        self.assertIsNone(finished["httpStatus"])
        self.assertEqual(finished["exceptionClass"], "ConnectError")
        self.assertNotIn("private", self.path.read_text(encoding="utf-8"))

    async def test_started_write_failure_does_not_block_successful_request(self):
        with patch.object(self.audit, "_write", side_effect=OSError("private write")):
            response = await self.request(lambda request: httpx.Response(200, json={"ok": True}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.audit.audit_complete)
        self.assertIsNone(self.audit.request_count)
        self.assertIn("started", self.audit.failure_stages)

    async def test_initial_file_failure_disables_audit_without_blocking_request(self):
        disabled_path = Path(self.temporary.name) / "disabled.jsonl"
        with patch("model_http_audit.Path.touch", side_effect=OSError("private init")):
            audit = ModelHttpAudit(disabled_path, **IDENTITY)
        client = AuditedAsyncHttpClient(
            audit,
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        )
        try:
            response = await client.get("https://provider.invalid/chat/completions")
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(audit.audit_complete)
        self.assertIsNone(audit.request_count)
        self.assertEqual(audit.failure_stages, ["initialize"])
        self.assertFalse(disabled_path.exists())

    async def test_started_fsync_failure_does_not_block_successful_request(self):
        with patch("model_http_audit.os.fsync", side_effect=OSError("private fsync")):
            response = await self.request(lambda request: httpx.Response(200, json={"ok": True}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.audit.audit_complete)
        self.assertIsNone(self.audit.request_count)
        self.assertEqual(self.events()[0]["event"], "MODEL_HTTP_STARTED")

    async def test_finished_write_failure_does_not_change_successful_response(self):
        original_write = self.audit._write
        def fail_finished(record):
            if record["event"] == "MODEL_HTTP_FINISHED":
                raise OSError("private finished write")
            original_write(record)
        with patch.object(self.audit, "_write", side_effect=fail_finished):
            response = await self.request(lambda request: httpx.Response(200, json={"ok": True}))
        self.assertEqual(response.json(), {"ok": True})
        self.assertFalse(self.audit.audit_complete)
        self.assertIsNone(self.audit.request_count)
        self.assertEqual(self.audit.failure_stages, ["finished"])

    async def test_finished_fsync_failure_does_not_change_successful_response(self):
        with patch("model_http_audit.os.fsync", side_effect=[None, OSError("private fsync")]):
            response = await self.request(lambda request: httpx.Response(200, json={"ok": True}))
        self.assertEqual(response.json(), {"ok": True})
        self.assertFalse(self.audit.audit_complete)
        self.assertIsNone(self.audit.request_count)
        self.assertEqual(self.audit.failure_stages, ["finished"])
        self.assertEqual(self.events()[-1]["event"], "MODEL_HTTP_FINISHED")

    async def test_body_completion_audit_failure_does_not_change_body(self):
        original_write = self.audit._write
        def fail_finished(record):
            if record["event"] == "MODEL_HTTP_FINISHED":
                raise OSError("private completion write")
            original_write(record)
        with patch.object(self.audit, "_write", side_effect=fail_finished):
            response = await self.request(lambda request: httpx.Response(200, content=b"body-ok"))
        self.assertEqual(response.content, b"body-ok")
        self.assertFalse(self.audit.audit_complete)

    async def test_audit_failure_does_not_mask_transport_exception(self):
        original = httpx.ConnectError("original transport failure")
        def fail(request):
            raise original
        with patch.object(self.audit, "_write", side_effect=OSError("private audit failure")):
            with self.assertRaises(httpx.ConnectError) as caught:
                await self.request(fail)
        self.assertIs(caught.exception, original)
        self.assertFalse(self.audit.audit_complete)
        self.assertIsNone(self.audit.request_count)

    async def test_cancellation_is_recorded(self):
        async def fail(request):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.request(fail)
        finished = self.events()[-1]
        self.assertTrue(finished["cancelled"])
        self.assertEqual(finished["exceptionClass"], "CancelledError")
        self.assertFalse(finished["responseReceived"])

    async def test_audit_failure_does_not_change_cancellation(self):
        async def fail(request):
            raise asyncio.CancelledError()
        with patch.object(self.audit, "_write", side_effect=OSError("private audit failure")):
            with self.assertRaises(asyncio.CancelledError):
                await self.request(fail)
        self.assertFalse(self.audit.audit_complete)
        self.assertIsNone(self.audit.request_count)

    async def test_redirects_are_counted_as_physical_requests(self):
        calls = 0
        def redirect(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(302, headers={"location": "/final"})
            return httpx.Response(200)
        response = await self.request(redirect, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.audit.request_count, 2)
        self.assertEqual(
            [event["httpStatus"] for event in self.events()
             if event["event"] == "MODEL_HTTP_FINISHED"],
            [302, 200],
        )

    async def test_openai_sdk_retry_is_counted_per_transport_attempt(self):
        calls = 0
        def retry(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("first attempt", request=request)
            return httpx.Response(200, json={
                "id": "response-id", "object": "chat.completion", "created": 1,
                "model": "synthetic", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop"}],
            })
        http_client = AuditedAsyncHttpClient(
            self.audit, transport=httpx.MockTransport(retry)
        )
        client = AsyncOpenAI(
            api_key="test-key", base_url="https://provider.invalid/v1",
            max_retries=1, http_client=http_client,
        )
        try:
            result = await client.chat.completions.create(
                model="synthetic", messages=[{"role": "user", "content": "private prompt"}]
            )
        finally:
            await client.close()
        self.assertEqual(result.choices[0].message.content, "ok")
        self.assertEqual(self.audit.request_count, 2)
        self.assertEqual(
            [event["responseReceived"] for event in self.events()
             if event["event"] == "MODEL_HTTP_FINISHED"],
            [False, True],
        )

    async def test_litellm_deepseek_call_uses_injected_http_handler(self):
        def respond(request):
            return httpx.Response(200, json={
                "id": "response-id", "object": "chat.completion", "created": 1,
                "model": "synthetic", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop"}],
            })
        handler = AuditedLiteLLMHTTPHandler(
            self.audit, transport=httpx.MockTransport(respond)
        )
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model="deepseek/synthetic",
                    api_key="test-key",
                    api_base="https://provider.invalid/v1",
                    client=handler,
                    messages=[{"role": "user", "content": "private prompt"}],
                ),
                timeout=5,
            )
        finally:
            await handler.close()
        self.assertEqual(response.choices[0].message.content, "ok")
        self.assertEqual(self.audit.request_count, 1)
        self.assertEqual(
            [event["httpStatus"] for event in self.events()
             if event["event"] == "MODEL_HTTP_FINISHED"],
            [200],
        )

    def test_litellm_client_construction_preserves_original_parameters(self):
        handler = AuditedLiteLLMHTTPHandler.__new__(AuditedLiteLLMHTTPHandler)
        handler._model_audit = self.audit
        handler._model_transport = None
        ssl_context = __import__("ssl").SSLContext(__import__("ssl").PROTOCOL_TLS_CLIENT)
        headers = {"user-agent": "litellm-test"}
        with patch("model_http_audit.get_ssl_configuration", return_value=ssl_context), \
             patch("model_http_audit.get_default_headers", return_value=headers), \
             patch("model_http_audit.AsyncHTTPHandler._create_async_transport",
                   return_value=sentinel.transport) as create_transport, \
             patch("model_http_audit.AuditedAsyncHttpClient",
                   return_value=sentinel.client) as client_type, \
             patch.dict("model_http_audit.os.environ",
                        {"SSL_CERTIFICATE": "synthetic-client-cert.pem"}):
            result = handler.create_client(
                timeout=sentinel.timeout,
                event_hooks=sentinel.hooks,
                ssl_verify=sentinel.ssl_verify,
                shared_session=sentinel.shared_session,
            )
        self.assertIs(result, sentinel.client)
        create_transport.assert_called_once_with(
            ssl_context=ssl_context,
            ssl_verify=None,
            shared_session=sentinel.shared_session,
        )
        client_type.assert_called_once_with(
            self.audit,
            transport=sentinel.transport,
            event_hooks=sentinel.hooks,
            timeout=sentinel.timeout,
            verify=ssl_context,
            cert="synthetic-client-cert.pem",
            headers=headers,
            follow_redirects=True,
        )

    def test_litellm_client_uses_original_default_timeout_and_test_transport(self):
        handler = AuditedLiteLLMHTTPHandler.__new__(AuditedLiteLLMHTTPHandler)
        handler._model_audit = self.audit
        handler._model_transport = sentinel.injected_transport
        with patch("model_http_audit.get_ssl_configuration", return_value=True), \
             patch("model_http_audit.get_default_headers", return_value={}), \
             patch("model_http_audit.AsyncHTTPHandler._create_async_transport") as create_transport, \
             patch("model_http_audit.AuditedAsyncHttpClient",
                   return_value=sentinel.client) as client_type:
            handler.create_client(
                timeout=None, event_hooks=None, ssl_verify=None,
                shared_session=sentinel.shared_session,
            )
        create_transport.assert_not_called()
        kwargs = client_type.call_args.kwargs
        self.assertIs(kwargs["transport"], sentinel.injected_transport)
        self.assertEqual(kwargs["timeout"], __import__("model_http_audit")._DEFAULT_TIMEOUT)
        self.assertTrue(kwargs["verify"])
        self.assertTrue(kwargs["follow_redirects"])

    async def test_lmi_model_spec_preserves_injected_transport_handler(self):
        handler = AuditedLiteLLMHTTPHandler(
            self.audit, transport=httpx.MockTransport(lambda request: httpx.Response(200))
        )
        try:
            model = LiteLLMModel(name="audit-alias", config={
                "model_list": [{"model_name": "audit-alias", "litellm_params": {
                    "model": "deepseek/synthetic", "api_key": "test-key",
                    "api_base": "https://provider.invalid/v1", "max_retries": 0,
                    "client": handler,
                }}],
                "router_kwargs": {"num_retries": 0},
            })
            kwargs = model.llm_config.models[0].to_litellm_kwargs()
            self.assertIs(kwargs["client"], handler)
            self.assertEqual(model.llm_config.models[0].max_retries, 0)
        finally:
            await handler.close()

    async def test_deepseek_handler_connection_retry_counts_each_physical_attempt(self):
        calls = 0
        def respond(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("private first failure", request=request)
            return httpx.Response(200, json={
                "id": "response-id", "object": "chat.completion", "created": 1,
                "model": "synthetic", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop"}],
            })
        handler = AuditedLiteLLMHTTPHandler(
            self.audit, transport=httpx.MockTransport(respond)
        )
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model="deepseek/synthetic", api_key="test-key",
                    api_base="https://provider.invalid/v1", client=handler,
                    messages=[{"role": "user", "content": "private prompt"}],
                ),
                timeout=5,
            )
        finally:
            await handler.close()
        self.assertEqual(response.choices[0].message.content, "ok")
        self.assertEqual(self.audit.request_count, 2)
        finishes = [event for event in self.events()
                    if event["event"] == "MODEL_HTTP_FINISHED"]
        self.assertEqual([event["responseReceived"] for event in finishes], [False, True])
        self.assertNotIn("private", self.path.read_text(encoding="utf-8"))

    async def test_response_stream_failure_keeps_status_and_exception_class(self):
        with self.assertRaises(httpx.ReadError):
            await self.request(lambda request: httpx.Response(200, stream=FailingStream()))
        finished = self.events()[-1]
        self.assertTrue(finished["responseReceived"])
        self.assertEqual(finished["httpStatus"], 200)
        self.assertEqual(finished["exceptionClass"], "ReadError")
        self.assertNotIn("private response", self.path.read_text(encoding="utf-8"))

    def test_started_event_survives_without_finished_event(self):
        self.audit.started()
        self.assertEqual([event["event"] for event in self.events()], ["MODEL_HTTP_STARTED"])

    async def test_identity_and_allowlist_exclude_sensitive_request_data(self):
        await self.request(lambda request: httpx.Response(
            200,
            headers={"authorization": "secret-response", "cookie": "secret-cookie"},
            text="private paper response",
        ))
        text = self.path.read_text(encoding="utf-8")
        events = self.events()
        self.assertTrue(all(event["groupId"] == "group-test" for event in events))
        self.assertTrue(all(event["sessionId"] == "session-test" for event in events))
        self.assertTrue(all(event["taskId"] == "task-test" for event in events))
        self.assertTrue(all(event["workerRunId"] == "run-test" for event in events))
        for forbidden in ("private-key", "secret-response", "secret-cookie",
                          "private paper response", "provider.invalid"):
            self.assertNotIn(forbidden, text)

    async def test_untrusted_request_id_header_is_not_persisted(self):
        await self.request(lambda request: httpx.Response(
            200, headers={"x-request-id": "unsafe request id with spaces"}
        ))
        self.assertIsNone(self.events()[-1]["providerRequestId"])


if __name__ == "__main__":
    unittest.main()
