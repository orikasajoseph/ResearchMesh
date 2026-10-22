
"""Trusted Inbox + Group connector for a registered Literature Agent."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

from acps_sdk.aip import (
    GroupPartnerMqClient,
    GroupMgmtCommandType,
)

from aip.group_literature_handler import LiteratureGroupHandler
from aip.group_task_store import DurableTaskStore


def tls_context(credential_dir: Path) -> ssl.SSLContext:
    """Build the official mTLS client context."""

    ctx = ssl.create_default_context(
        ssl.Purpose.SERVER_AUTH
    )

    ctx.load_verify_locations(
        cafile=str(credential_dir / "trust-bundle.pem")
    )

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

    leader = getattr(
        getattr(invitation, "group", None),
        "leader",
        None,
    )

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
) -> None:
    """Run the formally registered Literature Group Partner."""

    if not partner_aic.strip() or not leader_aic.strip():
        raise ValueError(
            "REAL_PARTNER_AND_LEADER_AICS_REQUIRED"
        )

    # Production Group tasks must not fall back to memory-only dedup.
    if task_store is None:
        raise RuntimeError(
            "DURABLE_TASK_STORE_REQUIRED"
        )

    client = GroupPartnerMqClient(
        partner_aic=partner_aic,
        rabbitmq_host="wt.ioa.pub",
        rabbitmq_port=5671,
        rabbitmq_vhost="acps",
        rabbitmq_user=None,
        rabbitmq_password=None,
        ssl_context=tls_context(credential_dir),
        identity_binding_enabled=True,
        robust_connection=True,
    )

    # Reuse the existing Literature business handler.
    # DurableTaskStore is injected before any TaskCommand arrives.
    handler = LiteratureGroupHandler(
        client,
        leader_aic=leader_aic,
        task_store=task_store,
    )

    client.set_command_handler(handler.on_command)

    # --------------------------------------------
    # Group lifecycle observability
    # --------------------------------------------
    async def on_mgmt_command(command: Any) -> None:
        if (
            command.command == GroupMgmtCommandType.DISBAND
            and command.groupId == client.group_id
            and command.senderRole == "leader"
            and command.senderId == leader_aic
            and command.must_respond(partner_aic)
        ):
            print("DISBAND_RECEIVED", flush=True)
            print("Group:", command.groupId, flush=True)

    async def on_disconnect(
        _client: GroupPartnerMqClient,
        left_group_id: str | None,
    ) -> None:
        print("GROUP_LEFT", flush=True)
        print("Group:", left_group_id, flush=True)

        # The SDK may close the shared AMQP connection during
        # Group cleanup. Do not claim the Inbox is still active.
        print("INBOX_RESTART_REQUIRED", flush=True)

    client.set_mgmt_command_handler(on_mgmt_command)
    client.set_disconnect_handler(on_disconnect)

    # --------------------------------------------
    # Invitation policy
    # --------------------------------------------
    async def policy(invitation: Any) -> bool:
        return invitation_allowed(
            invitation,
            allowed_leader_aic=leader_aic,
            current_group=client.group_id,
        )

    client.set_invitation_handler(policy)

    # --------------------------------------------
    # Inbox invitation handling
    # --------------------------------------------
    async def on_invitation(invitation: Any) -> None:
        joined = await client.join_group_from_invitation(
            invitation
        )

        print(
            "GROUP_JOINED"
            if joined
            else "GROUP_INVITATION_REJECTED",
            flush=True,
        )

        if joined:
            print(
                "Group:",
                client.group_id,
                flush=True,
            )

    # --------------------------------------------
    # Connection lifecycle
    # --------------------------------------------
    try:
        await client.connect()
        print("AMQPS_CONNECTED", flush=True)

        await client.start_inbox_consuming(
            on_invitation
        )

        print("INBOX_LISTENING", flush=True)
        print(
            "Waiting for permitted Leader invitation. "
            "Press Ctrl+C to stop.",
            flush=True,
        )

        await asyncio.Event().wait()

    finally:
        await client.close()
