"""Invitation-only platform diagnostic; never execute a scientific task."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

import aio_pika
from aio_pika.connection import make_url
from aiormq.exceptions import ChannelNotFoundEntity
from acps_sdk.aic import validate_aic_format
from acps_sdk.aip import ACSObject, GroupLeaderMqClient, GroupMgmtCommand, GroupMgmtCommandType, GroupMgmtResult
from acps_sdk.aip.aip_group_auth import GroupAclClient, GroupAclError
from acps_sdk.aip.aip_group_runtime import (
    build_external_connection_url, build_group_exchange_name,
    build_group_queue_name, build_inbox_queue_name,
)

from platform_mq_check import (
    HOST, PORT, VHOST, CheckError, certificate_context, classify_error, validate_config,
)

AUTH_SERVICE_URL = "https://wt.ioa.pub/mq-auth/"


@dataclass(frozen=True)
class InviteConfig:
    leader_aic: str
    partner_aic: str
    credentials_dir: Path
    auth_service_url: str = AUTH_SERVICE_URL
    timeout: float = 20
    join_timeout: float = 45
    hold_seconds: float = 30


def validate(config: InviteConfig, mode: str, execute: bool, partner_ready: bool) -> Path:
    if mode not in {"offline", "acl-check", "invite"}:
        raise CheckError("INVALID_MODE")
    if execute != (mode != "offline"):
        raise CheckError("LIVE_MODE_REQUIRES_EXECUTE" if not execute else "OFFLINE_MODE_CANNOT_EXECUTE")
    directory = validate_config(config.leader_aic, config.credentials_dir, config.timeout)
    for aic in (config.leader_aic, config.partner_aic):
        valid, _ = validate_aic_format(aic)
        if not valid:
            raise CheckError("INVALID_AIC_FORMAT")
    if config.leader_aic == config.partner_aic:
        raise CheckError("PARTNER_MUST_DIFFER_FROM_LEADER")
    if config.auth_service_url.rstrip("/") != AUTH_SERVICE_URL.rstrip("/"):
        raise CheckError("UNCONFIRMED_AUTH_SERVICE_URL")
    parsed = urlsplit(config.auth_service_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CheckError("UNCONFIRMED_AUTH_SERVICE_URL")
    if not math.isfinite(config.join_timeout) or not 1 <= config.join_timeout <= 120:
        raise CheckError("INVALID_JOIN_TIMEOUT")
    if not math.isfinite(config.hold_seconds) or not 0 <= config.hold_seconds <= 60:
        raise CheckError("INVALID_HOLD_SECONDS")
    if mode == "invite" and not partner_ready:
        raise CheckError("PARTNER_READY_CONFIRMATION_REQUIRED")
    return directory


class InvitationOnlyMqClient(GroupLeaderMqClient):
    """Use SDK wire messages, with a hard guard against task/data-plane sends."""

    async def publish_message(self, message):
        if not (isinstance(message, GroupMgmtCommand)
                and message.command == GroupMgmtCommandType.DISBAND):
            raise CheckError("TASK_AND_OTHER_COMMANDS_DISABLED")
        return await super().publish_message(message)


class JoinConfirmation:
    """Accept only an SDK identity-checked status for this target and fresh group."""

    def __init__(self, partner_aic: str, group_id: str):
        self.partner_aic = partner_aic
        self.group_id = group_id
        self.event = asyncio.Event()
        self.message_id: str | None = None
        self.unexpected_task_messages = 0

    async def on_message(self, message) -> None:
        # SDK start_consuming validates AMQP user_id == senderId and groupId first.
        if getattr(message, "type", None) in {"task-command", "task-result"}:
            self.unexpected_task_messages += 1
            return
        if (isinstance(message, GroupMgmtResult)
                and message.senderRole == "partner"
                and message.senderId == self.partner_aic
                and message.groupId == self.group_id
                and message.status.connected):
            self.message_id = message.id
            self.event.set()


async def delete_resource(connection, kind: str, name: str) -> None:
    """Strict cleanup: absent is OK; access/transport failures must not be swallowed."""
    channel = await connection.channel()
    try:
        if kind == "queue":
            await channel.queue_delete(name, if_unused=False, if_empty=False)
        else:
            await channel.exchange_delete(name, if_unused=False)
    except ChannelNotFoundEntity:
        pass
    finally:
        if not channel.is_closed:
            await channel.close()


async def run_invitation(config: InviteConfig, *, mode: str = "offline", execute: bool = False,
                         partner_ready: bool = False,
                         checkpoint: Callable[[dict], None] | None = None) -> dict:
    started = monotonic()
    prefix = "rm-acl-check" if mode == "acl-check" else "rm-invite"
    # Never accept a user-selected group ID: rollback must only affect this run.
    group_id = f"{prefix}-{uuid4().hex}"
    record = {
        "timeUtc": datetime.now(timezone.utc).isoformat(), "mode": mode,
        "scope": {"offline": "OFFLINE_INVITATION_PREFLIGHT", "acl-check": "PLATFORM_TEMPORARY_ACL_CHECK",
                  "invite": "PLATFORM_INVITATION_ONLY"}.get(mode, "INVALID_MODE"),
        "groupId": group_id, "partnerSelection": "explicit_aic_not_adp",
        "offlineChecksPassed": False, "networkAttempted": False,
        "aclWriteAttempted": False, "leaderAuthorized": False, "partnerAuthorized": False,
        "amqpConnected": False, "groupCreationAttempted": False, "groupCreated": False,
        "invitationPublishAttempted": False, "invitationPublished": False,
        "partnerJoinConfirmed": False, "cleanupAttempted": False, "cleanupSucceeded": None,
        "aclDeleted": None, "groupResourcesDeleted": None, "connectionClosed": None,
        "taskCommandsSent": 0, "llmCalls": 0, "protocolCompleted": False, "businessReady": False,
        "events": [], "error": None, "cleanupErrors": [],
    }
    acl = connection = client = confirmation = None

    def event(name: str):
        record["events"].append({"event": name, "timeUtc": datetime.now(timezone.utc).isoformat()})
        if checkpoint:
            try:
                checkpoint(record)
            except Exception as exc:
                record["journalError"] = type(exc).__name__
                # A broken log must stop new mutations, but never prevent rollback.
                if not record["cleanupAttempted"]:
                    raise CheckError("JOURNAL_WRITE_FAILED") from None

    async def bounded(awaitable):
        return await asyncio.wait_for(awaitable, timeout=config.timeout)

    async def cleanup_step(name, operation):
        try:
            await asyncio.wait_for(operation(), timeout=min(config.timeout, 5))
            return True
        except Exception as exc:
            record["cleanupErrors"].append({"step": name, "type": type(exc).__name__})
            return False

    try:
        directory = validate(config, mode, execute, partner_ready)
        record.update(leaderAic=config.leader_aic, partnerAic=config.partner_aic,
                      authServiceUrl=AUTH_SERVICE_URL,
                      partnerInbox=build_inbox_queue_name(config.partner_aic))
        ctx = certificate_context(directory, config.leader_aic)
        record["offlineChecksPassed"] = True
        event("OFFLINE_PREFLIGHT_PASSED")
        if execute:
            acl = GroupAclClient(base_url=AUTH_SERVICE_URL, ssl_context=ctx, timeout=config.timeout)
            record["networkAttempted"] = record["aclWriteAttempted"] = True
            event("LEADER_ACL_WRITE_ATTEMPT")
            await bounded(acl.add_member(leader_aic=config.leader_aic, group_id=group_id,
                                         member_aic=config.leader_aic))
            record["leaderAuthorized"] = True
            event("LEADER_ACL_AUTHORIZED")
            if mode == "invite":
                url = build_external_connection_url(host=HOST, port=PORT, vhost=VHOST,
                                                    connection_name=f"invite-only-{config.leader_aic}")
                connection = aio_pika.Connection(make_url(url), ssl_context=ctx)
                await bounded(connection.connect(timeout=config.timeout))
                record["amqpConnected"] = not connection.is_closed
                if not record["amqpConnected"]:
                    raise CheckError("AMQP_CONNECTION_NOT_OPEN")
                client = InvitationOnlyMqClient(
                    leader_aic=config.leader_aic, rabbitmq_host=HOST, rabbitmq_port=PORT,
                    rabbitmq_vhost=VHOST, rabbitmq_user=None, rabbitmq_password=None,
                    ssl_context=ctx, connection=connection, connection_owner=False,
                    identity_binding_enabled=True,
                )
                await bounded(client.connect())
                record["groupCreationAttempted"] = True
                event("GROUP_CREATE_ATTEMPT")
                created = await bounded(client.create_group(group_id=group_id, partner_acs_list=[]))
                if created != group_id:
                    raise CheckError("UNEXPECTED_GROUP_ID")
                record["groupCreated"] = True
                event("GROUP_CREATED")
                confirmation = JoinConfirmation(config.partner_aic, group_id)
                client.set_message_handler(confirmation.on_message)
                await bounded(client.start_consuming())
                await bounded(acl.add_member(leader_aic=config.leader_aic, group_id=group_id,
                                             member_aic=config.partner_aic))
                record["partnerAuthorized"] = True
                event("PARTNER_ACL_AUTHORIZED")
                record["invitationPublishAttempted"] = True
                event("INVITATION_PUBLISH_ATTEMPT")
                await bounded(client.invite_via_inbox(
                    ACSObject(aic=config.partner_aic), partner_inbox=record["partnerInbox"],
                    timeout_seconds=math.ceil(config.join_timeout),
                ))
                # Do not save the invitation object/token or raw transport payload.
                record["invitationPublished"] = True
                event("INVITATION_PUBLISHED")
                try:
                    await asyncio.wait_for(confirmation.event.wait(), timeout=config.join_timeout)
                except TimeoutError:
                    raise CheckError("PARTNER_JOIN_TIMEOUT") from None
                record["partnerJoinConfirmed"] = True
                record["joinMessageId"] = confirmation.message_id
                event("GROUP_JOINED_CONFIRMED")
                # Finite observation window for Wu to see GROUP_JOINED; never start tasks.
                await asyncio.sleep(config.hold_seconds)
    except asyncio.CancelledError:
        record["error"] = {"code": "INTERRUPTED", "type": "CancelledError"}
    except Exception as exc:
        code = "GROUP_ACL_REQUEST_FAILED" if isinstance(exc, GroupAclError) else classify_error(exc)
        record["error"] = {"code": code, "type": type(exc).__name__}
    finally:
        if execute and record["aclWriteAttempted"]:
            record["cleanupAttempted"] = True
            event("CLEANUP_STARTED")
            if client is not None and record["groupCreated"]:
                await cleanup_step("notify_group_disband", client.broadcast_group_shutdown)
            if client is not None and record["groupCreationAttempted"]:
                # SDK stops its consumer first. Strict resource deletion below catches
                # errors that the SDK's best-effort helper may log and suppress.
                await cleanup_step("stop_sdk_consumer", client.cleanup_group_resources)
            if record["groupCreationAttempted"]:
                results = []
                if connection is not None and not connection.is_closed:
                    for kind, name in (
                        ("queue", build_group_queue_name(config.leader_aic, group_id, config.partner_aic)),
                        ("queue", build_group_queue_name(config.leader_aic, group_id, config.leader_aic)),
                        ("exchange", build_group_exchange_name(config.leader_aic, group_id)),
                    ):
                        results.append(await cleanup_step(
                            f"delete_{kind}", lambda k=kind, n=name: delete_resource(connection, k, n)))
                    record["groupResourcesDeleted"] = all(results)
                else:
                    record["groupResourcesDeleted"] = False
                    record["cleanupErrors"].append({"step": "delete_group_resources", "type": "ConnectionUnavailable"})
            if acl is not None:
                record["aclDeleted"] = await cleanup_step("delete_own_acl", lambda: acl.delete_group(
                    leader_aic=config.leader_aic, group_id=group_id))
            if connection is not None:
                await cleanup_step("close_connection", connection.close)
                record["connectionClosed"] = bool(connection.is_closed)
                if not connection.is_closed:
                    record["cleanupErrors"].append({"step": "close_connection", "type": "ConnectionRemainsOpen"})
            if acl is not None:
                await cleanup_step("close_acl_client", acl.aclose)
            record["cleanupSucceeded"] = not record["cleanupErrors"]
            event("CLEANUP_FINISHED")
        if confirmation is not None:
            record["unexpectedTaskMessagesReceived"] = confirmation.unexpected_task_messages
        record["elapsedMs"] = round((monotonic() - started) * 1000)
        record["passed"] = bool(record["error"] is None and record["offlineChecksPassed"] and (
            mode == "offline" or (record["leaderAuthorized"] and record["cleanupSucceeded"] is True
                                  and (mode == "acl-check" or record["partnerJoinConfirmed"]))))
        if checkpoint:
            try:
                checkpoint(record)
            except Exception as exc:
                record["journalError"] = type(exc).__name__
                record["passed"] = False
    return record
