"""Observe SDK lifecycle by subclassing; never alter installed SDK or leave flow."""
import asyncio
from contextvars import ContextVar
from importlib import import_module
import logging

from acps_sdk.aip import GroupPartnerMqClient, TaskResult
from aio_pika.exceptions import (
    AMQPConnectionError,
    ChannelClosed,
    ChannelInvalidStateError,
    ConnectionClosed,
)

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
        self._audited_cleanup_task = None
        self._audited_leave_scheduled = False
        self._audited_leave_task = None
        # Group task commands can run for minutes.  aio-pika cancels an active
        # consumer callback when its transport drops, but that transient
        # callback cancellation must not kill business work already protected
        # by the durable task claim.  Keep strong references until completion;
        # explicit Group cleanup cancels and drains them below.
        self._command_lifecycles = set()
        self._command_shutdown = asyncio.Event()

    async def _handle_incoming_message(self, body):
        if body.get("type") == "task-command":
            lifecycle = asyncio.create_task(
                super()._handle_incoming_message(body),
                name="audited-group-task-command",
            )
            self._command_lifecycles.add(lifecycle)
            lifecycle.add_done_callback(self._command_lifecycle_finished)
            try:
                # Cancellation of the AMQP delivery callback leaves the
                # lifecycle running.  The unacked message may be redelivered;
                # DurableTaskStore prevents a second business execution.
                return await asyncio.shield(lifecycle)
            except asyncio.CancelledError:
                if self.audit:
                    self.audit.emit("COMMAND_DELIVERY_DETACHED", **self._identity())
                raise
            except BaseException:
                if self.audit:
                    self.audit.emit("SDK_MESSAGE_FAILED", **self._identity())
                raise
        try:
            return await super()._handle_incoming_message(body)
        except BaseException:
            if self.audit:
                self.audit.emit("SDK_MESSAGE_FAILED", **self._identity())
            raise

    def _command_lifecycle_finished(self, lifecycle):
        self._command_lifecycles.discard(lifecycle)
        if lifecycle.cancelled():
            return
        try:
            error = lifecycle.exception()
        except (asyncio.CancelledError, Exception):
            error = None
        if error is not None and self.audit:
            self.audit.emit("COMMAND_LIFECYCLE_FAILED", **self._identity())

    async def _cancel_command_lifecycles(self):
        self._command_shutdown.set()
        current = asyncio.current_task()
        active = [task for task in self._command_lifecycles
                  if task is not current and not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def _publish_message(self, message):
        if not isinstance(message, TaskResult):
            return await super()._publish_message(message)
        attempt = 0
        while True:
            if self._command_shutdown.is_set():
                raise asyncio.CancelledError()
            try:
                # Retrying reuses the same TaskResult object and message id.
                # The protocol remains at-least-once, while business execution
                # remains exactly-once under the durable task claim.
                return await super()._publish_message(message)
            except (AMQPConnectionError, ChannelClosed, ChannelInvalidStateError,
                    ConnectionClosed, ConnectionError, OSError):
                attempt += 1
                if self.audit:
                    self.audit.emit("RESULT_DELIVERY_DEFERRED", **self._identity(),
                                    deliveryAttempt=attempt)
                delay = min(0.25 * (2 ** min(attempt - 1, 3)), 2.0)
                try:
                    await asyncio.wait_for(self._command_shutdown.wait(), timeout=delay)
                except TimeoutError:
                    continue
                raise asyncio.CancelledError()

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
        # The SDK invokes this method before its AMQP consumer callback has
        # returned.  Cancelling that consumer from inside the same callback can
        # cancel the leave coroutine itself.  Queue the complete leave path for
        # the next loop turn so ACK and callback teardown finish first.
        if self._audited_leave_scheduled:
            return
        self._audited_leave_scheduled = True
        asyncio.get_running_loop().call_soon(self._start_audited_leave)

    def _start_audited_leave(self):
        if self._audited_leave_task is None:
            self._audited_leave_task = asyncio.create_task(
                self._run_audited_leave_request(),
                name="audited-group-leave",
            )

    async def _run_audited_leave_request(self):
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
        current = asyncio.current_task()
        active_cleanup = self._audited_cleanup_task
        if active_cleanup is not None and active_cleanup is not current:
            # DISBAND can race with application shutdown.  The SDK cleanup is
            # not re-entrant: a second cancel/delete/close sequence can cancel
            # the first one after it has already started.  A concurrent caller
            # therefore observes the in-flight result instead of entering the
            # SDK a second time.
            return await asyncio.shield(active_cleanup)
        await self._cancel_command_lifecycles()
        if not self.audit or not self.group_id:
            return await super()._cleanup_group_resources()
        self._audited_cleanup_task = current
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
            if self._audited_cleanup_task is current:
                self._audited_cleanup_task = None
