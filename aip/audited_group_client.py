"""Observe SDK lifecycle by subclassing; never alter installed SDK or leave flow."""
import asyncio
from contextvars import ContextVar
from importlib import import_module
import logging

from acps_sdk.aip import GroupPartnerMqClient

_CLEANUP = ContextVar("sdk_cleanup_audit", default=None)
SDK_LOGGER = import_module(GroupPartnerMqClient.__module__).logger


class _FailureCapture(logging.Handler):
    def emit(self, record):
        failures = _CLEANUP.get()
        if failures is None:
            return
        # Never format SDK records: arguments can contain sensitive exception text.
        for code in ("consumer_cancel_failed", "queue_delete_failed", "partner_leave_status_failed"):
            if str(record.msg).startswith("event=" + code + " "):
                if code not in failures:
                    failures.append(code)


class AuditedGroupPartnerMqClient(GroupPartnerMqClient):
    audit = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Application-owned signal.  The SDK still owns ACK, leave ordering and
        # resource cleanup; a service loop may only build a fresh client after
        # the complete leave path has returned.
        self.leave_finished = asyncio.Event()
        self.leave_succeeded = False
        self._audited_cleanup_succeeded = False

    async def _handle_incoming_message(self, body):
        try:
            return await super()._handle_incoming_message(body)
        except BaseException:
            if self.audit:
                self.audit.emit("SDK_MESSAGE_FAILED", **self._identity())
            raise

    def _identity(self):
        return dict(groupId=self.group_id, agentAic=self.partner_aic)

    def resource_snapshot(self, channel=None, connection=None):
        residual = [name for name in ("_group_id", "_group_info", "_exchange", "_queue",
                    "_queue_name", "_exchange_name", "_connection_name", "_vhost", "_node_name",
                    "_channel", "_connection", "_consumer_tag", "_pending_leave",
                    "_pending_leave_session_id", "_task_states") if getattr(self, name, None)]
        if channel is not None and not channel.is_closed:
            residual.append("channel_not_closed")
        if self._connection_owner and connection is not None and not connection.is_closed:
            residual.append("owned_connection_not_closed")
        if getattr(self._state, "value", None) != "disconnected":
            residual.append("state_not_disconnected")
        return dict(residual=residual, groupResourcesCleared=not residual)

    async def _handle_leave_request(self):
        if not self.audit:
            try:
                result = await super()._handle_leave_request()
                self.leave_succeeded = True
                return result
            finally:
                self.leave_finished.set()
        identity = self._identity()
        self.audit.emit("LEAVE_ENTERED", **identity,
                        managementSessionId=getattr(self, "_pending_leave_session_id", None))
        failures = []
        if SDK_LOGGER.disabled or not SDK_LOGGER.isEnabledFor(logging.WARNING) or SDK_LOGGER.filters:
            failures.append("sdk_warning_observation_unavailable")
        token = _CLEANUP.set(failures)
        capture = _FailureCapture()
        SDK_LOGGER.addHandler(capture)
        try:
            await super()._handle_leave_request()
        except BaseException:
            self.audit.emit("LEAVE_FAILED", **identity)
            raise
        finally:
            SDK_LOGGER.removeHandler(capture)
            _CLEANUP.reset(token)
            if failures:
                self.audit.emit("LEAVE_STATUS_FAILED", **identity)
            self.leave_succeeded = bool(not failures and self._audited_cleanup_succeeded)
            self.leave_finished.set()
        if self.leave_succeeded:
            self.audit.emit("LEAVE_FINISHED", **identity)

    async def _cleanup_group_resources(self):
        if not self.audit or not self.group_id:
            return await super()._cleanup_group_resources()
        identity = self._identity()
        channel, connection = self._channel, self._connection
        # aio-pika cancels the active consumer callback when that callback asks
        # the SDK to cancel its own consumer after DISBAND.  Run the SDK cleanup
        # in an independent task so that callback cancellation cannot interrupt
        # channel/connection closure, state reset, or GROUP_LEFT.  Cancellation
        # is absorbed only when a consumer existed and the complete SDK cleanup
        # task subsequently succeeded; every cleanup exception still propagates.
        had_consumer = bool(self._queue and self._consumer_tag)
        caller = asyncio.current_task()
        cancel_count = caller.cancelling() if caller else 0
        consumer_callback_cancelled = False
        self.audit.emit("CLEANUP_ENTERED", **identity)
        failures = []
        if SDK_LOGGER.disabled or not SDK_LOGGER.isEnabledFor(logging.WARNING) or SDK_LOGGER.filters:
            failures.append("sdk_warning_observation_unavailable")
        token = _CLEANUP.set(failures)
        capture = _FailureCapture()
        SDK_LOGGER.addHandler(capture)
        try:
            if not had_consumer:
                await super()._cleanup_group_resources()
            else:
                cleanup = asyncio.create_task(
                    super()._cleanup_group_resources(),
                    name="sdk-group-resource-cleanup",
                )
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    if not (caller and caller.cancelling() > cancel_count):
                        raise
                    caller.uncancel()
                    await asyncio.shield(cleanup)
                    consumer_callback_cancelled = True
        except BaseException as error:
            self.audit.emit("CLEANUP_FAILED", **identity,
                            exceptionCategory="cancelled" if isinstance(error, asyncio.CancelledError) else "sdk_cleanup_error",
                            failures=failures, **self.resource_snapshot(channel, connection))
            raise
        else:
            snapshot = self.resource_snapshot(channel, connection)
            self._audited_cleanup_succeeded = bool(
                not failures and snapshot["groupResourcesCleared"]
            )
            self.audit.emit("CLEANUP_SUCCEEDED" if self._audited_cleanup_succeeded
                            else "CLEANUP_FAILED", **identity, failures=failures,
                            consumerCallbackCancelled=consumer_callback_cancelled,
                            **snapshot)
        finally:
            SDK_LOGGER.removeHandler(capture)
            _CLEANUP.reset(token)
