"""Offline only: fake transport and certificate metadata; no keys or network."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from platform_mq_check import (
    ROOT, CertificateInfo, CheckError, certificate_context,
    check_connection, classify_error, validate_config,
)
import run_mq_check

AIC = "1.2.3.TEST.LEADER"


class ConfigTests(unittest.TestCase):
    def test_control_character_aic_rejected(self):
        with self.assertRaisesRegex(CheckError, "INVALID_LEADER_AIC"):
            validate_config("\x16", Path(tempfile.gettempdir()), 20)

    def test_placeholder_aic_rejected(self):
        with self.assertRaisesRegex(CheckError, "INVALID_LEADER_AIC"):
            validate_config("{AIC}", Path(tempfile.gettempdir()), 20)

    def test_empty_aic_rejected(self):
        with self.assertRaisesRegex(CheckError, "INVALID_LEADER_AIC"):
            validate_config("", Path(tempfile.gettempdir()), 20)

    def test_credentials_in_repository_rejected(self):
        with self.assertRaisesRegex(CheckError, "OUTSIDE_REPOSITORY"):
            validate_config(AIC, ROOT / "outputs" / "keys", 20)

    def test_invalid_deadlines_rejected(self):
        for timeout in (0, -1, 61, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(CheckError, "INVALID_TIMEOUT"):
                validate_config(AIC, Path(tempfile.gettempdir()), timeout)

    def test_unknown_error_never_echoes_raw_message(self):
        self.assertEqual(classify_error(RuntimeError("secret-value")), "CONNECTION_CHECK_FAILED")

    def test_platform_credential_paths_are_ignored(self):
        paths = ["leader-key.pem", "leader-cert.pem", "trust-bundle.pem", "leader-eab.json",
                 "eab.json", ".acps-cli/tokens/registry-user.json", "keyfiles/accounts/account.key",
                 "credentials/eab.json", ".venv-mq-check/Scripts/python.exe"]
        result = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=ROOT,
                                input=("\0".join(paths) + "\0").encode(), capture_output=True, check=True)
        self.assertEqual(set(result.stdout.decode().strip("\0").split("\0")), set(paths))


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("leader-cert.pem", "leader-key.pem", "trust-bundle.pem"):
            (self.root / name).touch()
        now = datetime.now(timezone.utc)
        self.info = CertificateInfo((AIC,), now - timedelta(days=1), now + timedelta(days=1), True)

    def context(self, info):
        with patch("platform_mq_check.certificate_info", return_value=info), patch(
                "platform_mq_check.ssl.create_default_context") as factory:
            result = certificate_context(self.root, AIC)
        return result, factory

    def test_valid_certificate_requires_ca_and_matching_key(self):
        result, factory = self.context(self.info)
        factory.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
        result.load_verify_locations.assert_called_once_with(cafile=str(self.root / "trust-bundle.pem"))
        result.load_cert_chain.assert_called_once_with(str(self.root / "leader-cert.pem"), str(self.root / "leader-key.pem"))
        self.assertNotIn("verify_mode", result.__dict__)

    def test_missing_file_stops_before_crypto(self):
        (self.root / "leader-key.pem").unlink()
        with patch("platform_mq_check.certificate_info") as read, self.assertRaisesRegex(CheckError, "FILE_MISSING"):
            certificate_context(self.root, AIC)
        read.assert_not_called()

    def test_wrong_identity_rejected(self):
        with self.assertRaisesRegex(CheckError, "AIC_MISMATCH"):
            self.context(replace(self.info, common_names=("1.2.3.OTHER",)))

    def test_multiple_common_names_rejected(self):
        with self.assertRaisesRegex(CheckError, "AIC_MISMATCH"):
            self.context(replace(self.info, common_names=(AIC, AIC)))

    def test_expired_certificate_rejected(self):
        with self.assertRaisesRegex(CheckError, "NOT_CURRENTLY_VALID"):
            self.context(replace(self.info, not_after=datetime.now(timezone.utc) - timedelta(hours=1)))

    def test_future_certificate_rejected(self):
        with self.assertRaisesRegex(CheckError, "NOT_CURRENTLY_VALID"):
            self.context(replace(self.info, not_before=datetime.now(timezone.utc) + timedelta(hours=1)))

    def test_server_only_certificate_rejected(self):
        with self.assertRaisesRegex(CheckError, "CLIENT_AUTH_REQUIRED"):
            self.context(replace(self.info, client_auth=False))

    def test_local_key_mismatch_error_not_suppressed(self):
        with patch("platform_mq_check.certificate_info", return_value=self.info), patch(
                "platform_mq_check.ssl.create_default_context") as factory:
            factory.return_value.load_cert_chain.side_effect = ssl.SSLError("KEY_VALUES_MISMATCH")
            with self.assertRaises(ssl.SSLError):
                certificate_context(self.root, AIC)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def mock_check(self, *, connect_error=None, channel_error=None, close_error=None,
                         remains_open=False, block=False):
        connection = MagicMock()
        connection.is_closed = True

        async def connect(**kwargs):
            if connect_error:
                raise connect_error
            connection.is_closed = False

        async def channel():
            if block:
                await asyncio.Event().wait()
            if channel_error:
                raise channel_error
            return connection.channel_object

        async def close():
            if close_error:
                raise close_error
            connection.is_closed = not remains_open

        connection.connect = AsyncMock(side_effect=connect)
        connection.channel = AsyncMock(side_effect=channel)
        connection.close = AsyncMock(side_effect=close)
        with patch("platform_mq_check.certificate_context"), patch(
                "platform_mq_check.aio_pika.Connection", return_value=connection) as factory, patch(
                "platform_mq_check.aio_pika.connect_robust") as robust:
            record = await check_connection(aic=AIC, credentials_dir=Path(tempfile.gettempdir()),
                                            execute=True, timeout=0.03 if block else 20)
        robust.assert_not_called()
        connection.connect.assert_awaited_once()
        connection.close.assert_awaited_once()
        return record, connection, factory

    async def test_offline_never_constructs_network_connection(self):
        with patch("platform_mq_check.certificate_context"), patch("platform_mq_check.aio_pika.Connection") as factory:
            record = await check_connection(aic=AIC, credentials_dir=Path(tempfile.gettempdir()))
        factory.assert_not_called()
        self.assertTrue(record["passed"])
        self.assertFalse(record["networkAttempted"])
        self.assertFalse(record["amqpConnected"])
        self.assertFalse(record["protocolCompleted"])

    async def test_certificate_failure_prevents_network(self):
        with patch("platform_mq_check.certificate_context", side_effect=CheckError("CERTIFICATE_AIC_MISMATCH")), patch(
                "platform_mq_check.aio_pika.Connection") as factory:
            record = await check_connection(aic=AIC, credentials_dir=Path(tempfile.gettempdir()), execute=True)
        factory.assert_not_called()
        self.assertFalse(record["passed"])

    async def test_success_uses_real_sdk_channel_only(self):
        record, connection, factory = await self.mock_check()
        self.assertTrue(record["passed"])
        self.assertTrue(record["leaderChannelOpened"])
        self.assertTrue(record["connectionClosed"])
        self.assertIn("auth=external", str(factory.call_args.args[0]))
        self.assertIn("wt.ioa.pub:5671/acps", str(factory.call_args.args[0]))
        connection.channel.assert_awaited_once()
        connection.channel_object.declare_queue.assert_not_called()
        connection.channel_object.declare_exchange.assert_not_called()
        connection.channel_object.default_exchange.publish.assert_not_called()
        self.assertEqual((record["groupsCreated"], record["tasksSent"], record["llmCalls"]), (0, 0, 0))
        self.assertFalse(record["protocolCompleted"])
        self.assertFalse(record["businessReady"])

    async def test_dns_failure(self):
        record, _, _ = await self.mock_check(connect_error=socket.gaierror())
        self.assertEqual(record["error"]["code"], "DNS_LOOKUP_FAILED")

    async def test_connect_timeout_no_retry(self):
        record, _, _ = await self.mock_check(connect_error=TimeoutError())
        self.assertEqual(record["error"]["code"], "CONNECTION_TIMEOUT")

    async def test_real_deadline_includes_channel_creation(self):
        record, _, _ = await self.mock_check(block=True)
        self.assertEqual(record["error"]["code"], "CONNECTION_TIMEOUT")
        self.assertFalse(record["passed"])

    async def test_tls_failure(self):
        record, _, _ = await self.mock_check(connect_error=ssl.SSLCertVerificationError())
        self.assertEqual(record["error"]["code"], "SERVER_CERTIFICATE_VERIFICATION_FAILED")

    async def test_channel_failure_keeps_connect_evidence(self):
        record, _, _ = await self.mock_check(channel_error=RuntimeError("secret-value"))
        self.assertTrue(record["amqpConnected"])
        self.assertFalse(record["passed"])
        self.assertNotIn("secret-value", str(record))

    async def test_cleanup_error_is_failure(self):
        record, _, _ = await self.mock_check(close_error=TimeoutError())
        self.assertFalse(record["passed"])
        self.assertFalse(record["connectionClosed"])

    async def test_open_connection_after_close_is_failure(self):
        record, _, _ = await self.mock_check(remains_open=True)
        self.assertFalse(record["passed"])
        self.assertEqual(record["cleanupError"], "CONNECTION_REMAINS_OPEN")

    async def test_missing_crypto_is_reported_without_network(self):
        with patch("platform_mq_check.certificate_context", side_effect=ModuleNotFoundError()), patch(
                "platform_mq_check.aio_pika.Connection") as factory:
            record = await check_connection(aic=AIC, credentials_dir=Path(tempfile.gettempdir()), execute=True)
        self.assertEqual(record["error"]["code"], "OPTIONAL_DEPENDENCY_MISSING")
        factory.assert_not_called()


class CliTests(unittest.TestCase):
    def test_execute_opt_in_and_output_record(self):
        async def fake_check(**kwargs):
            self.assertFalse(kwargs["execute"])
            return {"passed": True, "error": None, "networkAttempted": False}

        with tempfile.TemporaryDirectory() as directory, patch.object(run_mq_check, "check_connection", side_effect=fake_check), patch("builtins.print"):
            status = run_mq_check.main(["--aic", AIC, "--credentials-dir", directory,
                                        "--output-dir", str(Path(directory) / "records")])
            self.assertEqual(status, 0)
            self.assertEqual(len(list((Path(directory) / "records").glob("mq-check-*.json"))), 1)

    def test_failure_exit_status(self):
        async def fake_check(**kwargs):
            return {"passed": False, "error": {"code": "CERTIFICATE_AIC_MISMATCH"}}

        with tempfile.TemporaryDirectory() as directory, patch.object(run_mq_check, "check_connection", side_effect=fake_check), patch("builtins.print"):
            self.assertEqual(run_mq_check.main(["--aic", AIC, "--credentials-dir", directory,
                                               "--output-dir", directory]), 1)


if __name__ == "__main__":
    unittest.main()
