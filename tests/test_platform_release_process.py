"""Real stdin/process shutdown regression; platform objects are offline mocks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import asyncio
from pathlib import Path
import sys
from unittest.mock import patch
from tests.test_platform_literature import WorkflowTests, LEADER, PARTNER
import run_platform_literature as cli

mode = sys.argv[2]
async def offline_runner(config, task, **kwargs):
    release = kwargs["task_release"]
    async def cancel_release(record):
        current = asyncio.current_task()
        handle = asyncio.get_running_loop().call_later(.1, current.cancel)
        try:
            await release(record)
        finally:
            handle.cancel()
    rec, _, _, _, _, _ = await WorkflowTests().scenario(
        task_release=cancel_release if mode == "cancel" else release,
        checkpoint=kwargs["checkpoint"])
    return rec

task_path = Path.cwd() / "examples/group_literature_single_task_v02.json"
with patch.object(cli, "ROOT", Path(sys.argv[1])), patch.object(cli, "run_group_literature", offline_runner):
    status = cli.main(["--leader-aic", LEADER, "--partner-aic", PARTNER,
        "--credentials-dir", sys.argv[1], "--task", str(task_path),
        "--partner-commit", "abcdef1", "--execute", "--partner-ready",
        "--confirm-after-join", "--release-timeout", "1"])
print("PROCESS_EXIT_REACHED", status, flush=True)
'''


class ReleaseProcessTests(unittest.TestCase):
    def run_child(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, "-B", "-u", "-X", "utf8", "-c", CHILD, directory, mode],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
                env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                if mode == "send":
                    process.stdin.write("SEND\n")
                    process.stdin.flush()
                elif mode == "eof":
                    process.stdin.close()
                    process.stdin = None
                # Do not use communicate before wait: it would close stdin,
                # masking the original blocked-input shutdown bug.
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.fail("Release deadline/cancellation left the CLI blocked on stdin")
                stdout, stderr = process.communicate(timeout=2)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn("PROCESS_EXIT_REACHED", stdout)
                reports = list(Path(directory).glob("outputs/platform-literature/*/report.md"))
                self.assertEqual(len(reports), 1, stdout + stderr)
                record = json.loads(reports[0].with_name("record.json").read_text(encoding="utf-8"))
                self.assertTrue(record["cleanupSucceeded"])
                self.assertTrue(record["connectionClosed"])
                return record, reports[0].read_text(encoding="utf-8")
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)

    def test_unanswered_prompt_times_out_exits_and_writes_report(self):
        rec, report = self.run_child("timeout")
        self.assertEqual(rec["error"]["code"], "TASK_RELEASE_TIMEOUT")
        self.assertEqual(rec["taskCommandsSent"], 0)
        self.assertFalse(rec["taskReleaseConfirmed"])
        self.assertFalse(rec["startAttempted"])
        self.assertIn("TASK_RELEASE_TIMEOUT", report)

    def test_cancellation_with_blocked_stdin_exits_after_cleanup(self):
        rec, _ = self.run_child("cancel")
        self.assertEqual(rec["error"]["code"], "INTERRUPTED")
        self.assertEqual(rec["taskCommandsSent"], 0)
        self.assertFalse(rec["startAttempted"])

    def test_closed_stdin_sends_no_start_and_writes_report(self):
        rec, _ = self.run_child("eof")
        self.assertEqual(rec["error"]["code"], "TASK_RELEASE_INPUT_CLOSED")
        self.assertEqual(rec["taskCommandsSent"], 0)

    def test_send_preserves_single_start_complete_and_result(self):
        rec, report = self.run_child("send")
        self.assertTrue(rec["passed"])
        self.assertTrue(rec["taskReleaseConfirmed"])
        self.assertTrue(rec["protocolCompleted"])
        self.assertEqual(rec["taskCommandsSent"], 2)
        self.assertEqual(len(rec["result"]), 6)
        self.assertIn("不是 Provider HTTP 请求次数", report)
