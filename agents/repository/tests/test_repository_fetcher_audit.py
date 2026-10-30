from __future__ import annotations

import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from researchmesh_repository.fetcher import (
    ProcessTerminationResult,
    RepositoryCloneError,
    RepositoryFetcher,
    _git_command,
    _git_environment,
    _run_git_stage,
)

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
    def test_git_trust_config_is_opt_in_and_preserves_git_configuration(self):
        with patch.dict(os.environ, {
            "REPOSITORY_GIT_TRUST_CONFIG": "1",
            "HTTPS_PROXY": "http://configured-proxy:8443",
        }, clear=False):
            command = _git_command("clone", "https://github.com/microsoft/LoRA")
            environment = _git_environment()

        self.assertEqual(command, ["git", "clone", "https://github.com/microsoft/LoRA"])
        self.assertEqual(environment["HTTPS_PROXY"], "http://configured-proxy:8443")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GIT_LFS_SKIP_SMUDGE"], "1")

    def test_git_trust_config_false_keeps_proxy_isolation(self):
        with patch.dict(os.environ, {
            "REPOSITORY_GIT_TRUST_CONFIG": "false",
            "HTTPS_PROXY": "http://configured-proxy:8443",
        }, clear=False):
            command = _git_command("status")
            environment = _git_environment()

        self.assertEqual(command[:5], ["git", "-c", "http.proxy=", "-c", "https.proxy="])
        self.assertNotIn("HTTPS_PROXY", environment)

    @staticmethod
    def success_processes():
        return [FakeProcess(), FakeProcess(), FakeProcess(stdout=COMMIT + "\n"), FakeProcess()]

    def test_success_fetches_shallow_head_blobs_before_checkout(self):
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
        self.assertNotIn("--filter=blob:none", command)
        self.assertIn("--single-branch", command)
        self.assertIn("--progress", command)
        self.assertIn("protocol.version=2", command)
        self.assertEqual(command[1:5], ["-c", "http.proxy=", "-c", "https.proxy="])
        environment = popen.call_args_list[0].kwargs["env"]
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["UNRELATED_VALUE"], "preserved")
        self.assertEqual(environment["GIT_LFS_SKIP_SMUDGE"], "1")
        checkout_command = popen.call_args_list[1].args[0]
        self.assertEqual(checkout_command[1:5],
                         ["-c", "http.proxy=", "-c", "https.proxy="])
        self.assertIn("--progress", checkout_command)
        self.assertTrue(any(str(value).startswith("core.hooksPath=") for value in checkout_command))
        checkout_environment = popen.call_args_list[1].kwargs["env"]
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                     "ALL_PROXY", "all_proxy"):
            self.assertNotIn(name, checkout_environment)

    def test_nonzero_clone_records_bounded_redacted_stderr_and_stops(self):
        secret = "https://user:password@github.com/microsoft/LoRA"
        fetcher = RepositoryFetcher()
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   return_value=FakeProcess(
                       returncode=128,
                       stderr=(f"fatal {URL} {secret} access_token=abc "
                               "Authorization: Bearer bearer-value "
                               "ghp_abcdefghijklmnopqrstuvwxyz"))) as popen, \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL): self.fail("failed clone must not yield")
        self.assertEqual(popen.call_count, 1)
        record = fetcher.git_diagnostics[0]
        self.assertEqual(record["returncode"], 128); self.assertFalse(record["timedOut"])
        self.assertNotIn("password", record["stderr"]); self.assertNotIn("abc", record["stderr"])
        self.assertNotIn("bearer-value", record["stderr"])
        self.assertNotIn("ghp_", record["stderr"])
        self.assertNotIn(URL, record["stderr"]); self.assertLessEqual(len(record["stderr"]), 1000)
        self.assertEqual(record["stdout"], "")
        self.assertFalse(record["stderrTruncated"])
        self.assertEqual(record["returncodeSource"], "natural-exit")

    def test_clone_strategy_has_no_partial_clone_lazy_fetch_dependency(self):
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   side_effect=self.success_processes()) as popen, \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with RepositoryFetcher().fetch(URL): pass
        clone = popen.call_args_list[0].args[0]
        checkout = popen.call_args_list[1].args[0]
        self.assertIn("--depth", clone)
        self.assertIn("--no-checkout", clone)
        self.assertFalse(any(str(value).startswith("--filter=") for value in clone))
        self.assertNotIn("fetch", checkout)

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
        self.assertIsNone(record["timeoutReturncode"])
        self.assertEqual(record["returncodeSource"], "post-termination")
        self.assertEqual(record["termination"]["method"], "taskkill-tree")
        self.assertEqual(record["termination"]["commandReturncode"], 0)
        self.assertIsNone(record["termination"]["treeCleanupVerified"])
        self.assertFalse(record["preCheckoutState"]["worktreeCheckoutVerified"])
        self.assertIsNone(record["resolvedCommit"])

    def test_timeout_can_have_no_obtainable_returncode(self):
        process = FakeProcess(timeout=True, stderr="hung")
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=process), \
                patch("researchmesh_repository.fetcher._terminate_owned_tree",
                      return_value=ProcessTerminationResult(
                          True, "mock", None, True, None, False)), \
                patch("researchmesh_repository.fetcher._close_process_pipes"):
            result = _run_git_stage(["git", "mock"], timeout=0.01)
        self.assertTrue(result.timed_out); self.assertIsNone(result.returncode)
        self.assertEqual(result.returncode_source, "post-termination")

    def test_timeout_race_records_natural_exit_without_termination(self):
        process = FakeProcess(returncode=7, timeout=True, stdout="partial out")
        process.returncode = 7
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=process), \
                patch("researchmesh_repository.fetcher._terminate_owned_tree",
                      return_value=ProcessTerminationResult(
                          False, None, None, False, 7, None)) as terminate:
            result = _run_git_stage(["git", "mock"], timeout=0.01)
        terminate.assert_called_once()
        self.assertTrue(result.timed_out)
        self.assertEqual(result.timeout_returncode, 7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.returncode_source, "natural-exit-race-after-timeout")
        self.assertFalse(result.termination_attempted)

    def test_timeout_race_between_status_check_and_termination_is_distinguished(self):
        process = FakeProcess(returncode=7, timeout=True)
        polls = iter([None, 7, 7])
        process.poll = lambda: next(polls, 7)
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=process):
            result = _run_git_stage(["git", "mock"], timeout=0.01)
        self.assertIsNone(result.timeout_returncode)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.returncode_source,
                         "natural-exit-race-before-termination")
        self.assertFalse(result.termination_attempted)

    def test_timeout_preserves_partial_and_final_stdout_and_stderr_separately(self):
        process = FakeProcess(returncode=1, timeout=True,
                              stdout="partial-out\n", stderr="partial-err\n",
                              second_stdout="final-out\n", second_stderr="final-err\n")
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=process), \
                patch("researchmesh_repository.fetcher._terminate_owned_tree",
                      return_value=ProcessTerminationResult(
                          True, "mock", 0, False, 1, True)):
            result = _run_git_stage(["git", "mock"], timeout=0.01)
        self.assertEqual(result.stdout, "partial-out\nfinal-out\n")
        self.assertEqual(result.stderr, "partial-err\nfinal-err\n")

    def test_streams_are_independently_redacted_and_truncated(self):
        fetcher = RepositoryFetcher()
        long_stdout = "out " + URL + " " + ("x" * 1100)
        long_stderr = ("api_key=secret Authorization: Bearer bearer-secret "
                       "ghp_abcdefghijklmnopqrstuvwxyz " + ("y" * 1100))
        with patch("researchmesh_repository.fetcher.subprocess.Popen",
                   return_value=FakeProcess(returncode=128, stdout=long_stdout,
                                            stderr=long_stderr)), \
                patch("researchmesh_repository.fetcher._remove_temp_root"):
            with self.assertRaises(RepositoryCloneError):
                with fetcher.fetch(URL): pass
        record = fetcher.git_diagnostics[0]
        self.assertTrue(record["stdoutTruncated"])
        self.assertTrue(record["stderrTruncated"])
        self.assertEqual(len(record["stdout"]), 1000)
        self.assertEqual(len(record["stderr"]), 1000)
        self.assertNotIn(URL, record["stdout"])
        self.assertNotIn("secret", record["stderr"])

    def test_termination_command_failure_uses_fallback_and_is_not_tree_verified(self):
        process = FakeProcess(returncode=1, timeout=True)
        with patch("researchmesh_repository.fetcher.subprocess.Popen", return_value=process), \
                patch("researchmesh_repository.fetcher.subprocess.run",
                      side_effect=subprocess.TimeoutExpired(["taskkill"], 10)):
            result = _run_git_stage(["git", "mock"], timeout=0.01)
        self.assertTrue(result.termination_attempted)
        self.assertIsNone(result.termination_command_returncode)
        self.assertTrue(result.fallback_kill_attempted)
        self.assertIsNone(result.tree_cleanup_verified)

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
