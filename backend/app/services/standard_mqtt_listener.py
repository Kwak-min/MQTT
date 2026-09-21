"""Receive Standard MQTT board telemetry and forward it to TelemetryService."""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from app.models.packet import PublishPacket

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - optional runtime dependency
    mqtt = None

logger = logging.getLogger(__name__)

STANDARD_TOPIC = "environmental/standard"
# Board 2가 QoS 1 발행이 끝난 뒤 실측 RTT/retry를 담아 보내는 메트릭 토픽.
# Gingerbread의 topic_id=2 텔레메트리 패킷에 대응합니다.
STANDARD_METRICS_TOPIC = "environmental/standard/metrics"


class StandardMqttListener:
    """Subscribe to Board 2 telemetry and reuse the normal env telemetry path."""

    def __init__(
        self,
        host: str,
        port: int,
        on_telemetry: Callable[[PublishPacket], None],
        on_metrics: Optional[Callable[[PublishPacket], None]] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_telemetry = on_telemetry  # 센서 데이터 (topic_id=1)
        self._on_metrics = on_metrics      # 실측 RTT/retry 메트릭 (topic_id=2)
        self._client: Optional[object] = None

    def start(self) -> None:
        if mqtt is None:
            logger.warning("[Standard MQTT] paho-mqtt is unavailable; listener disabled")
            return

        client = mqtt.Client(client_id="gingerbread-gateway-standard", clean_session=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        try:
            client.connect_async(self._host, self._port, keepalive=60)
            client.loop_start()
            logger.info(
                "[Standard MQTT] listener started: %s:%d topic=%s",
                self._host,
                self._port,
                STANDARD_TOPIC,
            )
        except Exception as exc:
            logger.error("[Standard MQTT] connection setup failed: %s", exc)
            self._client = None

    def stop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception as exc:
            logger.warning("[Standard MQTT] shutdown failed: %s", exc)
        finally:
            self._client = None

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            client.subscribe(STANDARD_TOPIC, qos=1)
            client.subscribe(STANDARD_METRICS_TOPIC, qos=1)
            logger.info(
                "[Standard MQTT] subscribed: %s, %s",
                STANDARD_TOPIC, STANDARD_METRICS_TOPIC,
            )
        else:
            logger.warning("[Standard MQTT] broker connection failed: rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc) -> None:
        if rc != 0:
            logger.warning("[Standard MQTT] broker disconnected: rc=%s", rc)

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload_raw = message.payload.decode("utf-8").strip()
            payload = json.loads(payload_raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("[Standard MQTT] invalid telemetry payload: %s", exc)
            return

        # 토픽으로 경로를 나눕니다: 메트릭 토픽은 topic_id=2 (전력 추정), 그 외는 topic_id=1.
        is_metrics = message.topic == STANDARD_METRICS_TOPIC
        packet = PublishPacket(
            addr=(self._host, self._port),
            msg_id=0,
            qos=int(message.qos),
            topic_id=2 if is_metrics else 1,
            payload_raw=payload_raw,
            payload=payload,
        )
        if is_metrics:
            if self._on_metrics is not None:
                self._on_metrics(packet)
            else:
                logger.debug("[Standard MQTT] metrics message ignored (no on_metrics callback)")
            return
        self._on_telemetry(packet)
