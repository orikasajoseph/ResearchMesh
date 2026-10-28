"""Durable, allow-listed audit events for physical model HTTP attempts."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime
import json
import os
from pathlib import Path
import re
import ssl
import threading
import time
from uuid import uuid4

import httpx
import litellm
from litellm.llms.custom_httpx.http_handler import (
    AsyncHTTPHandler,
    _DEFAULT_TIMEOUT,
    get_default_headers,
    get_ssl_configuration,
)


_PROVIDER_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-ms-request-id",
)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def _provider_request_id(headers):
    for name in _PROVIDER_REQUEST_ID_HEADERS:
        value = headers.get(name)
        if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
            return value
    return None


class ModelHttpAudit:
    """Append-only model transport audit owned by one Worker run."""

    def __init__(self, path, *, group_id, session_id, task_id, worker_run_id,
                 provider="deepseek-openai-compatible"):
        values = (group_id, session_id, task_id, worker_run_id, provider)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("MODEL_HTTP_AUDIT_IDENTITY_REQUIRED")
        self.path = Path(path)
        self.identity = {
            "groupId": group_id,
            "sessionId": session_id,
            "taskId": task_id,
            "workerRunId": worker_run_id,
        }
        self.provider = provider
        self._lock = threading.RLock()
        self._sequence = 0
        self._audit_complete = True
        self._failure_stages = []
        self._disabled = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=False)
        except Exception:
            self._audit_complete = False
            self._failure_stages.append("initialize")
            self._disabled = True

    @property
    def request_count(self):
        with self._lock:
            return self._sequence if self._audit_complete else None

    @property
    def audit_complete(self):
        with self._lock:
            return self._audit_complete

    @property
    def failure_stages(self):
        with self._lock:
            return list(self._failure_stages)

    def started(self):
        with self._lock:
            self._sequence += 1
            attempt = _Attempt(
                audit=self,
                sequence=self._sequence,
                request_id=uuid4().hex,
                started=time.perf_counter(),
            )
            self._safe_write({
                "event": "MODEL_HTTP_STARTED",
                "at": datetime.now().astimezone().isoformat(),
                **self.identity,
                "requestSequence": attempt.sequence,
                "requestId": attempt.request_id,
                "logicalCallSequence": None,
                "attemptWithinLogicalCall": None,
                "provider": self.provider,
                "operation": None,
            }, stage="started")
            return attempt

    def finished(self, attempt, *, response_received, http_status=None,
                 provider_request_id=None, exception=None, cancelled=False):
        with self._lock:
            if attempt.finished:
                return
            attempt.finished = True
            self._safe_write({
                "event": "MODEL_HTTP_FINISHED",
                "at": datetime.now().astimezone().isoformat(),
                **self.identity,
                "requestSequence": attempt.sequence,
                "requestId": attempt.request_id,
                "logicalCallSequence": None,
                "attemptWithinLogicalCall": None,
                "provider": self.provider,
                "operation": None,
                "responseReceived": bool(response_received),
                "httpStatus": http_status if isinstance(http_status, int) else None,
                "providerRequestId": provider_request_id,
                "durationMs": round((time.perf_counter() - attempt.started) * 1000, 3),
                "exceptionClass": type(exception).__name__ if exception is not None else None,
                "cancelled": bool(cancelled),
            }, stage="finished")

    def _safe_write(self, record, *, stage):
        if self._disabled:
            return
        try:
            self._write(record)
        except Exception:
            self._audit_complete = False
            if stage not in self._failure_stages:
                self._failure_stages.append(stage)

    def _write(self, record):
        data = (json.dumps(record, ensure_ascii=False, allow_nan=False,
                           separators=(",", ":")) + "\n").encode("utf-8")
        with self.path.open("ab", buffering=0) as stream:
            if stream.write(data) != len(data):
                raise OSError("MODEL_HTTP_AUDIT_SHORT_WRITE")
            os.fsync(stream.fileno())


class _Attempt:
    def __init__(self, *, audit, sequence, request_id, started):
        self.audit = audit
        self.sequence = sequence
        self.request_id = request_id
        self.started = started
        self.finished = False
        self.response_received = False
        self.http_status = None
        self.provider_request_id = None


class _AuditedResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream, attempt):
        self._stream = stream
        self._attempt = attempt

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                yield chunk
        except BaseException as error:
            self._attempt.audit.finished(
                self._attempt,
                response_received=True,
                http_status=self._attempt.http_status,
                provider_request_id=self._attempt.provider_request_id,
                exception=error,
                cancelled=isinstance(error, asyncio.CancelledError),
            )
            raise
        else:
            self._attempt.audit.finished(
                self._attempt,
                response_received=True,
                http_status=self._attempt.http_status,
                provider_request_id=self._attempt.provider_request_id,
            )

    async def aclose(self):
        try:
            await self._stream.aclose()
        finally:
            self._attempt.audit.finished(
                self._attempt,
                response_received=True,
                http_status=self._attempt.http_status,
                provider_request_id=self._attempt.provider_request_id,
            )


class AuditedAsyncHttpClient(httpx.AsyncClient):
    """HTTPX client that adds audit hooks without changing request behavior."""

    def __init__(self, audit, **kwargs):
        self._model_audit = audit
        self._current_attempt = ContextVar(
            "researchmesh_model_http_attempt", default=None
        )
        hooks = dict(kwargs.pop("event_hooks", {}) or {})
        hooks["request"] = [*hooks.get("request", []), self._request_started]
        hooks["response"] = [*hooks.get("response", []), self._response_received]
        super().__init__(event_hooks=hooks, **kwargs)

    async def _request_started(self, request):
        attempt = self._model_audit.started()
        request.extensions["researchmesh_model_http_attempt"] = attempt
        self._current_attempt.set(attempt)

    async def _response_received(self, response):
        attempt = response.request.extensions.get("researchmesh_model_http_attempt")
        if attempt is None:
            return
        provider_request_id = _provider_request_id(response.headers)
        attempt.response_received = True
        attempt.http_status = response.status_code
        attempt.provider_request_id = provider_request_id
        if response.is_stream_consumed:
            self._model_audit.finished(
                attempt, response_received=True, http_status=response.status_code,
                provider_request_id=provider_request_id,
            )
        else:
            response.stream = _AuditedResponseStream(response.stream, attempt)

    async def send(self, request, *args, **kwargs):
        token = self._current_attempt.set(None)
        try:
            return await super().send(request, *args, **kwargs)
        except BaseException as error:
            attempt = self._current_attempt.get()
            if attempt is not None:
                self._model_audit.finished(
                    attempt,
                    response_received=attempt.response_received,
                    http_status=attempt.http_status,
                    provider_request_id=attempt.provider_request_id,
                    exception=error,
                    cancelled=isinstance(error, asyncio.CancelledError),
                )
            raise
        finally:
            self._current_attempt.reset(token)


class AuditedLiteLLMHTTPHandler(AsyncHTTPHandler):
    """DeepSeek handler using the audited HTTPX client accepted by LiteLLM."""

    def __init__(self, audit, *, transport=None, timeout=None):
        self._model_audit = audit
        self._model_transport = transport
        super().__init__(timeout=timeout)

    def create_client(self, *, timeout, event_hooks, ssl_verify=None,
                      shared_session=None):
        ssl_config = get_ssl_configuration(ssl_verify)
        cert = os.getenv("SSL_CERTIFICATE", litellm.ssl_certificate)
        if timeout is None:
            timeout = _DEFAULT_TIMEOUT
        transport = self._model_transport
        if transport is None:
            transport = AsyncHTTPHandler._create_async_transport(
                ssl_context=(ssl_config
                             if isinstance(ssl_config, ssl.SSLContext) else None),
                ssl_verify=ssl_config if isinstance(ssl_config, bool) else None,
                shared_session=shared_session,
            )
        return AuditedAsyncHttpClient(
            self._model_audit,
            transport=transport,
            event_hooks=event_hooks,
            timeout=timeout,
            verify=ssl_config,
            cert=cert,
            headers=get_default_headers(),
            follow_redirects=True,
        )

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self
