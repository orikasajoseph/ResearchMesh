"""Kafka AMP heartbeat publication while retaining the local NDJSON audit."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from acps_sdk.amp import HeartbeatEmitter


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmpKafkaSettings:
    """Centralized configuration for the platform AMP Kafka listener."""

    bootstrap_servers: str
    topic: str
    @classmethod
    def from_environment(cls) -> "AmpKafkaSettings":
        return cls(
            bootstrap_servers=os.getenv(
                "AMP_KAFKA_BOOTSTRAP_SERVERS", "wt.ioa.pub:19092"
            ).strip(),
            topic=os.getenv("AMP_KAFKA_TOPIC", "amp.heartbeat").strip(),
        )


class KafkaHeartbeatPublisher:
    """A single platform Kafka producer with delivery acknowledgement logging."""

    def __init__(self, *, settings: AmpKafkaSettings, aic: str,
                 producer_factory: Callable[..., Any] | None = None) -> None:
        self.settings = settings
        self.aic = aic
        self._producer_factory = producer_factory
        self._producer: Any | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        factory = self._producer_factory
        if factory is None:
            try:
                from aiokafka import AIOKafkaProducer
            except ImportError as exc:
                raise RuntimeError("aiokafka is required for AMP Kafka publication") from exc
            factory = AIOKafkaProducer
        self._producer = factory(
            bootstrap_servers=self.settings.bootstrap_servers,
            security_protocol="PLAINTEXT",
            acks="all",
            client_id=f"researchmesh-amp-{self.aic}",
        )
        await self._producer.start()
        LOGGER.info("AMP_KAFKA_CONNECTED broker=%s topic=%s aic=%s",
                    self.settings.bootstrap_servers, self.settings.topic, self.aic)

    async def publish(self, payload: bytes) -> Any:
        if self._producer is None:
            raise RuntimeError("AMP Kafka producer is not started")
        record = json.loads(payload.decode("utf-8"))
        if record.get("aic") != self.aic:
            raise ValueError("AMP heartbeat AIC does not match the configured producer identity")
        metadata = await self._producer.send_and_wait(
            self.settings.topic, value=payload, key=self.aic.encode("utf-8")
        )
        LOGGER.info("AMP_KAFKA_ACK topic=%s aic=%s partition=%s offset=%s",
                    self.settings.topic, self.aic,
                    getattr(metadata, "partition", "unknown"),
                    getattr(metadata, "offset", "unknown"))
        return metadata

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None


class KafkaBackedHeartbeatEmitter:
    """Writes the SDK heartbeat unchanged locally, then publishes that exact line."""

    def __init__(self, log_file: Path, *, aic: str,
                 publisher: KafkaHeartbeatPublisher | None = None) -> None:
        self._log_file = log_file
        self._local = HeartbeatEmitter(log_file, aic=aic)
        self._publisher = publisher or KafkaHeartbeatPublisher(
            settings=AmpKafkaSettings.from_environment(), aic=aic
        )

    async def run_periodic(self, interval_seconds: float) -> None:
        try:
            while True:
                await self.emit_once()
                await asyncio.sleep(interval_seconds)
        finally:
            await self._publisher.stop()

    async def emit_once(self) -> bool:
        """Emit locally first, then publish that exact serialized record."""
        await self._local.emit()
        try:
            await self._publisher.start()
            payload = await asyncio.to_thread(self._latest_local_payload)
            await self._publisher.publish(payload)
            return True
        except Exception as exc:
            LOGGER.warning("AMP_KAFKA_SEND_FAILED type=%s", type(exc).__name__)
            await self._publisher.stop()
            return False

    def _latest_local_payload(self) -> bytes:
        with self._log_file.open("rb") as stream:
            lines = stream.read().splitlines()
        if not lines:
            raise RuntimeError("local AMP heartbeat audit record is missing")
        return lines[-1]
