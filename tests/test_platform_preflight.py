"""No-network unit tests for offline platform preflight helpers."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from platform_preflight import Reporter, cert_identity, load_safe_env


class PreflightUnitTests(unittest.TestCase):
    def test_reporter_accumulates_without_secrets(self):
        reporter = Reporter()
        reporter.add("test", "check", "PASS", "safe")
        self.assertEqual(reporter.checks[0].state, "PASS")

    def test_missing_dotenv_is_empty(self):
        with TemporaryDirectory() as d:
            self.assertEqual(load_safe_env(Path(d) / "missing.env"), {})

    def test_missing_cert_raises(self):
        with TemporaryDirectory() as d:
            with self.assertRaises((OSError, FileNotFoundError)):
                cert_identity(Path(d) / "missing.pem")


if __name__ == "__main__":
    unittest.main()
