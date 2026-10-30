"""Bounded HTTP transport for the platform Discovery API.

This module deliberately does not guess an authentication mechanism.  The
platform's public Discovery endpoint currently uses ordinary HTTPS; callers
that later receive an authenticated client from the platform may inject that
``httpx.AsyncClient`` without changing the resolver or business pipeline.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import ssl
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx


DISCOVERY_PATH = "/acps-adp-v2/discover"


class DiscoveryHttpTransport:
    """Send one Discovery request with no retry and retain a redacted audit."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        cert_file: Path | None = None,
        key_file: Path | None = None,
        trust_bundle_file: Path | None = None,
    ) -> None:
        parsed = urlsplit(base_url.strip())
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("INVALID_DISCOVERY_URL")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("INVALID_DISCOVERY_HTTP_TIMEOUT")
        mtls_files = (cert_file, key_file, trust_bundle_file)
        if any(mtls_files) and not all(mtls_files):
            raise ValueError("INCOMPLETE_DISCOVERY_MTLS_CONFIGURATION")
        if client is not None and any(mtls_files):
            raise ValueError("AMBIGUOUS_DISCOVERY_HTTP_CLIENT")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._owns_client = client is None
        self._mtls = bool(cert_file)
        if client is None:
            verify: bool | ssl.SSLContext = True
            if cert_file is not None and key_file is not None and trust_bundle_file is not None:
                for path in (cert_file, key_file, trust_bundle_file):
                    if not path.is_file():
                        raise ValueError("DISCOVERY_MTLS_FILE_NOT_FOUND")
                # Keep public Web PKI roots for the HTTPS server certificate,
                # then add the ACPs trust bundle used by the Leader identity.
                context = ssl.create_default_context()
                context.load_verify_locations(cafile=str(trust_bundle_file))
                context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                verify = context
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                verify=verify,
            )
        else:
            self._client = client
        self.records: list[dict[str, Any]] = []

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        record: dict[str, Any] = {
            "scope": "PLATFORM_DISCOVERY_HTTP_NO_RETRY",
            "url": self.base_url + DISCOVERY_PATH,
            "requestType": request.get("type"),
            "attempts": 1,
            "authenticationMode": ("injected-client" if not self._owns_client else
                                   "leader-mtls" if self._mtls else "platform-public-https"),
            "httpStatus": None,
            "responseBytes": None,
            "responseSha256": None,
            "latencyMs": None,
            "error": None,
        }
        try:
            response = await self._client.post(self.base_url + DISCOVERY_PATH, json=request)
            record["httpStatus"] = response.status_code
            record["responseBytes"] = len(response.content)
            record["responseSha256"] = hashlib.sha256(response.content).hexdigest()
            if response.status_code != 200:
                return {"error": {"code": response.status_code, "message": "DISCOVERY_HTTP_ERROR"}}
            try:
                value = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return value if isinstance(value, dict) else {}
        except httpx.TimeoutException:
            record["error"] = "DISCOVERY_HTTP_TIMEOUT"
            raise
        except httpx.HTTPError:
            record["error"] = "DISCOVERY_HTTP_TRANSPORT_ERROR"
            raise
        finally:
            record["latencyMs"] = round((perf_counter() - started) * 1000, 3)
            self.records.append(record)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
