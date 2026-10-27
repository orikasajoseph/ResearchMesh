"""Resource and unsent-resume regressions with real resolvers, mocked HTTP/PDF.

No real API, MQ, PDF, clone, embedding or model calls.
"""
import asyncio
import base64
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from arxiv_pdf import ArxivPdfResolver
from input_binding import bind_task_input
from platform_chain_resources import (
    prepare_reader_resources, validate_preparation, verify_cached_preparation,
)
from platform_three_agent_orchestrator import (
    execute_platform_chain, read_json, stage_directory, unstarted_preflight,
)
from tests.test_platform_three_agent_orchestrator import (
    FakeRunner, accepted_record, paper, plan, PDF, REPOSITORY,
)
from tests.test_arxiv_pdf import FakePdfReader, pdf_bytes


def search_result():
    value = accepted_record("literature_search", plan()["tasks"][0])["result"]
    value["items"][0]["data"].update(
        paperId="semantic-scholar-paper-id", url="https://www.semanticscholar.org/paper/id",
        pdfUrl=None, repositoryUrls=[], sourceUrls=["https://arxiv.org/abs/2106.09685"],
        abstract="Implementation at " + REPOSITORY)
    return value


def reader_task(search=None):
    original = bind_task_input(plan()["tasks"][1], {"S1": search or search_result()})
    return {key: value for key, value in original.items() if key not in ("dependsOn", "requiredSkill")}


def preflight_record():
    value = {key: False for key in (
        "networkAttempted", "aclWriteAttempted", "groupCreationAttempted", "groupCreated",
        "invitationPublishAttempted", "invitationPublished", "startAttempted", "startPublished",
        "partnerJoinConfirmed", "taskReleaseConfirmed", "protocolCompleted",
        "remoteStateUnknown", "offlineChecksPassed")}
    value.update(scope="PLATFORM_READER_SINGLE_TASK", task=None, taskCommandsSent=0, result=None,
                 error={"code": "READER_REQUIRES_ACCESSIBLE_PDF_URL"},
                 groupId="old-unsent-group", taskId="old-unsent-task", sessionId="old-unsent-session")
    return value


def invitation_only_record():
    value = {key: True for key in (
        "offlineChecksPassed", "networkAttempted", "aclWriteAttempted",
        "groupCreationAttempted", "groupCreated", "invitationPublishAttempted",
        "invitationPublished", "partnerJoinConfirmed", "cleanupAttempted",
        "aclDeleted", "connectionClosed")}
    value.update(
        scope="PLATFORM_READER_SINGLE_TASK", taskCommandsSent=0, result=None,
        startAttempted=False, startPublished=False, protocolCompleted=False,
        remoteStateUnknown=False, taskReleaseConfirmed=False,
        completeAttempted=False, completePublished=False, resultValidated=False,
        providerExecutionVerified=False, cleanupSucceeded=True, groupResourcesDeleted=True,
        cleanupErrors=[],
        error={"code": "TASK_RELEASE_TIMEOUT"},
        groupId="old-invitation-group", taskId="old-invitation-task",
        sessionId="old-invitation-session")
    return value


def join_timeout_record():
    value = invitation_only_record()
    value.update(partnerJoinConfirmed=False, cleanupSucceeded=True,
                 groupResourcesDeleted=True, cleanupErrors=[],
                 error={"code": "PARTNER_JOIN_TIMEOUT"})
    return value


