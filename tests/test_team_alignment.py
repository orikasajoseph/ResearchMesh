"""Frozen-snapshot alignment: no Git, keys, external HTTP, model or downloads."""
from copy import deepcopy
from hashlib import sha1
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from aip_business_partner import validate_local_task, LocalBackendFailure
from common.models import BUSINESS_TASK_FIELDS
from literature_adapter import normalize_response, to_wrapper_request, AdapterError
from run_aip_literature import assess_live_run, live_partner
from tests.test_literature_adapter import plan, response
from tests.test_reader_repository_handoff import payload

ROOT = Path(__file__).resolve().parents[1]


def blob(path):
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()


def openalex_result():
    task = {k: deepcopy(plan()["tasks"][0][k]) for k in BUSINESS_TASK_FIELDS}
    request = to_wrapper_request(task, "test-request")
    raw = response(); raw["provider"] = "openalex"
    normalized = normalize_response(task, request, raw)
    outcome = {"execution": {"state": "completed", "simulatedResult": False, "result": normalized["result"]}, "error": None}
    normalized["diagnostics"].update(providerUsed="openalex", openalexRequests=1, openalexHttpStatus=200,
                                      httpObservationAvailable=True, httpRequests=[{"httpStatus": 429}])
    return task, outcome, normalized


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("no external network")))
        self.enterContext(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("no external network")))

    def test_thirteen_business_overlap_paths_are_unique_eight_code_modules_aligned(self):
        manifest = json.loads((ROOT / "docs/delivery/zhao_integration_alignment_20261020.json").read_text(encoding="utf-8"))
        rows = manifest["overlap"]
        self.assertEqual(len(rows), 13)
        self.assertEqual(len({r["path"] for r in rows}), 13)
        aligned = [r for r in rows if r["identical"]]
        self.assertEqual(len(aligned), 8)
        for row in aligned:
            self.assertEqual(blob(ROOT / row["path"]), row["previewBlob"], row["path"])
        # Publication is a historical record, not a permanent "unpublished"
        # invariant. The manifest now records explicit integration commits.
        self.assertEqual(set(manifest["localCommits"]), {"businessIntegration", "groupInvitationTool"})
        for commit in manifest["localCommits"].values():
            self.assertRegex(commit, r"^[0-9a-f]{7,40}$")
        self.assertIn("verify the remote branch", manifest["remotePublicationRule"])
        self.assertFalse(manifest["integrationPolicy"]["wholePreviewMerge"])

    def test_bundled_reader_business_sources_are_exact_snapshot_not_reimplemented(self):
        manifest = json.loads((ROOT / "docs/delivery/zhao_integration_alignment_20261020.json").read_text(encoding="utf-8"))
        for row in manifest["selectedReaderBusinessFiles"]:
            self.assertEqual(blob(ROOT / row["path"]), row["previewBlob"], row["path"])

    def test_bundled_reader_shared_six_fields_and_concrete_evidence_supports(self):
        from agents.paper_reader.researchmesh_paper_reader import PaperRef, PaperEvidence
        self.assertEqual(set(PaperRef.model_fields), {"paperId", "title", "doi", "url", "pdfUrl", "repositoryUrls"})
        sample = {"sourceId": "p", "url": "https://example.invalid/p.pdf", "locator": "p1", "supports": "A specific claim."}
        self.assertEqual(PaperEvidence.model_validate(sample).supports, sample["supports"])
        sample["supports"] = ["method"]
        with self.assertRaises(ValueError): PaperEvidence.model_validate(sample)

    def test_openalex_label_is_preserved_not_relabelled_semantic_scholar(self):
        _, outcome, backend = openalex_result()
        self.assertEqual(outcome["execution"]["result"]["items"][0]["data"]["provider"], "openalex")
        self.assertTrue(assess_live_run(outcome, backend)["businessReady"])
        self.assertFalse(assess_live_run(outcome, backend)["semanticHttp200Observed"])

    def test_fallback_name_without_successful_observed_request_not_live_pass(self):
        for change in ("no-status", "wrong-status", "no-requests", "bool-requests", "wrong-provider", "wrong-item"):
            _, outcome, backend = openalex_result()
            diagnostics = backend["diagnostics"]
            if change == "no-status": diagnostics.pop("openalexHttpStatus")
            elif change == "wrong-status": diagnostics["openalexHttpStatus"] = 429
            elif change == "no-requests": diagnostics["openalexRequests"] = 0
            elif change == "bool-requests": diagnostics["openalexRequests"] = True
            elif change == "wrong-provider": diagnostics["provider"] = "semantic-scholar"
            else:
                outcome["execution"]["result"]["items"][0]["data"]["provider"] = "semantic-scholar"
            with self.subTest(change=change):
                self.assertFalse(assess_live_run(outcome, backend)["businessReady"])

    def test_semantic_200_does_not_certify_failed_or_unobserved_openalex(self):
        _, outcome, backend = openalex_result()
        backend["diagnostics"].update(openalexHttpStatus=503, httpRequests=[{"httpStatus": 200}])
        self.assertFalse(assess_live_run(outcome, backend)["passed"])

    def test_unknown_provider_not_silently_accepted(self):
        task, _, _ = openalex_result()
        raw = response(); raw["provider"] = "invented-provider"
        with self.assertRaisesRegex(AdapterError, "INVALID_WRAPPER_RESULT"):
            normalize_response(task, to_wrapper_request(task, "test-request"), raw)

    def test_repository_context_checked_before_worker_without_concrete_partner_import(self):
        value = payload()
        self.assertEqual(validate_local_task(value), value)
        value["input"]["paperNotes"][0]["source"]["paperId"] = "wrong-paper"
        with self.assertRaises(ValueError): validate_local_task(value)

    def test_known_preview_error_codes_retained_but_arbitrary_detail_not_exported(self):
        for code in ("PDF_DOWNLOAD_FAILED", "PDF_SOURCE_UNVERIFIED", "REPOSITORY_UNAVAILABLE", "INVALID_REPOSITORY_URL"):
            self.assertEqual(LocalBackendFailure(code).code, code)
        self.assertEqual(LocalBackendFailure("private provider detail").code, "BACKEND_FAILED")

    def test_isolated_partner_runtime_invalid_path_rejected_before_any_process(self):
        with patch("run_aip_literature.subprocess.Popen", side_effect=AssertionError("process must not start")):
            with self.assertRaisesRegex(ValueError, "LOCAL_PARTNER_PYTHON_NOT_FOUND"):
                with live_partner(ROOT, ROOT, python_executable=ROOT / "missing-python-do-not-create.exe"):
                    self.fail("unreachable")

    def test_reader_root_default_is_repository_relative_not_personal_directory(self):
        import os
        import run_aip_research
        original_parse = run_aip_research.argparse.ArgumentParser.parse_args
        def capture(parser, argv=None):
            result = original_parse(parser, argv)
            self.assertEqual(result.reader_root, ROOT / "agents/paper_reader")
            return result
        with patch.dict(os.environ, {}, clear=True), patch.object(run_aip_research.argparse.ArgumentParser, "parse_args", capture):
            self.assertEqual(run_aip_research.main([]), 0)

    def test_wrapper_native_repository_result_requires_existing_adapter(self):
        from result_contract import validate_result
        from tests.test_repository_support import native
        # Do not broaden the shared contract to two competing item structures.
        with self.assertRaises(ValueError): validate_result(native(), payload())


if __name__ == "__main__":
    unittest.main()
