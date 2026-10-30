from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aip.amp_kafka_heartbeat import (
    AmpKafkaSettings,
    KafkaBackedHeartbeatEmitter,
    KafkaTlsHeartbeatPublisher,
)


AIC = "1.2.156.3088.1.BUPT.RECKYJ.TEST.HEARTBEAT.0001"


class FakePublisher:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.payloads: list[bytes] = []

    async def start(self) -> None:
        self.started += 1

    async def publish(self, payload: bytes) -> None:
        self.payloads.append(payload)

    async def stop(self) -> None:
        self.stopped += 1


class FailingPublisher(FakePublisher):
    async def publish(self, payload: bytes) -> None:
        raise RuntimeError("delivery failed")


class FakeKafkaProducer:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent: list[tuple[str, bytes, bytes]] = []

    async def start(self) -> None:
        self.started = True

    async def send_and_wait(self, topic: str, *, value: bytes, key: bytes):
        self.sent.append((topic, value, key))
        return type("Metadata", (), {"partition": 0, "offset": 7})()

    async def stop(self) -> None:
        self.stopped = True


class AmpKafkaHeartbeatTests(unittest.TestCase):
    def test_settings_centralize_broker_topic_and_per_agent_tls_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            credential_dir = Path(directory)
            settings = AmpKafkaSettings.from_environment(credential_dir)
        self.assertEqual(settings.bootstrap_servers, "wt.ioa.pub:19092")
        self.assertEqual(settings.topic, "amp.heartbeat")
        self.assertEqual(settings.ca_file, credential_dir / "trust-bundle.pem")
        self.assertEqual(settings.cert_file, credential_dir / "agent-cert.pem")
        self.assertEqual(settings.key_file, credential_dir / "agent-key.pem")

    def test_local_record_is_preserved_and_exact_bytes_are_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.jsonl"
            publisher = FakePublisher()
            emitter = KafkaBackedHeartbeatEmitter(
                path, aic=AIC, credential_dir=Path(directory), publisher=publisher,
            )
            self.assertTrue(asyncio.run(emitter.emit_once()))
            local = path.read_bytes().splitlines()
        self.assertEqual(publisher.started, 1)
        self.assertEqual(publisher.payloads, [local[-1]])
        record = json.loads(local[-1])
        self.assertEqual(record["aic"], AIC)
        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["log_type"], "heartbeat")

    def test_publish_failure_keeps_local_record_and_stops_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.jsonl"
            publisher = FailingPublisher()
            emitter = KafkaBackedHeartbeatEmitter(
                path, aic=AIC, credential_dir=Path(directory), publisher=publisher,
            )
            self.assertFalse(asyncio.run(emitter.emit_once()))
            record = json.loads(path.read_bytes().splitlines()[-1])
        self.assertEqual(record["aic"], AIC)
        self.assertEqual(publisher.stopped, 1)

    def test_producer_requires_tls_all_acks_and_aic_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            settings = AmpKafkaSettings.from_environment(Path(directory))
            made: list[FakeKafkaProducer] = []

            def factory(**kwargs: object) -> FakeKafkaProducer:
                producer = FakeKafkaProducer(**kwargs)
                made.append(producer)
                return producer

            async def exercise() -> None:
                with patch.object(AmpKafkaSettings, "ssl_context", return_value=object()):
                    publisher = KafkaTlsHeartbeatPublisher(
                        settings=settings, aic=AIC, producer_factory=factory,
                    )
                    await publisher.start()
                    await publisher.publish(json.dumps({"aic": AIC}).encode())
                    await publisher.stop()

            asyncio.run(exercise())
        producer = made[0]
        self.assertEqual(producer.kwargs["security_protocol"], "SSL")
        self.assertEqual(producer.kwargs["acks"], "all")
        self.assertEqual(producer.sent[0][0], "amp.heartbeat")
        self.assertEqual(producer.sent[0][2], AIC.encode())
        self.assertTrue(producer.stopped)
