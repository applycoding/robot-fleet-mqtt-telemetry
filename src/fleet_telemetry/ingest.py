"""Subscribe to fleet telemetry and persist it, then emit alert rules."""

from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import paho.mqtt.client as mqtt

from fleet_telemetry.alerts import Alert, AlertEngine
from fleet_telemetry.schema import TOPIC_PREFIX, PayloadError, Telemetry, parse_telemetry
from fleet_telemetry.store import TimescaleStore
from fleet_telemetry.webhook import NullWebhook, webhook_from_url

logger = logging.getLogger(__name__)


class TelemetrySink(Protocol):
    def insert_telemetry(self, telemetry: Telemetry) -> None: ...

    def insert_alert(self, alert: Alert) -> None: ...


class AlertSender(Protocol):
    def send(self, alert: Alert) -> None: ...


@dataclass(frozen=True)
class IngestSettings:
    mqtt_host: str
    mqtt_port: int
    mqtt_topic: str
    database_url: str
    webhook_url: str
    low_battery_percent: float
    critical_battery_percent: float
    stale_after: timedelta
    stale_check_interval: timedelta
    alert_cooldown: timedelta
    client_id: str
    mqtt_timeout: float

    @classmethod
    def from_env(cls) -> IngestSettings:
        return cls(
            mqtt_host=os.environ.get("MQTT_HOST", "localhost"),
            mqtt_port=_env_int("MQTT_PORT", 1883),
            mqtt_topic=os.environ.get("MQTT_TOPIC", f"{TOPIC_PREFIX}/#"),
            database_url=os.environ["DATABASE_URL"],
            webhook_url=os.environ.get("WEBHOOK_URL", ""),
            low_battery_percent=_env_float("LOW_BATTERY_PERCENT", 20.0),
            critical_battery_percent=_env_float("CRITICAL_BATTERY_PERCENT", 10.0),
            stale_after=timedelta(seconds=_env_float("STALE_AFTER_SECONDS", 15.0)),
            stale_check_interval=timedelta(seconds=_env_float("STALE_CHECK_SECONDS", 2.0)),
            alert_cooldown=timedelta(seconds=_env_float("ALERT_COOLDOWN_SECONDS", 60.0)),
            client_id=os.environ.get("MQTT_CLIENT_ID", f"fleet-ingest-{os.getpid()}"),
            mqtt_timeout=_env_float("MQTT_TIMEOUT_SECONDS", 15.0),
        )


class IngestService:
    def __init__(
        self,
        store: TelemetrySink,
        alerter: AlertEngine,
        webhook: AlertSender,
        settings: IngestSettings | None = None,
    ) -> None:
        self.store = store
        self.alerter = alerter
        self.webhook = webhook
        self.settings = settings
        self._client: mqtt.Client | None = None
        self._subscribed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def handle_payload(self, raw: bytes | str) -> None:
        try:
            telemetry = self._parse(raw)
        except PayloadError:
            logger.warning("discarding invalid telemetry payload")
            return
        self.store.insert_telemetry(telemetry)
        for alert in self.alerter.evaluate(telemetry):
            self._emit(alert)

    def run_stale_check(self, now: datetime | None = None) -> None:
        moment = now if now is not None else datetime.now(timezone.utc)
        for alert in self.alerter.check_stale(moment):
            self._emit(alert)

    def start(self) -> None:
        if self.settings is None:
            raise RuntimeError("settings are required to start the subscriber")
        if not isinstance(self.store, TimescaleStore):
            raise RuntimeError("start requires a TimescaleStore")
        self.store.ensure_schema()
        self._start_mqtt(self.settings)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._stale_loop,
            name="stale-heartbeat",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "ingesting %s from %s:%s",
            self.settings.mqtt_topic,
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    def _parse(self, raw: bytes | str) -> Telemetry:
        return parse_telemetry(raw)

    def _emit(self, alert: Alert) -> None:
        self.store.insert_alert(alert)
        try:
            self.webhook.send(alert)
        except Exception:
            logger.exception(
                "webhook delivery failed for %s on %s",
                alert.alert_type.value,
                alert.robot_id,
            )
        else:
            logger.info("alert %s %s: %s", alert.severity.value, alert.robot_id, alert.message)

    def _start_mqtt(self, settings: IngestSettings) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.client_id)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._subscribed.clear()
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client
        if not self._subscribed.wait(settings.mqtt_timeout):
            raise TimeoutError(f"timed out subscribing to {settings.mqtt_topic}")

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: object,
        reason_code: mqtt.ReasonCode,
        properties: object,
    ) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            logger.error("mqtt connect failed: %s", reason_code)
            return
        if self.settings is None:
            return
        client.subscribe(self.settings.mqtt_topic, qos=1)
        self._subscribed.set()

    def _on_message(self, client: mqtt.Client, userdata: object, message: mqtt.MQTTMessage) -> None:
        del client, userdata
        try:
            self.handle_payload(message.payload)
        except Exception:
            logger.exception("failed to ingest a telemetry message")

    def _stale_loop(self) -> None:
        if self.settings is None:
            return
        interval = self.settings.stale_check_interval.total_seconds()
        while not self._stop.wait(interval):
            try:
                self.run_stale_check()
            except Exception:
                logger.exception("stale check failed")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = IngestSettings.from_env()
    service = IngestService(
        store=TimescaleStore(settings.database_url),
        alerter=AlertEngine(
            low_percent=settings.low_battery_percent,
            critical_percent=settings.critical_battery_percent,
            stale_after=settings.stale_after,
            cooldown=settings.alert_cooldown,
        ),
        webhook=webhook_from_url(settings.webhook_url),
        settings=settings,
    )
    if isinstance(service.webhook, NullWebhook):
        logger.info("WEBHOOK_URL is empty; alerts are stored and not delivered")
    service.start()
    stop = threading.Event()

    def _handle(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    stop.wait()
    service.stop()
