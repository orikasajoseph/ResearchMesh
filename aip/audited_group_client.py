"""Audit SDK lifecycle and provide a fail-closed two-phase DISBAND candidate."""
import asyncio
from contextvars import ContextVar
import hashlib
from importlib import import_module
import json
import logging
from datetime import datetime, timezone
from time import monotonic

import aio_pika
from aiormq.abc import DeliveredMessage
from pamqp.commands import Basic
from acps_sdk.aip import (
    GroupMgmtCommand, GroupMgmtCommandType, GroupMgmtResult, GroupMemberStatus,
    GroupPartnerMqClient, StructuredDataItem, TaskResult,
)
from aio_pika.exceptions import (
    AMQPConnectionError,
    ChannelClosed,
    ChannelInvalidStateError,
    ConnectionClosed,
    PublishError,
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
        self._disband_context = None
        self._disband_received_at = None
        self._leave_receipt_published = False
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

    async def _handle_mgmt_command(self, command):
        if (isinstance(command, GroupMgmtCommand)
                and command.command == GroupMgmtCommandType.DISBAND):
            leader = getattr(getattr(self, "_group_info", None), "leader", None)
            valid = bool(
                command.groupId == self.group_id
                and command.senderRole == "leader"
                and getattr(leader, "aic", None) == command.senderId
                and command.must_respond(self.partner_aic)
                and command.sessionId
                and command.id
            )
            if not valid:
                if self.audit:
                    self.audit.emit("DISBAND_REJECTED", **self._identity(),
                                    reason="identity_or_correlation_invalid")
                return
            context = (command.groupId, command.sessionId, command.id, command.senderId)
            if self._disband_context is not None and self._disband_context != context:
                if self.audit:
                    self.audit.emit("DISBAND_REJECTED", **self._identity(),
                                    reason="conflicting_disband")
                return
            self._disband_context = context
            if self._disband_received_at is None:
                self._disband_received_at = monotonic()
        return await super()._handle_mgmt_command(command)

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
        if self._disband_context is not None:
            return await self._cleanup_disband_with_receipt()
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

    def _receipt_id(self):
        group_id, session_id, command_id, _ = self._disband_context
        digest = hashlib.sha256(
            f"{group_id}\0{session_id}\0{command_id}\0{self.partner_aic}".encode()
        ).hexdigest()[:32]
        return "leave-receipt-" + digest

    async def _publish_leave_receipt(self, *, timeout=5, attempts=3):
        group_id, session_id, command_id, leader_aic = self._disband_context
        receipt_id = self._receipt_id()
        receipt = GroupMgmtResult(
            id=receipt_id,
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="partner",
            senderId=self.partner_aic,
            mentions=[leader_aic],
            groupId=group_id,
            sessionId=session_id,
            status=GroupMemberStatus(connected=False, muted=self._muted),
            dataItems=[StructuredDataItem(data={
                "event": "GROUP_LEAVE_COMPLETED",
                "disbandCommandId": command_id,
                "consumerStopped": True,
                "partnerQueueDeleted": True,
                "bindingRemovedByQueueDelete": True,
                "leaderReceiptConfirmed": False,
            })],
        )
        exchange = self._exchange
        channel = getattr(exchange, "channel", None) if exchange is not None else None
        if exchange is None or channel is None or getattr(channel, "is_closed", False):
            raise RuntimeError("LEAVE_RECEIPT_PUBLISH_PATH_UNAVAILABLE")
        if getattr(channel, "publisher_confirms", None) is not True:
            raise RuntimeError("LEAVE_RECEIPT_PUBLISHER_CONFIRMS_REQUIRED")
        message = aio_pika.Message(
            body=json.dumps(receipt.model_dump(exclude_none=True), ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=receipt_id,
            user_id=self.partner_aic,
        )
        last_error = None
        uncertain = False
        for attempt in range(1, attempts + 1):
            attempt_started = monotonic()
            outcome = "exception"
            try:
                result = await asyncio.wait_for(exchange.publish(
                    message, routing_key="", mandatory=True, timeout=timeout), timeout)
                if isinstance(result, DeliveredMessage):
                    outcome = "unroutable"
                    raise RuntimeError("LEAVE_RECEIPT_UNROUTABLE")
                if not isinstance(result, Basic.Ack):
                    outcome = "uncertain"
                    uncertain = True
                    raise RuntimeError("LEAVE_RECEIPT_CONFIRM_UNCERTAIN")
                outcome = "ack"
                self._leave_receipt_published = True
                if self.audit:
                    self.audit.emit("LEAVE_RECEIPT_ATTEMPT", **self._identity(),
                                    receiptId=receipt_id, attempt=attempt, outcome=outcome,
                                    elapsedMs=round((monotonic() - attempt_started) * 1000))
                    self.audit.emit("LEAVE_RECEIPT_PUBLISHED", **self._identity(),
                                    managementSessionId=session_id,
                                    disbandCommandId=command_id, receiptId=receipt_id,
                                    brokerConfirmed=True, routable=True,
                                    leaderReceiptConfirmed=False, attempt=attempt)
                return
            except PublishError as error:
                outcome = "unroutable"
                last_error = error
            except RuntimeError as error:
                last_error = error
                if str(error) == "LEAVE_RECEIPT_UNROUTABLE":
                    outcome = "unroutable"
                elif str(error) == "LEAVE_RECEIPT_CONFIRM_UNCERTAIN":
                    outcome = "uncertain"
            except (TimeoutError, asyncio.TimeoutError) as error:
                outcome = "timeout"
                uncertain = True
                last_error = error
            except Exception as error:
                outcome = "exception"
                last_error = error
                uncertain = uncertain or isinstance(error, (ConnectionError, OSError))
            if self.audit:
                self.audit.emit("LEAVE_RECEIPT_ATTEMPT", **self._identity(),
                                receiptId=receipt_id, attempt=attempt, outcome=outcome,
                                elapsedMs=round((monotonic() - attempt_started) * 1000))
            if outcome == "unroutable":
                break
            if self.audit:
                self.audit.emit("LEAVE_RECEIPT_RETRY", **self._identity(),
                                receiptId=receipt_id, attempt=attempt)
        if self.audit:
            self.audit.emit("LEAVE_RECEIPT_FAILED", **self._identity(),
                            receiptId=receipt_id,
                            result="uncertain" if uncertain else "failed")
        raise RuntimeError("LEAVE_RECEIPT_NOT_CONFIRMED") from last_error

    async def _cleanup_disband_with_receipt(self):
        current = asyncio.current_task()
        if self._audited_cleanup_task is not None and self._audited_cleanup_task is not current:
            return await asyncio.shield(self._audited_cleanup_task)
        self._audited_cleanup_task = current
        group_id = self.group_id
        channel, connection = self._channel, self._connection
        try:
            if self.audit:
                self.audit.emit("CLEANUP_ENTERED", groupId=group_id,
                                agentAic=self.partner_aic)
            elapsed = monotonic() - self._disband_received_at
            remaining = 18 - elapsed
            if remaining <= 0:
                raise TimeoutError("PARTNER_LEAVE_RECEIPT_BUDGET_EXHAUSTED")
            async with asyncio.timeout(remaining):
                await self._cancel_command_lifecycles()
                if not self._queue or not self._consumer_tag:
                    raise RuntimeError("GROUP_CONSUMER_STATE_INCOMPLETE")
                if self.audit:
                    self.audit.emit("CONSUMER_CANCEL_ATTEMPT", **self._identity())
                await self._queue.cancel(self._consumer_tag)
                self._consumer_tag = None
                if self.audit:
                    self.audit.emit("CONSUMER_CANCEL_CONFIRMED", **self._identity())
                await self._queue.delete(if_unused=False, if_empty=False)
                if self.audit:
                    self.audit.emit("PARTNER_QUEUE_DELETE_CONFIRMED", **self._identity())
                self._queue = None
                self._queue_name = None
                await self._publish_leave_receipt()
            if channel and not getattr(channel, "is_closed", False):
                await channel.close()
            if (self._connection_owner and connection
                    and not getattr(connection, "is_closed", False)):
                await connection.close()
            disconnect_handler = self._disconnect_handler
            self._reset_group_state()
            self._audited_cleanup_succeeded = True
            if disconnect_handler and group_id:
                result = disconnect_handler(self, group_id)
                if hasattr(result, "__await__"):
                    await result
            if self.audit:
                self.audit.emit("CLEANUP_SUCCEEDED", groupId=group_id,
                                agentAic=self.partner_aic,
                                receiptId=self._receipt_id(),
                                leaderReceiptConfirmed=False, failures=[],
                                residual=[], groupResourcesCleared=True)
        except BaseException as error:
            self._audited_cleanup_succeeded = False
            if self.audit:
                self.audit.emit("CLEANUP_FAILED", groupId=group_id,
                                agentAic=self.partner_aic,
                                exceptionCategory="cancelled" if isinstance(error, asyncio.CancelledError)
                                else "two_phase_leave_failed",
                                receiptPublished=self._leave_receipt_published)
            raise
        finally:
            self._audited_cleanup_task = None

    async def close(self):
        # A failed two-phase leave must not re-enter leave_group from SDK close;
        # receipt retries are bounded inside _publish_leave_receipt. Close only
        # transports here and let the Leader time out fail-closed.
        if (self._disband_context is not None and self.leave_finished.is_set()
                and not self.leave_succeeded):
            for channel in (self._channel, self._inbox_channel):
                if channel is not None and not getattr(channel, "is_closed", False):
                    await channel.close()
            if (self._connection_owner and self._connection is not None
                    and not getattr(self._connection, "is_closed", False)):
                await self._connection.close()
            if self.audit:
                self.audit.emit("FAILED_LEAVE_TRANSPORT_CLOSED", **self._identity(),
                                receiptPublished=self._leave_receipt_published)
            return
        return await super().close()
