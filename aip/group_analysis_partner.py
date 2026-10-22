"""Shared registered analysis Partner; leaving a Group never stops the app."""
import asyncio
from pathlib import Path

from acps_sdk.aip import GroupMgmtCommandType
from aip.audited_group_client import AuditedGroupPartnerMqClient
from aip.group_literature_partner import invitation_allowed, tls_context


async def serve_analysis(*, partner_aic, leader_aic, credential_dir: Path,
                         task_store, executor, handler_type, audit):
    if not partner_aic.strip() or not leader_aic.strip():
        raise ValueError("REAL_PARTNER_AND_LEADER_AICS_REQUIRED")
    if task_store is None or audit is None:
        raise ValueError("DURABLE_TASK_STORE_AND_AUDIT_REQUIRED")
    client = AuditedGroupPartnerMqClient(
        partner_aic=partner_aic, rabbitmq_host="wt.ioa.pub", rabbitmq_port=5671,
        rabbitmq_vhost="acps", rabbitmq_user=None, rabbitmq_password=None,
        ssl_context=tls_context(credential_dir), identity_binding_enabled=True,
        robust_connection=True)
    client.audit = audit
    handler = handler_type(client, leader_aic=leader_aic, executor=executor,
                           task_store=task_store, audit=audit)
    client.set_command_handler(handler.on_command)

    async def on_mgmt(command):
        if (command.command == GroupMgmtCommandType.DISBAND
                and command.groupId == client.group_id and command.senderRole == "leader"
                and command.senderId == leader_aic and command.must_respond(partner_aic)):
            audit.emit("DISBAND_RECEIVED", groupId=command.groupId, agentAic=partner_aic,
                       managementSessionId=command.sessionId, pendingLeave=client._pending_leave)
            print("DISBAND_RECEIVED", command.groupId, flush=True)

    async def on_disconnect(current, group_id):
        audit.emit("GROUP_LEFT", groupId=group_id, agentAic=partner_aic,
                   **current.resource_snapshot())
        print("GROUP_LEFT", group_id, flush=True)
        print("INBOX_RESTART_REQUIRED", flush=True)

    async def policy(invitation):
        return invitation_allowed(invitation, allowed_leader_aic=leader_aic,
                                  current_group=client.group_id)

    async def on_invitation(invitation):
        joined = await client.join_group_from_invitation(invitation)
        event = "GROUP_JOINED" if joined else "GROUP_INVITATION_REJECTED"
        audit.emit(event, groupId=client.group_id, agentAic=partner_aic)
        print(event, client.group_id, flush=True)

    client.set_mgmt_command_handler(on_mgmt)
    client.set_disconnect_handler(on_disconnect)
    client.set_invitation_handler(policy)
    try:
        await client.connect()
        print("AMQPS_CONNECTED", flush=True)
        await client.start_inbox_consuming(on_invitation)
        print("INBOX_LISTENING", flush=True)
        await asyncio.Event().wait()
    except BaseException as error:
        audit.emit("PARTNER_EXIT", exceptionCategory="cancelled" if isinstance(error, asyncio.CancelledError) else "partner_failed")
        raise
    finally:
        await client.close()
