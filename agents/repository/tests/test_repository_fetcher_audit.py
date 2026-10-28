from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from researchmesh_repository.fetcher import RepositoryCloneError, RepositoryFetcher


URL = "https://github.com/microsoft/LoRA"


def completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class RepositoryFetcherAuditTests(unittest.TestCase):
    def run_fetch(self, effects):
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.run", side_effect=effects), \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with fetcher.fetch(URL) as fetched:
                self.assertEqual(fetched.commit_hash, "a" * 40)
        return fetcher

    def test_clone_success_records_clone_head_cleanup_and_direct_child_policy(self):
        with patch.dict(os.environ, {
            "HTTP_PROXY": "http://bad:1", "http_proxy": "http://bad:2",
            "HTTPS_PROXY": "http://bad:3", "UNRELATED_VALUE": "preserved",
        }, clear=False), patch("researchmesh_repository.fetcher.subprocess.run") as run, \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            run.side_effect = [completed(), completed(stdout="a" * 40 + "\n")]
            fetcher = RepositoryFetcher()
            with fetcher.fetch(URL):
                pass
        self.assertEqual([x["stage"] for x in fetcher.git_diagnostics],
                         ["clone", "head", "cleanup"])
        self.assertEqual(fetcher.git_diagnostics[1]["resolvedCommit"], "a" * 40)
        clone_call = run.call_args_list[0]
        command, environment = clone_call.args[0], clone_call.kwargs["env"]
        self.assertEqual(command[1:5], ["-c", "http.proxy=", "-c", "https.proxy="])
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                     "ALL_PROXY", "all_proxy"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["UNRELATED_VALUE"], "preserved")
        self.assertEqual(environment["NO_PROXY"], "github.com")
        self.assertNotIn("config", command[5:])

    def test_nonzero_clone_records_bounded_redacted_stderr_and_no_result(self):
        secret = "https://user:password@github.com/microsoft/LoRA"
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.run",
                   return_value=completed(128, stderr=f"fatal {URL} {secret} access_token=abc")), \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL):
                    self.fail("failed clone must not yield a repository")
        record = fetcher.git_diagnostics[0]
        self.assertEqual(record["returncode"], 128)
        self.assertFalse(record["timedOut"])
        self.assertNotIn("password", record["stderr"])
        self.assertNotIn("abc", record["stderr"])
        self.assertNotIn(URL, record["stderr"])
        self.assertLessEqual(len(record["stderr"]), 1000)

    def test_clone_timeout_is_real_subprocess_timeout(self):
        fetcher = RepositoryFetcher()
        timeout = subprocess.TimeoutExpired(["git", "clone"], 90, stderr="slow")
        with patch("researchmesh_repository.fetcher.subprocess.run", side_effect=timeout), \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL):
                    pass
        record = fetcher.git_diagnostics[0]
        self.assertEqual(record["stage"], "clone")
        self.assertIsNone(record["returncode"])
        self.assertTrue(record["timedOut"])

    def test_head_failure_is_distinct_from_clone(self):
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.run", side_effect=[
            completed(), completed(2, stderr="not a repository")
        ]), patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL):
                    pass
        self.assertEqual(fetcher.git_diagnostics[-2]["stage"], "head")
        self.assertEqual(fetcher.git_diagnostics[-2]["returncode"], 2)

    def test_cleanup_failure_is_distinct_and_has_no_returncode(self):
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.run", side_effect=[
            completed(), completed(stdout="a" * 40)
        ]), patch("researchmesh_repository.fetcher._remove_temp_root",
                  side_effect=OSError("denied")):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL):
                    pass
        record = fetcher.git_diagnostics[-1]
        self.assertEqual(record["stage"], "cleanup")
        self.assertIsNone(record["returncode"])
        self.assertFalse(record["timedOut"])
        self.assertIn("denied", record["stderr"])


if __name__ == "__main__":
    unittest.main()
