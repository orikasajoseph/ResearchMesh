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


if __name__ == "__main__":
    unittest.main()
