"""Real factories/worker, offline task fixtures; no model, PDF or external API."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aip_record_paths import require_record_directory
import aip_literature_partner as literature
import aip_reader_partner as reader
import reader_aip_worker as worker
from tests.test_aip_reader import fixture_task, journal
from run_aip_research import failure_code


class RecordPathTests(unittest.TestCase):
    def test_legacy_directories_remain_supported(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for stage in ("literature", "reader"):
                output = root / "outputs" / ("aip-" + stage + "-offline")
                output.mkdir(parents=True)
                self.assertEqual(require_record_directory(output, root, stage), output.resolve())

    def test_single_entry_nested_stage_directories_supported(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for stage in ("literature", "reader"):
                output = root / "outputs/aip-research-offline" / stage
                output.mkdir(parents=True)
                self.assertEqual(require_record_directory(output, root, stage), output.resolve())

    def test_root_cache_outside_missing_wrong_stage_and_deep_nesting_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("outputs", "outputs/pdf-cache", "outside", "outputs/aip-research-/reader",
                             "outputs/other-run/reader", "outputs/aip-research-test/literature",
                             "outputs/aip-research-test/reader/extra"):
                output = root / relative
                output.mkdir(parents=True, exist_ok=True)
                with self.subTest(path=relative), self.assertRaises(ValueError):
                    require_record_directory(output, root, "reader")
            with self.assertRaises(ValueError):
                require_record_directory(root / "outputs/missing", root, "reader")

    def test_traversal_resolves_outside_and_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outputs").mkdir(); (root / "outside").mkdir()
            with self.assertRaises(ValueError):
                require_record_directory(root / "outputs/../outside", root, "reader")

    def test_resolved_link_escape_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); outputs = root / "outputs"; outputs.mkdir()
            outside = root / "outside"; outside.mkdir()
            # Exercise the result of Windows junction/symlink resolution without
            # requiring symlink privileges on the test machine.
            with patch.object(Path, "resolve", autospec=True, side_effect=lambda p: outside if p.name == "linked-run" else p):
                with self.assertRaises(ValueError):
                    require_record_directory(outputs / "linked-run", root, "reader")


class RealBoundaryTests(unittest.TestCase):
    def test_literature_factory_accepts_nested_before_provider(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); output = root / "outputs/aip-research-test/literature"
            output.mkdir(parents=True)
            with patch.object(literature, "ROOT", root), patch.dict(os.environ, {
                "RESEARCHMESH_AIP_RECORD_DIR": str(output), "RESEARCHMESH_AIP_WRAPPER_ROOT": str(root),
                "RESEARCHMESH_LOCAL_PARTNER_AIC": "offline-test"}), \
                patch.object(literature, "prepare_live_wrapper", return_value=(object(), object())) as prepare, \
                patch.object(literature, "create_local_business_app", return_value="offline-app"):
                self.assertEqual(literature.create_live_app(), "offline-app")
                prepare.assert_called_once()

    def test_reader_factory_accepts_nested_without_launching_worker(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); output = root / "outputs/aip-research-test/reader"
            output.mkdir(parents=True)
            (output / "reader_input.json").write_text(json.dumps({"payload": fixture_task()}), encoding="utf-8")
            with patch.object(reader, "ROOT", root), patch.dict(os.environ, {
                "RESEARCHMESH_AIP_RECORD_DIR": str(output), "RESEARCHMESH_READER_PYTHON": str(root / "python.exe"),
                "RESEARCHMESH_READER_ROOT": str(root), "RESEARCHMESH_LOCAL_PARTNER_AIC": "offline-reader"}), \
                patch.object(reader.subprocess, "Popen", side_effect=AssertionError("unexpected worker")), \
                patch.object(reader, "create_local_business_app", return_value="offline-app"):
                self.assertEqual(reader.create_live_app(), "offline-app")

    def test_factories_reject_outside_before_wrappers_or_worker(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); outside = root / "outside"; outside.mkdir()
            with patch.dict(os.environ, {"RESEARCHMESH_AIP_RECORD_DIR": str(outside),
                                        "RESEARCHMESH_AIP_WRAPPER_ROOT": str(root)}), \
                patch.object(literature, "ROOT", root), patch.object(reader, "ROOT", root), \
                patch.object(literature, "prepare_live_wrapper", side_effect=AssertionError("wrapper")):
                with self.assertRaises(ValueError): literature.create_live_app()
                with self.assertRaises(ValueError): reader.create_live_app()

    def test_real_worker_nested_preflight_accepts_completed_fixture_without_provider(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); output = root / "outputs/aip-research-test/reader"
            output.mkdir(parents=True)
            old = root / "journal.json"; old.write_text(json.dumps(journal()), encoding="utf-8")
            inp = output / "reader_input.json"
            inp.write_text(json.dumps({"payload": fixture_task(), "pdf": {"sha256": "a" * 64}}), encoding="utf-8")
            args = SimpleNamespace(record_dir=output, input=inp, reader_root=root, resume_journal=old, execute=False)
            wrapper = SimpleNamespace(PaperReadTask=SimpleNamespace(model_validate=lambda t: t))
            with patch.object(worker, "ROOT", root), patch.object(worker, "inspect_input", return_value=(fixture_task(), b"cached")), \
                patch.object(worker, "load_reader", return_value=wrapper), \
                patch.object(worker, "prepare_live_reader", side_effect=AssertionError("paid provider")):
                self.assertEqual(asyncio.run(worker.run(args)), 0)
            data = json.loads((output / "reader_preflight.json").read_text(encoding="utf-8"))
            self.assertFalse(data["modelExecutionAttempted"])

    def test_nested_worker_failure_record_saved_but_outside_not_written(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, writes in (("outputs/aip-research-test/reader", True), ("outside", False)):
                output = root / relative; output.mkdir(parents=True)
                with patch.object(worker, "ROOT", root):
                    self.assertEqual(worker.main(["--input", str(root / "missing.json"),
                        "--reader-root", str(root), "--record-dir", str(output)]), 1)
                self.assertEqual((output / "worker_failure.json").exists(), writes)

    def test_pipeline_exposes_safe_stage_code_but_not_provider_details(self):
        self.assertEqual(failure_code(ValueError("STAGE_FAILED_LITERATURE")), "STAGE_FAILED_LITERATURE")
        self.assertEqual(failure_code(ValueError("SECRET_SENTINEL private provider detail")), "ValueError")
