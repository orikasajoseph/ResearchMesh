"""Semantic Scholar 的进程共享 HTTP gate 和脱敏诊断；不修改 SDK 安装文件。"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from weakref import WeakKeyDictionary

import httpx
from semanticscholar import AsyncSemanticScholar
from semanticscholar.ApiRequester import ApiRequester
from semanticscholar.SemanticScholarException import (
    BadQueryParametersException, GatewayTimeoutException,
    InternalServerErrorException, ObjectNotFoundException,
)

INSTANCE_ID = "literature-" + uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_text(value, secret=None, limit=256):
    text = str(value)
    if secret:
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+|(?:x-api-key|api[_-]?key|authorization)\s*[:=]\s*)[^\s,;]+",
                  "[redacted]", text)
    return " ".join(text.split())[:limit]


def safe_rate_headers(headers, secret=None):
    return {name.lower(): safe_text(value, secret, 128) for name, value in headers.items()
            if name.lower() == "retry-after" or re.fullmatch(r"(?:x-)?rate-?limit[-a-z0-9]*", name.lower())}


def transport_failure(error):
    """Classify without storing exception text, URLs, proxy credentials or keys."""
    chain, seen = [], set()
    current = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current)); chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in chain):
        reason, retryable = "TLS_CERTIFICATE_ERROR", False
    elif any(isinstance(e, ssl.SSLError) for e in chain):
        reason, retryable = "TLS_ERROR", False
    elif isinstance(error, httpx.ProxyError):
        reason, retryable = "PROXY_ERROR", False
    elif isinstance(error, httpx.ConnectTimeout):
        reason, retryable = "CONNECT_TIMEOUT", True
    elif isinstance(error, httpx.ReadTimeout):
        reason, retryable = "READ_TIMEOUT", True
    elif isinstance(error, httpx.WriteTimeout):
        reason, retryable = "WRITE_TIMEOUT", True
    elif isinstance(error, httpx.PoolTimeout):
        reason, retryable = "POOL_TIMEOUT", True
    elif any(isinstance(e, socket.gaierror) for e in chain):
        reason, retryable = "DNS_ERROR", True
    elif isinstance(error, httpx.ConnectError):
        reason, retryable = "CONNECT_ERROR", True
    elif isinstance(error, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)):
        reason, retryable = "CONNECTION_INTERRUPTED", True
    else:
        reason, retryable = "OTHER_TRANSPORT_ERROR", False
    known_types = {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ProxyError", "ConnectError",
                   "ReadError", "WriteError", "RemoteProtocolError", "SSLCertVerificationError", "SSLError",
                   "gaierror", "OSError", "TimeoutError", "ConnectionRefusedError"}
    return {"transportError": reason, "transportRetryable": retryable,
            "transportExceptionType": type(error).__name__ if type(error).__name__ in known_types else "OtherError",
            "transportCauseTypes": [type(e).__name__ if type(e).__name__ in known_types else "OtherError" for e in chain]}


class ProcessRateGate:
    """asyncio.Lock 串行同一 loop，极短线程锁协调跨线程/loop 的同一个 gate。

    不持线程锁等待网络或 sleep；所有真实 HTTP 请求串行，开始时间至少相隔 1.2 秒。
    """
    def __init__(self, interval=1.2):
        self.interval = interval
        self._guard = threading.Lock()
        self._loop_locks = WeakKeyDictionary()
        self._busy = False
        self._last_started = None
        self._last_at = None
        self._sequence = 0
        self._events = []

    @asynccontextmanager
    async def enter(self):
        loop = asyncio.get_running_loop()
        with self._guard:
            lock = self._loop_locks.setdefault(loop, asyncio.Lock())
        queued = time.monotonic()
        async with lock:
            while True:
                with self._guard:
                    current = time.monotonic()
                    remaining = 0 if self._last_started is None else self.interval - (current - self._last_started)
                    if not self._busy and remaining <= 0:
                        previous = self._last_at
                        self._busy = True
                        self._last_started = current
                        self._last_at = now()
                        self._sequence += 1
                        timing = {"requestSequence": self._sequence,
                                  "rateGateWaitMs": round((current - queued) * 1000, 3),
                                  "previousRequestAt": previous, "actualRequestAt": self._last_at}
                        break
                await asyncio.sleep(max(0.05, remaining))
            try:
                yield timing
            finally:
                with self._guard:
                    self._busy = False

    def append(self, event):
        with self._guard:
            self._events.append(deepcopy(event))
            # 日志只在内存保存最近请求，避免长驻服务无限增长。
            del self._events[:-1000]

    def snapshot(self):
        with self._guard:
            return deepcopy(self._events)


GLOBAL_RATE_GATE = ProcessRateGate()


class ObservedRequester(ApiRequester):
    """SDK requester 扩展点：所有该 client 的端点统一经过 gate，不含内层重试。"""
    def __init__(self, timeout, api_key, *, source="literature-agent", subtask_id=None,
                 transport=None, gate=None, max_requests=None):
        super().__init__(timeout, retry=False)
        self._key = api_key
        self.source = source if source in {"literature-agent", "run_research", "run_literature", "cold-start-diagnostic"} else "literature-agent"
        self.subtask_id = subtask_id
        self.transport = transport
        self.gate = gate if gate is not None else GLOBAL_RATE_GATE
        self.events = []
        self.max_requests = max_requests

    async def get_data_async(self, url, parameters, headers, payload=None):
        if self.max_requests is not None and len(self.events) >= self.max_requests:
            raise RuntimeError("SEMANTIC_SCHOLAR_REQUEST_BUDGET_EXHAUSTED")
        # 只允许既定的官方主机，防止把 Key 或敏感 URL 带到错误地址。
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "api.semanticscholar.org" or parts.username or parts.query or parts.fragment:
            raise ValueError("SEMANTIC_SCHOLAR_ENDPOINT_REJECTED")
        parameters = parameters.lstrip("&")
        method = "POST" if payload is not None else "GET"
        async with httpx.AsyncClient(transport=self.transport, follow_redirects=False) as client:
            try:
                request = client.build_request(method, url, params=parameters,
                                               headers=headers, json=payload, timeout=self.timeout)
            except Exception:
                raise RuntimeError("SEMANTIC_SCHOLAR_REQUEST_PREPARATION_FAILED") from None
            # 客户端和 Request 先准备好，gate 紧贴真正 send，避免初始化耗时侵蚀间隔。
            async with self.gate.enter() as timing:
                event = {**timing, "instanceId": INSTANCE_ID, "endpoint": parts.path,
                         "requestStartedAt": timing["actualRequestAt"], "requestFinishedAt": None,
                         "httpStatus": None, "subtaskId": self.subtask_id,
                         "requestSource": self.source, "apiKeyConfigured": bool(self._key),
                         "apiKeyAttached": bool(request.headers.get("x-api-key")),
                         "requestTimeouts": dict(request.extensions.get("timeout", {})),
                         "retryAfterHeader": None, "rateLimitHeaders": {},
                         "requestParameters": {k: safe_text(v[0], self._key, 500)
                             for k, v in parse_qs(parameters).items() if k in {"query", "year", "limit", "offset"}}}
                started = time.monotonic()
                response = None
                try:
                    response = await client.send(request)
                    event["httpStatus"] = response.status_code
                    rate_headers = safe_rate_headers(response.headers, self._key)
                    event.update(rateLimitHeaders=rate_headers,
                                 retryAfterHeader=rate_headers.get("retry-after"))
                    if response.status_code != 200:
                        try:
                            body = response.json()
                        except (ValueError, UnicodeError):
                            body = {}
                        message = body.get("message") or body.get("error") if isinstance(body, dict) else None
                        event["responseReason"] = safe_text(message or "HTTP_" + str(response.status_code), self._key)
                except Exception as error:
                    event.update(transport_failure(error))
                    raise RuntimeError("SEMANTIC_SCHOLAR_TRANSPORT_FAILED") from None
                finally:
                    event.update(requestFinishedAt=now(), durationMs=round((time.monotonic() - started) * 1000, 3))
                    self.events.append(deepcopy(event))
                    self.gate.append(event)
                status = response.status_code
                # 保留 SDK/Provider 原有异常分类；不回显原始服务端消息。
                if status == 429:
                    raise ConnectionRefusedError("HTTP status 429 Too Many Requests.")
                if status == 400:
                    raise BadQueryParametersException("HTTP 400")
                if status in (401, 403):
                    raise PermissionError("HTTP " + str(status))
                if status == 404:
                    raise ObjectNotFoundException("HTTP 404")
                if status == 504:
                    raise GatewayTimeoutException("HTTP 504")
                if status >= 500:
                    raise InternalServerErrorException("HTTP " + str(status))
                if status != 200:
                    raise RuntimeError("SEMANTIC_SCHOLAR_HTTP_REJECTED")
                return response.json()


def create_monitored_client(config, *, source="literature-agent", subtask_id=None, transport=None, gate=None):
    client = AsyncSemanticScholar(api_key=config.api_key, timeout=config.timeout_seconds, retry=False)
    # 替换实例扩展点而非 SDK 文件；SDK 的搜索参数、分页和 Paper 解析保持原样。
    client._requester = ObservedRequester(config.timeout_seconds, config.api_key,
                                         source=source, subtask_id=subtask_id, transport=transport, gate=gate,
                                         max_requests=1 if os.environ.get("RESEARCHMESH_S1_SINGLE_REQUEST") == "1" else None)
    return client


def client_diagnostics(client, configured_key):
    requester = getattr(client, "_requester", None)
    observed = isinstance(requester, ObservedRequester)
    events = deepcopy(requester.events) if observed else []
    return {"instanceId": INSTANCE_ID, "apiKeyConfigured": bool(configured_key),
            "apiKeyAttached": bool(events and events[-1]["apiKeyAttached"]),
            "httpObservationAvailable": observed, "httpRequests": events,
            "processHttpRequests": GLOBAL_RATE_GATE.snapshot(),
            "retryAfterHeader": events[-1].get("retryAfterHeader") if events else None,
            "rateLimitHeaders": events[-1].get("rateLimitHeaders", {}) if events else {}}
