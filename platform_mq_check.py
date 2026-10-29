"""Connect-only platform diagnostic; never create groups, queues or AIP tasks."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import socket
import ssl
from time import monotonic

import aio_pika
from aio_pika.connection import make_url
from aiormq.exceptions import (
    AMQPConnectionError, ChannelAccessRefused, ChannelClosed,
    ChannelInvalidStateError, ConnectionClosed,
)
from acps_sdk.aip import GroupLeaderMqClient
from acps_sdk.aip.aip_group_runtime import build_external_connection_url


ROOT = Path(__file__).resolve().parent
HOST, PORT, VHOST = "wt.ioa.pub", 5671, "acps"


class CheckError(ValueError):
    """Only stable, non-secret error codes are exposed."""


@dataclass(frozen=True)
class CertificateInfo:
    common_names: tuple[str, ...]
    not_before: datetime
    not_after: datetime
    client_auth: bool


def certificate_info(path: Path) -> CertificateInfo:
    # Optional dependency: normal offline regression does not require a crypto install.
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    cert = x509.load_pem_x509_certificate(path.read_bytes())
    try:
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        client_auth = ExtendedKeyUsageOID.CLIENT_AUTH in usage
    except x509.ExtensionNotFound:
        client_auth = False
    return CertificateInfo(
        tuple(item.value for item in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)),
        cert.not_valid_before_utc, cert.not_valid_after_utc, client_auth,
    )


def validate_config(aic: str, credentials_dir: Path, timeout: float) -> Path:
    if len(aic) > 256 or not re.fullmatch(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)+", aic):
        raise CheckError("INVALID_LEADER_AIC")
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise CheckError("INVALID_TIMEOUT")
    credentials_dir = credentials_dir.resolve()
    # Credentials must stay outside the checkout even if Git happens to ignore them.
    if credentials_dir.is_relative_to(ROOT):
        raise CheckError("CREDENTIALS_MUST_BE_OUTSIDE_REPOSITORY")
    return credentials_dir


def certificate_context(credentials_dir: Path, aic: str) -> ssl.SSLContext:
    paths = [credentials_dir / name for name in
             ("leader-cert.pem", "leader-key.pem", "trust-bundle.pem")]
    if not all(path.is_file() for path in paths):
        raise CheckError("CERTIFICATE_FILE_MISSING")
    # Also reject a symlink whose resolved target is inside the checkout.
    if any(path.resolve().is_relative_to(ROOT) for path in paths):
        raise CheckError("CREDENTIALS_MUST_BE_OUTSIDE_REPOSITORY")
    info = certificate_info(paths[0])
    if info.common_names != (aic,):
        raise CheckError("CERTIFICATE_AIC_MISMATCH")
    now = datetime.now(timezone.utc)
    if not info.not_before <= now <= info.not_after:
        raise CheckError("CERTIFICATE_NOT_CURRENTLY_VALID")
    if not info.client_auth:
        raise CheckError("CERTIFICATE_CLIENT_AUTH_REQUIRED")
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=str(paths[2]))
    # OpenSSL uses the local private key for proof of possession. Never print it.
    ctx.load_cert_chain(str(paths[0]), str(paths[1]))
    return ctx


def classify_error(exc: Exception) -> str:
    if isinstance(exc, CheckError):
        return str(exc)
    if isinstance(exc, ImportError):
        return "OPTIONAL_DEPENDENCY_MISSING"
    if isinstance(exc, TimeoutError):
        return "CONNECTION_TIMEOUT"
    if isinstance(exc, socket.gaierror):
        return "DNS_LOOKUP_FAILED"
    if isinstance(exc, ChannelAccessRefused):
        return "AMQP_ACCESS_REFUSED"
    if isinstance(exc, ChannelInvalidStateError):
        return "AMQP_CHANNEL_INVALID_STATE"
    if isinstance(exc, ChannelClosed):
        return "AMQP_CHANNEL_CLOSED"
    if isinstance(exc, (AMQPConnectionError, ConnectionClosed, ConnectionError)):
        return "AMQP_CONNECTION_FAILED"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "SERVER_CERTIFICATE_VERIFICATION_FAILED"
    if isinstance(exc, ssl.SSLError):
        return "TLS_OR_LOCAL_KEY_ERROR"
    if isinstance(exc, FileNotFoundError):
        return "CERTIFICATE_FILE_MISSING"
    if "Authentication" in type(exc).__name__:
        return "AMQP_AUTHENTICATION_FAILED"
    if isinstance(exc, PermissionError):
        return "LOCAL_FILE_PERMISSION_DENIED"
    return "CONNECTION_CHECK_FAILED"


async def check_connection(*, aic: str, credentials_dir: Path, execute: bool = False,
                           timeout: float = 20) -> dict:
    started = monotonic()
    record = {
        "timeUtc": datetime.now(timezone.utc).isoformat(),
        "scope": "PLATFORM_MTLS_AMQP_CONNECT_ONLY" if execute else "OFFLINE_CERTIFICATE_CHECK",
        "endpoint": f"amqps://{HOST}:{PORT}/{VHOST}",
        "offlineChecksPassed": False, "networkAttempted": False,
        "amqpConnected": False, "leaderChannelOpened": False, "connectionClosed": None,
        "groupsCreated": 0, "tasksSent": 0, "llmCalls": 0,
        "protocolCompleted": False, "businessReady": False, "error": None,
    }
    connection = None
    try:
        directory = validate_config(aic, credentials_dir, timeout)
        # Publish the identifier only after input validation; never echo arbitrary input.
        record["leaderAic"] = aic
        ctx = certificate_context(directory, aic)
        record["offlineChecksPassed"] = True
        if execute:
            url = build_external_connection_url(
                host=HOST, port=PORT, vhost=VHOST, connection_name=f"leader-probe-{aic}",
            )
            connection = aio_pika.Connection(make_url(url), ssl_context=ctx)
            record["networkAttempted"] = True
            async with asyncio.timeout(timeout):
                # Non-robust connection: one attempt, no automatic reconnect loop.
                await connection.connect(timeout=timeout)
                record["amqpConnected"] = not connection.is_closed
                client = GroupLeaderMqClient(
                    leader_aic=aic, rabbitmq_host=HOST, rabbitmq_port=PORT,
                    rabbitmq_vhost=VHOST, rabbitmq_user=None, rabbitmq_password=None,
                    ssl_context=ctx, connection=connection, connection_owner=False,
                    identity_binding_enabled=True,
                )
                # With an injected open connection the SDK opens a channel, not a group.
                await client.connect()
                record["leaderChannelOpened"] = client.is_connected
    except Exception as exc:
        record["error"] = {"code": classify_error(exc), "type": type(exc).__name__}
    finally:
        if connection is not None:
            try:
                # Explicitly close the owned connection: SDK close() alone is not
                # sufficient while the client is still in its initial group state.
                await asyncio.wait_for(connection.close(), timeout=5)
                record["connectionClosed"] = bool(connection.is_closed)
                if not connection.is_closed:
                    record["cleanupError"] = "CONNECTION_REMAINS_OPEN"
            except Exception as exc:
                record["connectionClosed"] = False
                record["cleanupError"] = type(exc).__name__
        record["elapsedMs"] = round((monotonic() - started) * 1000)
    record["passed"] = bool(
        record["error"] is None and record["offlineChecksPassed"]
        and (not execute or (record["amqpConnected"] and record["leaderChannelOpened"]
                             and record["connectionClosed"] is True))
    )
    return record
