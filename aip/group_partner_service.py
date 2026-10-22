"""Application-level Inbox recovery around an unmodified SDK Group client."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def serve_client_cycles(
    client_factory: Callable[[], Any],
    configure: Callable[[Any], None],
    *,
    audit,
    partner_aic: str,
) -> None:
    """Keep an application available after a complete, successful Group leave.

    The installed SDK closes Group resources (and an owned shared connection)
    during leave.  Reusing that client would retain stale SDK state.  This loop
    therefore waits for the audited leave path to finish, closes the old client,
    and creates a new SDK client with a new connection and Inbox consumer.
    Cleanup failure is fail-closed and never triggers a reconnect loop.
    """
    cycle = 0
    while True:
        client = client_factory()
        invitation_handler = configure(client)
        try:
            await client.connect()
            if cycle:
                if audit:
                    audit.emit("INBOX_RESTART_SUCCEEDED", agentAic=partner_aic, cycle=cycle)
                print("INBOX_RESTART_SUCCEEDED", cycle, flush=True)
            else:
                print("AMQPS_CONNECTED", flush=True)
            await client.start_inbox_consuming(invitation_handler)
            print("INBOX_LISTENING", flush=True)
            await client.leave_finished.wait()
            if not client.leave_succeeded:
                if audit:
                    audit.emit("INBOX_RESTART_BLOCKED", agentAic=partner_aic,
                               cycle=cycle, reason="leave_cleanup_failed")
                raise RuntimeError("GROUP_LEAVE_CLEANUP_FAILED")
        except asyncio.CancelledError:
            raise
        finally:
            await client.close()
        cycle += 1
        if audit:
            audit.emit("INBOX_RESTART_STARTED", agentAic=partner_aic, cycle=cycle)
        print("INBOX_RESTART_STARTED", cycle, flush=True)
