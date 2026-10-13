"""Trusted Inbox + Group connector for an already registered Literature Agent."""
from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

from acps_sdk.aip import GroupPartnerMqClient
from aip.group_literature_handler import LiteratureGroupHandler


def tls_context(credential_dir: Path) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=str(credential_dir / "trust-bundle.pem"))
    ctx.load_cert_chain(
        certfile=str(credential_dir / "agent-cert.pem"),
        keyfile=str(credential_dir / "agent-key.pem"),
    )
    return ctx


def invitation_allowed(invitation: Any, *, allowed_leader_aic: str, current_group: str | None) -> bool:
    """Fail closed until Leader's real AIC is supplied by the team."""
    if not allowed_leader_aic or current_group:
        return False
    leader = getattr(getattr(invitation, "group", None), "leader", None)
    if getattr(leader, "aic", None) != allowed_leader_aic:
        return False
    sender = getattr(invitation, "senderId", None)
    return sender == allowed_leader_aic


async def serve(*, partner_aic: str, leader_aic: str, credential_dir: Path) -> None:
    if not partner_aic.strip() or not leader_aic.strip():
        raise ValueError("REAL_PARTNER_AND_LEADER_AICS_REQUIRED")

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
    handler = LiteratureGroupHandler(client, leader_aic=leader_aic)
    client.set_command_handler(handler.on_command)

    async def policy(invitation: Any) -> bool:
        return invitation_allowed(
            invitation, allowed_leader_aic=leader_aic,
            current_group=client.group_id,
        )

    client.set_invitation_handler(policy)

    async def on_invitation(invitation: Any) -> None:
        joined = await client.join_group_from_invitation(invitation)
        print("GROUP_JOINED" if joined else "GROUP_INVITATION_REJECTED")
        if joined:
            print("Group:", client.group_id)

    try:
        await client.connect()
        print("AMQPS_CONNECTED")
        await client.start_inbox_consuming(on_invitation)
        print("INBOX_LISTENING")
        print("Waiting for permitted Leader invitation. Press Ctrl+C to stop.")
        await asyncio.Event().wait()
    finally:
        await client.close()
