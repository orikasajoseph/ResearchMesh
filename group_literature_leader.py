"""One Literature task over the official platform Group SDK; no automatic retry."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import math
from time import monotonic
from uuid import uuid4

import aio_pika
from aio_pika.connection import make_url
from acps_sdk.aip import (
    ACSObject, GroupLeaderMqClient, GroupMgmtCommand, GroupMgmtCommandType,
    StructuredDataItem, TaskCommand, TaskCommandType, TaskResult, TaskState,
)
from acps_sdk.aip.aip_group_auth import GroupAclClient, GroupAclError
from acps_sdk.aip.aip_group_runtime import (
    build_external_connection_url, build_group_exchange_name, build_group_queue_name,
    build_inbox_queue_name,
)

from aip_business_partner import validate_local_task
from business_execution import SAFE_BACKEND_CODES, validate_business_result
from group_invitation import AUTH_SERVICE_URL, JoinConfirmation, delete_resource, validate
from platform_mq_check import HOST, PORT, VHOST, CheckError, certificate_context, classify_error


def validate_literature_task(task):
    payload = validate_local_task(deepcopy(task))
    if payload["taskType"] != "literature_search":
        raise CheckError("ONLY_LITERATURE_TASK_SUPPORTED")
    if payload["constraints"].get("maxResults") != 1:
        raise CheckError("FIRST_PLATFORM_CHECK_REQUIRES_MAX_RESULTS_ONE")
    return payload


def validate_reader_task(task):
    """Validate the first formal Reader check before any platform I/O."""
    payload = validate_local_task(deepcopy(task))
    if payload["taskType"] != "paper_read":
        raise CheckError("ONLY_READER_TASK_SUPPORTED")
    if payload["constraints"].get("maxPapers") != 1 or len(payload["input"].get("papers", [])) != 1:
        raise CheckError("FIRST_PLATFORM_CHECK_REQUIRES_ONE_PAPER")
    paper = payload["input"]["papers"][0]
    if not paper.get("pdfUrl"):
        raise CheckError("READER_REQUIRES_ACCESSIBLE_PDF_URL")
    return payload


async def settle_group_shutdown():
    # Give the Partner time to consume DISBAND before its temporary queue is removed.
    await asyncio.sleep(2)


def partner_failure_diagnostic(message):
    """Retain only recognized codes, never arbitrary remote text or credentials."""
    codes = set()
    items = list(message.status.dataItems or []) + list(message.dataItems or [])
    for item in items:
        if isinstance(item, StructuredDataItem) and isinstance(item.data, dict):
            candidates = [item.data.get("code"), item.data.get("errorCode")]
            error = item.data.get("error")
            candidates.append(error.get("code") if isinstance(error, dict) else error)
        else:
            candidates = [getattr(item, "text", None)]
        for value in candidates:
            if isinstance(value, str) and value in SAFE_BACKEND_CODES:
                codes.add(value)
    return {"codes": sorted(codes), "detailsOmitted": True,
            "requiresPartnerLog": not bool(codes)}


class SingleTaskMqClient(GroupLeaderMqClient):
    """Only one joined Partner may receive one Start and one Complete."""

    def bind_task(self, *, task_id, session_id, partner_aic, payload):
        self.task_binding = (task_id, session_id, partner_aic, deepcopy(payload))
        self.task_commands_attempted = set()
        self.task_sending_enabled = False

    async def publish_message(self, message):
        if isinstance(message, GroupMgmtCommand) and message.command == GroupMgmtCommandType.DISBAND:
            return await super().publish_message(message)
        binding = getattr(self, "task_binding", None)
        if not binding or not self.task_sending_enabled or not isinstance(message, TaskCommand):
            raise CheckError("TASK_SEND_NOT_ARMED")
        task_id, session_id, partner, payload = binding
        if (message.senderId != self.leader_aic or message.senderRole != "leader"
                or message.groupId != self.group_id or message.taskId != task_id
                or message.sessionId != session_id or message.mentions != [partner]
                or message.command not in (TaskCommandType.Start, TaskCommandType.Complete)):
            raise CheckError("TASK_SEND_SCOPE_MISMATCH")
        if message.command in self.task_commands_attempted:
            raise CheckError("TASK_COMMAND_RETRY_DISABLED")
        if message.command == TaskCommandType.Start:
            items = message.dataItems or []
            if len(items) != 1 or not isinstance(items[0], StructuredDataItem) or items[0].data != payload:
                raise CheckError("EXACT_STRUCTURED_TASK_REQUIRED")
        elif message.dataItems or TaskCommandType.Start not in self.task_commands_attempted:
            raise CheckError("COMPLETE_BEFORE_START_OR_WITH_DATA")
        # Count attempts before publish: a failed publish may still have delivered.
        self.task_commands_attempted.add(message.command)
        return await super().publish_message(message)


class LiteratureConversation:
    """SDK validates AMQP user_id first; correlate all task identifiers here."""

    invalid_result_code = "INVALID_LITERATURE_RESULT"

    def __init__(self, config, group_id, task_id, session_id):
        self.join = JoinConfirmation(config.partner_aic, group_id)
        self.task_id, self.session_id = task_id, session_id
        self.messages = asyncio.Queue(maxsize=128)
        self.overflow = False
        self.ignored = 0

    async def on_message(self, message):
        if not isinstance(message, TaskResult):
            await self.join.on_message(message)
            return
        if (message.senderRole != "partner" or message.senderId != self.join.partner_aic
                or message.groupId != self.join.group_id or message.taskId != self.task_id
                or message.sessionId != self.session_id):
            self.ignored += 1
            return
        try:
            self.messages.put_nowait(message)
        except asyncio.QueueFull:
            self.overflow = True

    @classmethod
    def extract(cls, message, task):
        items = [item for product in message.products or [] for item in product.dataItems]
        if len(items) != 1 or not isinstance(items[0], StructuredDataItem):
            raise CheckError("EXPECTED_ONE_BUSINESS_RESULT")
        result = deepcopy(items[0].data)
        try:
            validate_business_result(task, result)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CheckError(cls.invalid_result_code) from None
        if any(w.startswith(("TRANSPORT_TEST_ONLY", "SIMULATED_RESEARCH")) for w in result["warnings"]):
            raise CheckError("SIMULATED_RESULT_NOT_ACCEPTED")
        return result

    async def run(self, client, task, record, event, timeout):
        client.task_sending_enabled = True
        record["startAttempted"] = True
        event("TASK_START_ATTEMPT")
        # One total deadline covers Start publish, all messages and Complete publish.
        async with asyncio.timeout(timeout):
            await client.send_task_command(
                TaskCommandType.Start, self.task_id, self.session_id,
                data_items=[StructuredDataItem(data=deepcopy(task))], mentions=[self.join.partner_aic])
            record["taskCommandsSent"] += 1
            record["startPublished"] = True
            event("TASK_START_PUBLISHED")
            seen_ids = set()
            while True:
                if self.overflow:
                    raise CheckError("TASK_RESULT_QUEUE_OVERFLOW")
                message = await self.messages.get()
                if message.id in seen_ids:
                    continue
                seen_ids.add(message.id)
                state = message.status.state
                record["taskStates"].append({"messageId": message.id, "state": state.value,
                                            "timeUtc": datetime.now(timezone.utc).isoformat()})
                event("TASK_STATE_" + state.value.upper().replace("-", "_"))
                if state in (TaskState.Failed, TaskState.Rejected, TaskState.Canceled, TaskState.AwaitingInput):
                    record["partnerTerminalState"] = state.value
                    record["partnerFailure"] = partner_failure_diagnostic(message)
                    event("PARTNER_FAILURE_RECORDED")
                    raise CheckError("PARTNER_" + state.value.upper().replace("-", "_"))
                if state == TaskState.AwaitingCompletion:
                    result = self.extract(message, task)
                    if record["result"] is not None and result != record["result"]:
                        raise CheckError("PRODUCT_CHANGED_AFTER_CONFIRMATION")
                    if record["completeAttempted"]:
                        continue
                    record["result"] = result
                    record["resultValidated"] = True
                    # Save the validated product before Complete, including empty results.
                    event("BUSINESS_RESULT_VALIDATED")
                    record["completeAttempted"] = True
                    event("TASK_COMPLETE_ATTEMPT")
                    await client.send_task_command(
                        TaskCommandType.Complete, self.task_id, self.session_id,
                        mentions=[self.join.partner_aic])
                    record["taskCommandsSent"] += 1
                    record["completePublished"] = True
                    event("TASK_COMPLETE_PUBLISHED")
                elif state == TaskState.Completed:
                    if not record["completePublished"] or not record["resultValidated"]:
                        raise CheckError("COMPLETION_HANDSHAKE_MISSING")
                    if message.products and self.extract(message, task) != record["result"]:
                        raise CheckError("PRODUCT_CHANGED_AFTER_CONFIRMATION")
                    record["protocolCompleted"] = True
                    record["businessReady"] = bool(record["result"]["items"])
                    event("TASK_COMPLETED_CONFIRMED")
                    return


class ReaderConversation(LiteratureConversation):
    """Reader uses the same official protocol flow with Reader-specific errors."""

    invalid_result_code = "INVALID_READER_RESULT"


async def _run_group_single_task(config, task, *, execute, partner_ready,
                                 partner_commit, task_timeout, checkpoint,
                                 task_release, task_validator, conversation_type,
                                 group_prefix, live_scope, offline_scope,
                                 connection_prefix, max_task_timeout):
    """Preflight by default. Live mode creates a fresh temporary group and task."""
    started = monotonic()
    group_id, task_id, session_id = (prefix + uuid4().hex for prefix in
                                    (group_prefix, "task-", "session-"))
    record = {
        "timeUtc": datetime.now(timezone.utc).isoformat(),
        "scope": live_scope if execute else offline_scope,
        "groupId": group_id, "taskId": task_id, "sessionId": session_id,
        "leaderAic": config.leader_aic, "partnerAic": config.partner_aic,
        "partnerSelection": "explicit_aic_not_adp", "partnerCommitDeclared": partner_commit,
        "partnerCommitRemotelyVerified": False, "providerExecutionVerified": False,
        "leaderLlmCalls": 0, "partnerLlmCalls": None,
        "offlineChecksPassed": False, "networkAttempted": False,
        "aclWriteAttempted": False, "leaderAuthorized": False, "partnerAuthorized": False,
        "amqpConnected": False, "groupCreationAttempted": False, "groupCreated": False,
        "invitationPublishAttempted": False, "invitationPublished": False, "partnerJoinConfirmed": False,
        "taskReleaseRequired": task_release is not None, "taskReleaseConfirmed": False,
        "startAttempted": False, "startPublished": False, "taskCommandsSent": 0,
        "completeAttempted": False, "completePublished": False,
        "resultValidated": False, "result": None, "taskStates": [],
        "protocolCompleted": False, "businessReady": False, "remoteStateUnknown": False,
        "cleanupAttempted": False, "cleanupSucceeded": None, "aclDeleted": None,
        "groupResourcesDeleted": None, "connectionClosed": None,
        "events": [], "error": None, "cleanupErrors": [], "passed": False,
    }
    acl = connection = client = conversation = None

    def event(name):
        record["events"].append({"event": name, "timeUtc": datetime.now(timezone.utc).isoformat()})
        if checkpoint:
            try:
                checkpoint(deepcopy(record))
            except Exception:
                record["journalError"] = "JOURNAL_WRITE_FAILED"
                if not record["cleanupAttempted"]:
                    raise CheckError("JOURNAL_WRITE_FAILED") from None

    async def bounded(awaitable):
        return await asyncio.wait_for(awaitable, config.timeout)

    async def cleanup(name, operation):
        try:
            await asyncio.wait_for(operation(), min(config.timeout, 5))
            return True
        except Exception as error:
            record["cleanupErrors"].append({"step": name, "type": type(error).__name__})
            return False

    try:
        payload = task_validator(task)
        record["task"] = payload
        if type(execute) is not bool or type(partner_ready) is not bool:
            raise CheckError("INVALID_EXECUTION_FLAGS")
        if task_release is not None and not callable(task_release):
            raise CheckError("INVALID_TASK_RELEASE")
        directory = validate(config, "invite" if execute else "offline", execute, partner_ready)
        if (type(task_timeout) not in (int, float) or not math.isfinite(task_timeout)
                or not 1 <= task_timeout <= max_task_timeout):
            raise CheckError("INVALID_TASK_TIMEOUT")
        if execute and (not isinstance(partner_commit, str) or not 7 <= len(partner_commit) <= 40
                        or any(c not in "0123456789abcdefABCDEF" for c in partner_commit)):
            raise CheckError("CONFIRMED_PARTNER_COMMIT_REQUIRED")
        ctx = certificate_context(directory, config.leader_aic)
        record["offlineChecksPassed"] = True
        event("OFFLINE_PREFLIGHT_PASSED")
        if execute:
            acl = GroupAclClient(base_url=AUTH_SERVICE_URL, ssl_context=ctx, timeout=config.timeout)
            record["networkAttempted"] = record["aclWriteAttempted"] = True
            event("LEADER_ACL_WRITE_ATTEMPT")
            await bounded(acl.add_member(leader_aic=config.leader_aic, group_id=group_id, member_aic=config.leader_aic))
            record["leaderAuthorized"] = True
            event("LEADER_ACL_AUTHORIZED")
            url = build_external_connection_url(host=HOST, port=PORT, vhost=VHOST,
                                               connection_name=connection_prefix + config.leader_aic)
            connection = aio_pika.Connection(make_url(url), ssl_context=ctx)
            await bounded(connection.connect(timeout=config.timeout))
            record["amqpConnected"] = not connection.is_closed
            if not record["amqpConnected"]:
                raise CheckError("AMQP_CONNECTION_NOT_OPEN")
            client = SingleTaskMqClient(
                leader_aic=config.leader_aic, rabbitmq_host=HOST, rabbitmq_port=PORT,
                rabbitmq_vhost=VHOST, rabbitmq_user=None, rabbitmq_password=None,
                ssl_context=ctx, connection=connection, connection_owner=False, identity_binding_enabled=True)
            client.bind_task(task_id=task_id, session_id=session_id, partner_aic=config.partner_aic, payload=payload)
            await bounded(client.connect())
            record["groupCreationAttempted"] = True
            event("GROUP_CREATE_ATTEMPT")
            if await bounded(client.create_group(group_id=group_id, partner_acs_list=[])) != group_id:
                raise CheckError("UNEXPECTED_GROUP_ID")
            record["groupCreated"] = True
            event("GROUP_CREATED")
            conversation = conversation_type(config, group_id, task_id, session_id)
            client.set_message_handler(conversation.on_message)
            await bounded(client.start_consuming())
            await bounded(acl.add_member(leader_aic=config.leader_aic, group_id=group_id, member_aic=config.partner_aic))
            record["partnerAuthorized"] = True
            event("PARTNER_ACL_AUTHORIZED")
            record["invitationPublishAttempted"] = True
            event("INVITATION_PUBLISH_ATTEMPT")
            await bounded(client.invite_via_inbox(ACSObject(aic=config.partner_aic),
                partner_inbox=build_inbox_queue_name(config.partner_aic),
                timeout_seconds=math.ceil(config.join_timeout)))
            record["invitationPublished"] = True
            event("INVITATION_PUBLISHED")
            await asyncio.wait_for(conversation.join.event.wait(), config.join_timeout)
            record["partnerJoinConfirmed"] = True
            event("GROUP_JOINED_CONFIRMED")
            if task_release is not None:
                event("TASK_RELEASE_WAITING")
                await task_release(deepcopy(record))
                record["taskReleaseConfirmed"] = True
                event("TASK_RELEASE_CONFIRMED")
            await conversation.run(client, payload, record, event, task_timeout)
    except asyncio.CancelledError:
        record["error"] = {"code": "INTERRUPTED", "type": "CancelledError"}
    except Exception as error:
        code = ("GROUP_ACL_REQUEST_FAILED" if isinstance(error, GroupAclError) else
                "TASK_TIMEOUT_REMOTE_STATE_UNKNOWN" if isinstance(error, TimeoutError) and record["startAttempted"] else
                "PARTNER_JOIN_TIMEOUT" if isinstance(error, TimeoutError) and record["invitationPublished"] else
                classify_error(error))
        record["error"] = {"code": code, "type": type(error).__name__}
    finally:
        record["remoteStateUnknown"] = bool(record["startAttempted"] and not record["protocolCompleted"]
            and record.get("partnerTerminalState") not in ("failed", "rejected", "canceled"))
        if execute and record["aclWriteAttempted"]:
            record["cleanupAttempted"] = True
            event("CLEANUP_STARTED")
            if client is not None:
                client.task_sending_enabled = False
            if record["groupCreated"]:
                # Use the public SDK call directly: its convenience shutdown swallows publish errors.
                await cleanup("disband", lambda: client.send_mgmt_command(GroupMgmtCommandType.DISBAND, mentions="all"))
                await cleanup("disband_settle", settle_group_shutdown)
                await cleanup("stop_consumer", client.cleanup_group_resources)
            if record["groupCreationAttempted"]:
                deleted = []
                if connection is not None and not connection.is_closed:
                    for kind, name in (
                        ("queue", build_group_queue_name(config.leader_aic, group_id, config.partner_aic)),
                        ("queue", build_group_queue_name(config.leader_aic, group_id, config.leader_aic)),
                        ("exchange", build_group_exchange_name(config.leader_aic, group_id))):
                        deleted.append(await cleanup("delete_" + kind,
                            lambda k=kind, n=name: delete_resource(connection, k, n)))
                record["groupResourcesDeleted"] = len(deleted) == 3 and all(deleted)
                if not record["groupResourcesDeleted"]:
                    record["cleanupErrors"].append({"step": "delete_group_resources", "type": "IncompleteCleanup"})
            if acl is not None:
                record["aclDeleted"] = await cleanup("delete_own_acl", lambda: acl.delete_group(
                    leader_aic=config.leader_aic, group_id=group_id))
            if connection is not None:
                await cleanup("close_connection", connection.close)
                record["connectionClosed"] = bool(connection.is_closed)
                if not connection.is_closed:
                    record["cleanupErrors"].append({"step": "close_connection", "type": "ConnectionRemainsOpen"})
            if acl is not None:
                await cleanup("close_acl_client", acl.aclose)
            record["cleanupSucceeded"] = not record["cleanupErrors"]
            event("CLEANUP_FINISHED")
        if conversation is not None:
            record["ignoredTaskResults"] = conversation.ignored
        record["elapsedMs"] = round((monotonic() - started) * 1000)
        record["passed"] = bool(record["error"] is None and record["offlineChecksPassed"]
            and "journalError" not in record and (not execute or
            (record["protocolCompleted"] and record["businessReady"] and record["cleanupSucceeded"])))
        if checkpoint:
            try:
                checkpoint(deepcopy(record))
            except Exception:
                record["journalError"] = "JOURNAL_WRITE_FAILED"
                record["passed"] = False
    return record


async def run_group_literature(config, task, *, execute=False, partner_ready=False,
                               partner_commit=None, task_timeout=180, checkpoint=None,
                               task_release=None):
    """Run one formal Literature task while preserving the sealed interface."""
    return await _run_group_single_task(
        config, task, execute=execute, partner_ready=partner_ready,
        partner_commit=partner_commit, task_timeout=task_timeout,
        checkpoint=checkpoint, task_release=task_release,
        task_validator=validate_literature_task,
        conversation_type=LiteratureConversation,
        group_prefix="rm-literature-",
        live_scope="PLATFORM_LITERATURE_SINGLE_TASK",
        offline_scope="OFFLINE_PLATFORM_LITERATURE_PREFLIGHT",
        connection_prefix="literature-check-",
        max_task_timeout=600,
    )


async def run_group_reader(config, task, *, execute=False, partner_ready=False,
                           partner_commit=None, task_timeout=750, checkpoint=None,
                           task_release=None):
    """Run one formal Paper Reader task; no Planner, search or Repository work."""
    return await _run_group_single_task(
        config, task, execute=execute, partner_ready=partner_ready,
        partner_commit=partner_commit, task_timeout=task_timeout,
        checkpoint=checkpoint, task_release=task_release,
        task_validator=validate_reader_task,
        conversation_type=ReaderConversation,
        group_prefix="rm-reader-",
        live_scope="PLATFORM_READER_SINGLE_TASK",
        offline_scope="OFFLINE_PLATFORM_READER_PREFLIGHT",
        connection_prefix="reader-check-",
        max_task_timeout=900,
    )
