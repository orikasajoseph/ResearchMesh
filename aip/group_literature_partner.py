"""Trusted Inbox + Group connector for a registered Literature Agent."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

from acps_sdk.aip import (
    GroupMgmtCommandType,
)

from aip.audited_group_client import AuditedGroupPartnerMqClient as GroupPartnerMqClient
from aip.group_literature_handler import LiteratureGroupHandler
from aip.group_task_store import DurableTaskStore
from aip.literature_unified_executor import execute_unified_literature
from aip.group_partner_service import serve_client_cycles


def tls_context(credential_dir: Path) -> ssl.SSLContext:
    """Build the official mTLS client context."""

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=str(credential_dir / "trust-bundle.pem"))
    ctx.load_cert_chain(
        certfile=str(credential_dir / "agent-cert.pem"),
        keyfile=str(credential_dir / "agent-key.pem"),
    )
    return ctx


def invitation_allowed(
    invitation: Any,
    *,
    allowed_leader_aic: str,
    current_group: str | None,
) -> bool:
    """Reject invitations that do not come from the allowed Leader."""
    if not allowed_leader_aic or current_group:
        return False
    leader = getattr(getattr(invitation, "group", None), "leader", None)
    if getattr(leader, "aic", None) != allowed_leader_aic:
        return False
    sender = getattr(invitation, "senderId", None)
    return sender == allowed_leader_aic


async def serve(
    *,
    partner_aic: str,
    leader_aic: str,
    credential_dir: Path,
    task_store: DurableTaskStore,
    audit=None,
) -> None:
    """Run the formal Literature Group Partner with durable task handling."""
    if not partner_aic.strip() or not leader_aic.strip():
        raise ValueError("REAL_PARTNER_AND_LEADER_AICS_REQUIRED")
    if task_store is None:
        raise RuntimeError("DURABLE_TASK_STORE_REQUIRED")

    context = tls_context(credential_dir)

    def client_factory():
        client = GroupPartnerMqClient(
            partner_aic=partner_aic, rabbitmq_host="wt.ioa.pub", rabbitmq_port=5671,
            rabbitmq_vhost="acps", rabbitmq_user=None, rabbitmq_password=None,
            ssl_context=context, identity_binding_enabled=True, robust_connection=True)
        client.audit = audit
        return client

    def configure(client):
        # Each fresh SDK client gets a fresh handler; durable SQLite remains the
        # cross-cycle authority for duplicate Task prevention.
        handler = LiteratureGroupHandler(
            client, leader_aic=leader_aic, executor=execute_unified_literature,
            task_store=task_store, audit=audit)
        client.set_command_handler(handler.on_command)

        async def on_mgmt_command(command: Any) -> None:
            if (command.command == GroupMgmtCommandType.DISBAND
                    and command.groupId == client.group_id
                    and command.senderRole == "leader" and command.senderId == leader_aic
                    and command.must_respond(partner_aic)):
                if audit:
                    audit.emit("DISBAND_RECEIVED", groupId=command.groupId,
                               managementSessionId=getattr(command, "sessionId", None),
                               agentAic=partner_aic, pendingLeave=client._pending_leave)
                print("DISBAND_RECEIVED", flush=True)
                print("Group:", command.groupId, flush=True)

        async def on_disconnect(current: GroupPartnerMqClient,
                                left_group_id: str | None) -> None:
            if audit:
                audit.emit("GROUP_LEFT", groupId=left_group_id, agentAic=partner_aic,
                           **current.resource_snapshot())
            print("GROUP_LEFT", flush=True)
            print("Group:", left_group_id, flush=True)

        async def policy(invitation: Any) -> bool:
            return invitation_allowed(invitation, allowed_leader_aic=leader_aic,
                                      current_group=client.group_id)

        async def on_invitation(invitation: Any) -> None:
            joined = await client.join_group_from_invitation(invitation)
            print("GROUP_JOINED" if joined else "GROUP_INVITATION_REJECTED", flush=True)
            if audit:
                audit.emit("GROUP_JOINED" if joined else "GROUP_INVITATION_REJECTED",
                           groupId=client.group_id, agentAic=partner_aic)
            if joined:
                print("Group:", client.group_id, flush=True)

        client.set_mgmt_command_handler(on_mgmt_command)
        client.set_disconnect_handler(on_disconnect)
        client.set_invitation_handler(policy)
        return on_invitation

    try:
        print("Waiting for permitted Leader invitation. Press Ctrl+C to stop.", flush=True)
        await serve_client_cycles(client_factory, configure, audit=audit,
                                  partner_aic=partner_aic)
    except BaseException as error:
        if audit:
            audit.emit("PARTNER_EXIT", exceptionCategory="cancelled" if isinstance(error, asyncio.CancelledError) else "partner_failed")
        raise
