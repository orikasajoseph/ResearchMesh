"""Safety tests for the formal three-Agent CLI; no platform calls."""
from pathlib import Path
import unittest
from unittest.mock import patch

import run_platform_three_agent as cli
from tests.test_group_invitation import LEADER, PARTNER


ROOT = Path(cli.__file__).resolve().parent


def arguments():
    return ["--leader-aic", LEADER,
            "--literature-aic", PARTNER, "--reader-aic", PARTNER,
            "--repository-aic", PARTNER,
            "--literature-commit", "a" * 40, "--reader-commit", "b" * 40,
            "--repository-commit", "c" * 40,
            "--credentials-dir", str(ROOT)]


class PlatformThreeAgentCliTests(unittest.TestCase):
    def test_business_checkpoint_resume_requires_resume_and_cleanup_proof(self):
        with patch.object(cli, "execute_platform_chain", side_effect=AssertionError("no Group allowed")), \
                patch("builtins.print"):
            self.assertEqual(cli.main(arguments() + ["--execute", "--partners-ready",
                "--confirm-each-stage", "--resume-business-completed"]), 2)

    def test_default_only_validates_plan_and_never_executes(self):
        with patch.object(cli, "execute_platform_chain", side_effect=AssertionError("must not execute")), \
                patch("builtins.print"):
            self.assertEqual(cli.main(arguments()), 0)

    def test_live_requires_both_readiness_and_manual_release_flags(self):
        for flags in (["--execute"], ["--execute", "--partners-ready"],
                      ["--execute", "--confirm-each-stage"]):
            with self.subTest(flags=flags), \
                    patch.object(cli, "execute_platform_chain", side_effect=AssertionError("must not execute")), \
                    patch("builtins.print"):
                self.assertEqual(cli.main(arguments() + flags), 2)

    def test_missing_pdf_dependency_stops_before_group_creation(self):
        with patch.object(cli.importlib.util, "find_spec", return_value=None), \
                patch.object(cli, "execute_platform_chain", side_effect=AssertionError("no Group allowed")), \
                patch("builtins.print"):
            self.assertEqual(cli.main(arguments() + ["--execute", "--partners-ready",
                                                      "--confirm-each-stage"]), 2)


if __name__ == "__main__":
    unittest.main()
