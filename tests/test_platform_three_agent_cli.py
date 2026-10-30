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


def discovery_arguments():
    values = arguments()
    for option in ("--literature-aic", "--reader-aic", "--repository-aic"):
        index = values.index(option)
        del values[index:index + 2]
    return values + ["--discovery-url", "https://wt.ioa.pub/discovery"]


class PlatformThreeAgentCliTests(unittest.TestCase):
    def test_default_only_validates_plan_and_never_executes(self):
        with patch.object(cli, "execute_platform_chain", side_effect=AssertionError("must not execute")), \
                patch("builtins.print"):
            self.assertEqual(cli.main(arguments()), 0)

    def test_discovery_mode_offline_validation_never_calls_network(self):
        with patch.object(cli, "execute_platform_chain", side_effect=AssertionError("must not execute")), \
                patch.object(cli, "DiscoveryHttpTransport", side_effect=AssertionError("must not construct")), \
                patch("builtins.print"):
            self.assertEqual(cli.main(discovery_arguments()), 0)

    def test_agent_selection_is_explicit_xor_discovery(self):
        cases = (
            discovery_arguments() + ["--literature-aic", PARTNER],
            [item for item in arguments() if item not in {"--literature-aic", "--reader-aic", "--repository-aic", PARTNER}],
            arguments() + ["--discovery-mtls"],
        )
        for values in cases:
            with self.subTest(values=values), \
                    patch.object(cli, "execute_platform_chain", side_effect=AssertionError("must not execute")), \
                    patch("builtins.print"):
                self.assertEqual(cli.main(values), 2)

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
