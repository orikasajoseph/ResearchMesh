from __future__ import annotations

import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from researchmesh_repository.fetcher import RepositoryCloneError, RepositoryFetcher, _run_git_stage

URL = "https://github.com/microsoft/LoRA"
COMMIT = "a" * 40


class FakeProcess:
    next_pid = 41000

    def __init__(self, *, returncode=0, stdout="", stderr="", timeout=False,
                 second_stdout="", second_stderr=""):
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None if timeout else returncode
        self.stdout, self.stderr, self.stdin = Mock(), Mock(), None
        self._final_returncode, self._timeout, self._calls = returncode, timeout, 0
        self._output, self._second = (stdout, stderr), (second_stdout, second_stderr)

    def communicate(self, timeout=None):
        self._calls += 1
        if self._timeout and self._calls == 1:
            raise subprocess.TimeoutExpired(["git"], timeout, output=self._output[0], stderr=self._output[1])
        return self._second if self._timeout else self._output

    def poll(self): return self.returncode
    def wait(self, timeout=None):
        self.returncode = self._final_returncode
        return self.returncode
    def kill(self): self.returncode = self._final_returncode


class RepositoryFetcherAuditTests(unittest.TestCase):
    @staticmethod
    def success_processes():
        return [FakeProcess(), FakeProcess(), FakeProcess(stdout=COMMIT + "\n"), FakeProcess()]

    def test_success_splits_clone_checkout_head_worktree_and_direct_child_policy(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://bad:1", "http_proxy": "http://bad:2",
                  "HTTPS_PROXY": "http://bad:3", "UNRELATED_VALUE": "preserved"}, clear=False), \
                patch("researchmesh_repository.fetcher.subprocess.Popen",
                      side_effect=self.success_processes()) as popen, \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            fetcher = RepositoryFetcher()
            with fetcher.fetch(URL) as fetched: self.assertEqual(fetched.commit_hash, COMMIT)
        self.assertEqual([x["stage"] for x in fetcher.git_diagnostics],
                         ["clone", "checkout", "head", "worktree", "cleanup"])
        self.assertEqual(fetcher.git_diagnostics[2]["resolvedCommit"], COMMIT)
        self.assertEqual(fetcher.git_diagnostics[3]["resolvedCommit"], COMMIT)
        command = popen.call_args_list[0].args[0]
        self.assertIn("--no-checkout", command)
        self.assertEqual(command[1:5], ["-c", "http.proxy=", "-c", "https.proxy="])
        environment = popen.call_args_list[0].kwargs["env"]
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["UNRELATED_VALUE"], "preserved")

    def test_nonzero_clone_records_bounded_redacted_stderr_and_stops(self):
        secret = "https://user:password@github.com/microsoft/LoRA"
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   return_value=FakeProcess(returncode=128, stderr=f"fatal {URL} {secret} access_token=abc")) as popen, \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL): self.fail("failed clone must not yield")
        self.assertEqual(popen.call_count, 1)
        record = fetcher.git_diagnostics[0]
        self.assertEqual(record["returncode"], 128); self.assertFalse(record["timedOut"])
        self.assertNotIn("password", record["stderr"]); self.assertNotIn("abc", record["stderr"])
        self.assertNotIn(URL, record["stderr"]); self.assertLessEqual(len(record["stderr"]), 1000)

    def test_checkout_100_percent_then_hangs_kills_owned_tree_and_stops(self):
        checkout = FakeProcess(returncode=1, timeout=True,
                               stderr="Updating files: 100% (1189/1189), done.")
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   side_effect=[FakeProcess(), checkout]) as popen, \
                patch("researchmesh_repository.fetcher.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)) as run, \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL): pass
        self.assertEqual(popen.call_count, 2); run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["taskkill", "/PID"]); self.assertIn("/T", command); self.assertIn("/F", command)
        record = fetcher.git_diagnostics[1]
        self.assertEqual(record["stage"], "checkout"); self.assertTrue(record["timedOut"])
        self.assertEqual(record["returncode"], 1); self.assertIn("100%", record["stderr"])
        self.assertIsNone(record["resolvedCommit"])

    def test_timeout_can_have_no_obtainable_returncode(self):
        process = FakeProcess(timeout=True, stderr="hung")
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=process), \
                patch("researchmesh_repository.fetcher._terminate_owned_tree"), \
                patch("researchmesh_repository.fetcher._close_process_pipes"):
            result = _run_git_stage(["git", "mock"], timeout=0.01)
        self.assertTrue(result.timed_out); self.assertIsNone(result.returncode)

    def test_head_failure_never_produces_resolved_commit(self):
        processes = [FakeProcess(), FakeProcess(), FakeProcess(returncode=2, stderr="not a repository")]
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.Popen", side_effect=processes), \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL): pass
        record = fetcher.git_diagnostics[-2]
        self.assertEqual(record["stage"], "head"); self.assertEqual(record["returncode"], 2)
        self.assertIsNone(record["resolvedCommit"])

    def test_invalid_head_text_never_produces_resolved_commit(self):
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   side_effect=[FakeProcess(), FakeProcess(), FakeProcess(stdout="not-a-sha\n")]), \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL): pass
        self.assertIsNone(fetcher.git_diagnostics[-2]["resolvedCommit"])

    def test_clone_failure_remains_primary_when_cleanup_also_fails(self):
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   return_value=FakeProcess(returncode=128, stderr="clone refused")), \
                patch("researchmesh_repository.fetcher._remove_temp_root", side_effect=OSError("cleanup denied")):
            with self.assertRaisesRegex(RepositoryCloneError, "clone refused"):
                with fetcher.fetch(URL): pass
        self.assertEqual([x["stage"] for x in fetcher.git_diagnostics], ["clone", "cleanup"])
        self.assertIn("cleanup denied", fetcher.git_diagnostics[-1]["stderr"])

    def test_timing_and_timeout_flags_come_from_stage_runner(self):
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=FakeProcess()), \
                patch("researchmesh_repository.fetcher.time.perf_counter", side_effect=[10.0, 10.25]):
            result = _run_git_stage(["git", "mock"], timeout=2)
        self.assertEqual(result.duration_ms, 250.0); self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__": unittest.main()
