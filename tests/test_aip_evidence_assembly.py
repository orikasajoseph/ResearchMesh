import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
import tests.test_aip_reader as fixture
from aip_reader_inputs import prepare_reader_input
from assemble_aip_evidence import assemble, render
from input_binding import bind_task_input, InputBindingError
from reader_adapter import normalize_reader_result, ReaderAdapterError


class AssemblyTests(unittest.TestCase):
    def files(self, directory):
        root = Path(directory)
        source, proof, cache = fixture.ReaderInputTests().make_files(directory)
        search_path = source / "outcome.json"; search = json.loads(search_path.read_text())
        search["execution"]["latencyMs"] = 1.5
        for row in search["aipMessages"]:
            row["command"]["sentAt"] = "2026-10-12T10:00:00Z"
            row["response"]["result"]["sentAt"] = "2026-10-12T10:00:01Z"
        search["execution"]["events"] = [{"command": row["command"]["command"],
            "state": row["response"]["result"]["status"]["state"], "at": "2026-10-12T10:01:00Z"}
            for row in search["aipMessages"]]
        search_path.write_text(json.dumps(search))
        prepared = prepare_reader_input(source, proof, cache_dir=cache)
        payload = prepared["payload"]
        raw = fixture.note_result(payload)
        journal = fixture.journal(payload); journal["pdf"] = prepared["pdf"]
        journal["outcome"] = normalize_reader_result(payload, raw)
        out = deepcopy(search)
        out["execution"].update(result=raw, subtaskId="S2", taskId="aip-reader-task", agentAic="reader-agent", latencyMs=2.)
        for row in out["aipMessages"]:
            row["command"]["taskId"] = "aip-reader-task"
            row["response"]["result"].update(taskId="aip-reader-task", senderId="reader-agent")
        out["aipMessages"][0]["command"]["dataItems"][0]["data"] = payload
        reader = root / "reader"; reader.mkdir()
        for name, value in (("reader_input", prepared), ("reader_journal", journal), ("outcome", out)):
            (reader / (name + ".json")).write_text(json.dumps(value))
        return source, reader, cache

    def test_assembly_retains_real_shaped_metadata_no_calls_or_history_writes(self):
        with TemporaryDirectory() as directory:
            source, reader, cache = self.files(directory)
            before = {str(p): p.read_bytes() for folder in (source, reader) for p in folder.glob("*.json")}
            with patch.object(httpx.Client, "get", side_effect=AssertionError("network")), \
                 patch.object(httpx.AsyncClient, "post", side_effect=AssertionError("model or task replay")):
                result = assemble(source, reader, cache_dir=cache)
            self.assertEqual(result["acceptance"]["sumOriginalTaskLatencyMs"], 3.5)
            self.assertEqual(result["executionRecords"][1]["finishedAt"], "2026-10-12T10:01:00Z")
            self.assertNotEqual(result["executionRecords"][1]["finishedAt"], result["executionRecords"][1]["partnerEnvelopeSentAt"])
            self.assertEqual([r["requiredSkill"] for r in result["executionRecords"]], ["literature.search", "paper.analyze"])
            self.assertEqual([r["aipTaskId"] for r in result["executionRecords"]], ["aip-search-task", "aip-reader-task"])
            self.assertFalse(result["acceptance"]["singleFreshEndToEndRun"])
            self.assertFalse(result["acceptance"]["platformDiscoveryUsed"])
            self.assertFalse(result["repositoryHandoff"]["taskExecuted"])
            self.assertIn("REPOSITORY_URL_REQUIRED", result["repositoryHandoff"]["blockers"])
            self.assertEqual(before, {str(p): p.read_bytes() for folder in (source, reader) for p in folder.glob("*.json")})
            self.assertIn("不是一次新端到端运行", render(result))

    def test_rejects_source_task_session_and_product_mismatches(self):
        for kind in ("source", "task", "session", "product", "pdf"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                source, reader, cache = self.files(directory)
                if kind == "source":
                    path = reader / "reader_input.json"; data = json.loads(path.read_text()); data["searchSource"] = str(reader)
                elif kind == "pdf":
                    path = reader / "reader_journal.json"; data = json.loads(path.read_text()); data["pdf"]["sha256"] = "b" * 64
                else:
                    path = reader / "outcome.json"; data = json.loads(path.read_text())
                    if kind == "task": data["aipMessages"][0]["command"]["taskId"] = "other"
                    elif kind == "session": data["aipMessages"][1]["response"]["result"]["sessionId"] = "other"
                    else: data["execution"]["result"]["summary"] = "Changed"
                path.write_text(json.dumps(data))
                with self.assertRaises(ReaderAdapterError): assemble(source, reader, cache_dir=cache)

    def test_s3_binding_preserves_notes_requires_explicit_url(self):
        with TemporaryDirectory() as directory:
            source, reader, cache = self.files(directory)
            record = assemble(source, reader, cache_dir=cache)
            upstream = deepcopy(record["results"][1])
            task = {"schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
                    "goal": "Only analyze an explicitly supplied repository with paper notes",
                    "input": {"papers": [], "paperNotes": [], "repositoryUrls": []},
                    "constraints": {"executeCode": False, "allowRepositorySearch": False,
                                    "requirePaperNotes": True, "maxRepositories": 1},
                    "requiredSkill": "repository.analyze", "dependsOn": ["S2"], "outputSchema": "code_records.v1"}
            with self.assertRaises(InputBindingError): bind_task_input(task, {"S2": upstream})
            upstream["items"][0]["data"]["source"]["repositoryUrls"] = ["https://github.com/offline/example"]
            bound = bind_task_input(task, {"S2": upstream})
            self.assertEqual(bound["input"]["repositoryUrls"], ["https://github.com/offline/example"])
            self.assertEqual(bound["input"]["paperNotes"][0]["evidence"], upstream["items"][0]["data"]["evidence"])
            self.assertEqual(bound["input"]["papers"][0]["paperId"], upstream["items"][0]["data"]["source"]["paperId"])
