"""No-network tests for archived Reader full-text result replay via AIP SDK."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from run_reader_aip_replay_v02 import load_archived_result, replay_via_aip


class ReaderReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "archive.json"
        self.record = {
            "status": "PASS", "warmResult": {
                "schemaVersion": "0.2", "subtaskId": "reader-replay-S1", "taskType": "paper_read",
                "summary": "archived synthetic example (not new research)",
                "items": [{"kind": "paper_note", "data": {
                    "source": {"paperId": "demo-1", "title": "Demo", "doi": None,
                               "url": "https://example.org/paper", "pdfUrl": "https://example.org/paper.pdf",
                               "repositoryUrls": []},
                    "method": "Demonstration method", "findings": "Demonstration findings",
                    "limitations": "Synthetic fixture", "analysisLevel": "fulltext",
                    "evidence": [{"sourceId": "demo-source-1", "url": "https://example.org/paper.pdf",
                                  "locator": "page 1", "supports": "Demonstration evidence"}],
                }}], "warnings": [],
            },
        }
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.record), encoding="utf-8")

    def test_load_archived_fulltext_validates(self):
        task, result, meta = load_archived_result(self.path)
        self.assertEqual(task.schemaVersion, "0.2")
        self.assertEqual(task.subtaskId, result["subtaskId"])
        self.assertEqual(meta["evidenceCount"], 1)
        self.assertTrue(meta["replayOnly"])

    def test_reject_non_pass_archive(self):
        self.record["status"] = "FAILED"
        self.write()
        with self.assertRaisesRegex(ValueError, "ARCHIVE_NOT_PASS"):
            load_archived_result(self.path)

    def test_reject_missing_evidence_url(self):
        self.record["warmResult"]["items"][0]["data"]["evidence"][0]["url"] = None
        self.write()
        with self.assertRaises(ValueError):
            load_archived_result(self.path)

    def test_sdk_round_trip_does_not_claim_fresh_research(self):
        task, result, _ = load_archived_result(self.path)
        outcome = asyncio.run(replay_via_aip(task, result))
        self.assertEqual(outcome.status, "completed", outcome.error_code)
        self.assertTrue(outcome.protocol_completed)
        self.assertTrue(outcome.business_validated)
        self.assertFalse(outcome.research_succeeded)
        self.assertEqual(outcome.backend_mode, "fixture")
        self.assertIsNone(outcome.error_code)
        self.assertEqual(outcome.result, result)


if __name__ == "__main__":
    unittest.main()
