"""Repository per-new-task directory creation, with fully mocked Worker calls."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aip.repository_unified_executor import RepositoryStaticExecutor
from test_group_repository_platform import task_payload, valid_result
from common.models import BusinessTask


class RepositoryStaticExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_different_tasks_create_distinct_worker_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            python = root / "bin" / "python.exe"
            python.write_text("placeholder")
            wrapper = root / "wrapper"
            (wrapper / "researchmesh_repository").mkdir(parents=True)
            (wrapper / "researchmesh_repository" / "__init__.py").write_text("")
            backend = AsyncMock(side_effect=_value)
            calls = []

            def build(**kwargs):
                self.assertEqual(kwargs["analysis_mode"], "static")
                self.assertTrue(kwargs["dynamic"])
                self.assertIsNone(kwargs["env_file"])
                calls.append(Path(kwargs["record_dir"]))
                return backend

            with patch("aip.repository_unified_executor.ROOT", root), \
                 patch("aip.repository_unified_executor.create_repository_business_executor", side_effect=build):
                executor = RepositoryStaticExecutor(repository_python=python, wrapper_root=wrapper)
                out1 = await executor(BusinessTask.model_validate(task_payload("R1")))
                out2 = await executor(BusinessTask.model_validate(task_payload("R2")))

            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0], calls[1])
            self.assertEqual(backend.await_count, 2)
            self.assertEqual(out1["subtaskId"], "R1")
            self.assertEqual(out2["subtaskId"], "R2")
            self.assertTrue(all(p.is_dir() for p in calls))


async def _value(task):
    return valid_result(BusinessTask.model_validate(task))