async def prepared(task, search, directory, *, pdf_error=False, repo_error=False):
    title = task["input"]["papers"][0]["title"]

    def pdf_handler(request):
        if pdf_error:
            return httpx.Response(404)
        if request.url.path.startswith("/abs/"):
            return httpx.Response(200, content=(f'<meta name="citation_title" content="{title}">'
                f'<meta name="citation_pdf_url" content="{PDF}">').encode())
        return httpx.Response(200, content=pdf_bytes(title))

    def repository_handler(request):
        if repo_error:
            return httpx.Response(429)
        if request.url.host == "arxiv.org":
            return httpx.Response(200, content=REPOSITORY.encode())
        text = title + "\nPaper: arxiv:2106.09685"
        return httpx.Response(200, json={"encoding": "base64",
            "content": base64.b64encode(text.encode()).decode()})

    with patch.dict("sys.modules", {"pypdf": SimpleNamespace(PdfReader=FakePdfReader)}):
        async with httpx.AsyncClient(transport=httpx.MockTransport(pdf_handler)) as pdf_client:
            with httpx.Client(transport=httpx.MockTransport(repository_handler)) as repo_client:
                return await prepare_reader_resources(task, search, cache_dir=directory,
                    pdf_resolver=ArxivPdfResolver(directory, client=pdf_client, request_interval=0),
                    repository_client=repo_client)


class IncompleteRunner(FakeRunner):
    def __init__(self, fail_reader_preflight=False):
        super().__init__()
        self.fail_reader_preflight = fail_reader_preflight

    async def __call__(self, stage, task):
        self.calls.append(stage)
        self.payloads.append(deepcopy(task))
        if stage == "paper_read" and self.fail_reader_preflight:
            return preflight_record()
        record = accepted_record(stage, task)
        if stage == "literature_search":
            record["result"] = search_result()
        elif stage == "paper_read":
            record["result"]["items"][0]["data"]["evidence"][0]["sourceId"] = (
                task["input"]["papers"][0]["paperId"])
        return record


class InvitationOnlyRunner(IncompleteRunner):
    def __init__(self):
        super().__init__()
        self.abandon_reader = True

    async def __call__(self, stage, task):
        if stage == "paper_read" and self.abandon_reader:
            self.calls.append(stage)
            self.payloads.append(deepcopy(task))
            return invitation_only_record()
        return await super().__call__(stage, task)


class TerminalFailedRunner(IncompleteRunner):
    def __init__(self):
        super().__init__()
        self.fail_reader = True

    async def __call__(self, stage, task):
        if stage == "paper_read" and self.fail_reader:
            self.calls.append(stage)
            self.payloads.append(deepcopy(task))
            return {
                "task": deepcopy(task), "result": None, "passed": False,
                "protocolCompleted": False, "resultValidated": False,
                "remoteStateUnknown": False, "partnerTerminalState": "failed",
                "startAttempted": True, "startPublished": True, "taskCommandsSent": 1,
                "cleanupAttempted": True, "cleanupSucceeded": True, "aclDeleted": True,
                "groupResourcesDeleted": True, "connectionClosed": True,
                "cleanupErrors": [], "error": {"code": "PARTNER_FAILED"},
                "groupId": "failed-reader-group", "sessionId": "failed-reader-session",
                "taskId": "failed-reader-task", "partnerAic": "reader-aic",
            }
        return await super().__call__(stage, task)


class PlatformChainResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_resolvers_fill_missing_resources_without_mutating_s1_or_task(self):
        search = search_result()
        task = reader_task(search)
        originals = deepcopy((task, search))
        with TemporaryDirectory() as directory:
            bundle = await prepared(task, search, Path(directory))
        resolved = validate_preparation(bundle, task, search)
        self.assertEqual(resolved["input"]["papers"][0]["pdfUrl"], PDF)
        self.assertEqual(resolved["input"]["papers"][0]["repositoryUrls"], [REPOSITORY])
        self.assertEqual(resolved["input"]["papers"][0]["paperId"], "semantic-scholar-paper-id")
        self.assertTrue(bundle["repositoryProof"]["verifiedRelation"])
        self.assertEqual((task, search), originals)

    async def test_missing_pdf_or_repository_fails_closed(self):
        for failure in ("pdf", "repository"):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "VERIFIED_(PDF_URL|REPOSITORY_URL)_UNAVAILABLE"):
                    await prepared(reader_task(), search_result(), Path(directory),
                                   pdf_error=failure == "pdf", repo_error=failure == "repository")

    async def test_changed_identity_pdf_proof_or_repository_proof_is_rejected(self):
        task, search = reader_task(), search_result()
        with TemporaryDirectory() as directory:
            bundle = await prepared(task, search, Path(directory))
        for change in ("identity", "pdf", "repository", "source"):
            value = deepcopy(bundle)
            if change == "identity": value["task"]["input"]["papers"][0]["paperId"] = "wrong"
            elif change == "pdf": value["pdfProofs"][0]["state"] = "unverified"
            elif change == "repository": value["repositoryProof"]["proof"]["identifierMatched"] = False
            else: value["searchResultSha256"] = "0" * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_preparation(value, task, search)

    async def test_cached_preparation_rechecks_pdf_before_reuse(self):
        task, search = reader_task(), search_result()
        with TemporaryDirectory() as directory, patch.dict(
                "sys.modules", {"pypdf": SimpleNamespace(PdfReader=FakePdfReader)}):
            bundle = await prepared(task, search, Path(directory))
            self.assertEqual(verify_cached_preparation(bundle, task, search, cache_dir=directory),
                             bundle["task"])
            Path(bundle["pdfProofs"][0]["cachePath"]).write_bytes(b"tampered cached PDF")
            with self.assertRaisesRegex(ValueError, "VERIFIED_PDF_CACHE_CHANGED"):
                verify_cached_preparation(bundle, task, search, cache_dir=directory)

    async def test_full_chain_receives_enriched_ref_and_resume_makes_no_calls(self):
        with TemporaryDirectory() as directory:
            runner = IncompleteRunner()
            first = await execute_platform_chain(plan(), directory, runner, resource_preparer=prepared)
            search_path = Path(directory) / "stages/literature_search/result.json"
            original = search_path.read_bytes()
            async def forbidden(*args):
                self.fail("completed resume must not prepare resources or call a Partner")
            second = await execute_platform_chain(plan(), directory, forbidden, resource_preparer=forbidden)
            self.assertEqual(first["state"], "completed")
            self.assertEqual(second["state"], "completed")
            self.assertEqual(search_path.read_bytes(), original)
            self.assertIsNone(read_json(search_path)["items"][0]["data"]["pdfUrl"])
            self.assertEqual(runner.payloads[1]["input"]["papers"][0]["pdfUrl"], PDF)
            self.assertEqual(runner.payloads[2]["input"]["repositoryUrls"], [REPOSITORY])
            self.assertEqual(runner.payloads[2]["input"]["paperNotes"][0]["source"],
                             runner.payloads[1]["input"]["papers"][0])

    async def test_legacy_unsent_reader_can_resume_explicitly_and_keeps_old_files(self):
        with TemporaryDirectory() as directory:
            runner = IncompleteRunner(fail_reader_preflight=True)
            with self.assertRaises(ValueError):
                await execute_platform_chain(plan(), directory, runner)
            old_dir = Path(directory) / "stages/paper_read"
            old_files = {name: (old_dir / name).read_bytes() for name in ("task.json", "record.json")}
            s1_result = (Path(directory) / "stages/literature_search/result.json").read_bytes()
            runner.fail_reader_preflight = False
            with self.assertRaisesRegex(ValueError, "FORMAL_STAGE_NOT_SAFE_TO_REPLAY"):
                await execute_platform_chain(plan(), directory, runner, resource_preparer=prepared)
            final = await execute_platform_chain(plan(), directory, runner,
                resource_preparer=prepared, resume_unstarted=True)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(runner.calls.count("literature_search"), 1)
            self.assertEqual(runner.calls.count("paper_read"), 2)  # old local preflight + one real dispatch
            for name, content in old_files.items():
                self.assertEqual((old_dir / name).read_bytes(), content)
            self.assertEqual((Path(directory) / "stages/literature_search/result.json").read_bytes(), s1_result)
            row = final["stages"][1]
            self.assertNotEqual(stage_directory(directory, row), old_dir)
            self.assertEqual(row["attempts"], 2)
            self.assertEqual(len(final["unstartedHistory"]), 1)

    async def test_invitation_only_reader_can_use_fresh_group_without_replaying_s1(self):
        with TemporaryDirectory() as directory:
            runner = InvitationOnlyRunner()
            with self.assertRaises(ValueError):
                await execute_platform_chain(plan(), directory, runner,
                                             resource_preparer=prepared)
            old_row = read_json(Path(directory) / "state.json")["stages"][1]
            old_dir = stage_directory(directory, old_row)
            old_record = (old_dir / "record.json").read_bytes()
            old_s1 = (Path(directory) / "stages/literature_search/result.json").read_bytes()
            runner.abandon_reader = False
            final = await execute_platform_chain(plan(), directory, runner,
                resource_preparer=prepared, resume_unstarted=True)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(runner.calls.count("literature_search"), 1)
            self.assertEqual(runner.calls.count("paper_read"), 2)
            self.assertEqual((old_dir / "record.json").read_bytes(), old_record)
            self.assertEqual((Path(directory) / "stages/literature_search/result.json").read_bytes(), old_s1)
            self.assertEqual(final["unstartedHistory"][-1]["recordSha256"], old_row["recordSha256"])

    async def test_conclusive_terminal_failure_requires_separate_manual_retry_flag(self):
        with TemporaryDirectory() as directory:
            runner = TerminalFailedRunner()
            with self.assertRaises(ValueError):
                await execute_platform_chain(plan(), directory, runner,
                                             resource_preparer=prepared)
            count = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "FORMAL_STAGE_NOT_PROVEN_UNSTARTED"):
                await execute_platform_chain(plan(), directory, runner,
                    resource_preparer=prepared, resume_unstarted=True)
            self.assertEqual(len(runner.calls), count)
            runner.fail_reader = False
            final = await execute_platform_chain(plan(), directory, runner,
                resource_preparer=prepared, resume_terminal_failed=True)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(runner.calls.count("literature_search"), 1)
            self.assertEqual(runner.calls.count("paper_read"), 2)

    async def test_remote_unknown_cannot_use_terminal_failed_retry_flag(self):
        with TemporaryDirectory() as directory:
            runner = FakeRunner(fail="paper_read")
            with self.assertRaises(ValueError):
                await execute_platform_chain(plan(), directory, runner)
            count = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "FORMAL_STAGE_NOT_PROVEN_TERMINAL_FAILED"):
                await execute_platform_chain(plan(), directory, runner,
                                             resume_terminal_failed=True)
            self.assertEqual(len(runner.calls), count)

    def test_invitation_only_resume_requires_complete_no_start_evidence(self):
        self.assertTrue(unstarted_preflight(invitation_only_record()))
        for key in ("startAttempted", "startPublished", "taskCommandsSent", "result",
                    "remoteStateUnknown", "partnerJoinConfirmed", "aclDeleted"):
            value = invitation_only_record()
            value[key] = (1 if key == "taskCommandsSent" else
                          {"unexpected": True} if key == "result" else
                          True if key in ("startAttempted", "startPublished", "remoteStateUnknown")
                          else False)
            with self.subTest(key=key):
                self.assertFalse(unstarted_preflight(value))

    async def test_invitation_cleanup_failure_blocks_new_runner_and_preserves_evidence(self):
        with TemporaryDirectory() as directory:
            runner = InvitationOnlyRunner()
            incomplete = invitation_only_record()
            incomplete.update(cleanupSucceeded=False, groupResourcesDeleted=False,
                              cleanupErrors=[{"step": "delete_group_resources"}])
            with patch(__name__ + ".invitation_only_record") as fixture:
                fixture.return_value = incomplete
                with self.assertRaises(ValueError):
                    await execute_platform_chain(plan(), directory, runner, resource_preparer=prepared)
            state_path = Path(directory) / "state.json"
            state_before = state_path.read_bytes()
            count = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "FORMAL_STAGE_NOT_PROVEN_UNSTARTED"):
                await execute_platform_chain(plan(), directory, runner,
                                             resource_preparer=prepared, resume_unstarted=True)
            self.assertEqual(runner.calls.count("literature_search"), 1)
            self.assertEqual(len(runner.calls), count)
            self.assertEqual(state_path.read_bytes(), state_before)

    def test_invitation_resume_requires_all_cleanup_evidence(self):
        for key in ("cleanupSucceeded", "aclDeleted", "groupResourcesDeleted", "connectionClosed"):
            for value in (False, None):
                record = invitation_only_record()
                record[key] = value
                with self.subTest(key=key, value=value):
                    self.assertFalse(unstarted_preflight(record))
        record = invitation_only_record()
        record["cleanupErrors"] = [{"step": "disband"}]
        self.assertFalse(unstarted_preflight(record))

    def test_clean_join_timeout_is_replayable_but_incomplete_cleanup_is_not(self):
        self.assertTrue(unstarted_preflight(join_timeout_record()))
        for key in ("cleanupSucceeded", "aclDeleted", "groupResourcesDeleted",
                    "connectionClosed"):
            value = join_timeout_record()
            value[key] = False
            with self.subTest(key=key):
                self.assertFalse(unstarted_preflight(value))
        value = join_timeout_record()
        value["cleanupErrors"] = [{"step": "delete_group_resources"}]
        self.assertFalse(unstarted_preflight(value))

    async def test_failed_preparation_stops_before_reader_and_can_be_resumed_explicitly(self):
        async def unavailable(*args):
            raise ValueError("VERIFIED_PDF_URL_UNAVAILABLE")
        with TemporaryDirectory() as directory:
            runner = IncompleteRunner()
            with self.assertRaisesRegex(ValueError, "VERIFIED_PDF_URL_UNAVAILABLE"):
                await execute_platform_chain(plan(), directory, runner, resource_preparer=unavailable)
            self.assertEqual(runner.calls, ["literature_search"])
            self.assertEqual(read_json(Path(directory) / "state.json")["state"], "blocked_resources")
            final = await execute_platform_chain(plan(), directory, runner,
                resource_preparer=prepared, resume_unstarted=True)
            self.assertEqual(final["state"], "completed")
            self.assertEqual(runner.calls.count("literature_search"), 1)

    async def test_tampered_resource_checkpoint_stops_without_new_calls(self):
        with TemporaryDirectory() as directory:
            runner = IncompleteRunner()
            state = await execute_platform_chain(plan(), directory, runner, resource_preparer=prepared)
            path = stage_directory(directory, state["stages"][1]) / "input_resolution.json"
            path.write_text("{}", encoding="utf-8")
            count = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "FORMAL_RESOURCE_CHECKPOINT_CHANGED"):
                await execute_platform_chain(plan(), directory, runner, resource_preparer=prepared)
            self.assertEqual(len(runner.calls), count)

    async def test_remotely_started_unknown_stage_is_not_replayed_even_with_flag(self):
        with TemporaryDirectory() as directory:
            runner = FakeRunner(fail="paper_read")
            with self.assertRaises(ValueError):
                await execute_platform_chain(plan(), directory, runner)
            count = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "FORMAL_STAGE_NOT_PROVEN_UNSTARTED"):
                await execute_platform_chain(plan(), directory, runner, resume_unstarted=True)
            self.assertEqual(len(runner.calls), count)

    def test_missing_or_positive_side_effect_flags_cannot_authorize_resume(self):
        self.assertTrue(unstarted_preflight(preflight_record()))
        for key in ("networkAttempted", "startAttempted", "invitationPublished", "remoteStateUnknown"):
            for missing in (True, False):
                record = preflight_record()
                if missing: record.pop(key)
                else: record[key] = True
                with self.subTest(key=key, missing=missing):
                    self.assertFalse(unstarted_preflight(record))


if __name__ == "__main__":
    unittest.main()
