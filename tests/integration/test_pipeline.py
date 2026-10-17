"""Broker and database round trip. Runs in CI against service containers."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest

from fleet_telemetry.alerts import AlertEngine, AlertType
from fleet_telemetry.ingest import IngestService, IngestSettings
from fleet_telemetry.schema import Telemetry
from fleet_telemetry.store import TimescaleStore
from fleet_telemetry.webhook import HttpWebhook
from tests.unit.factories import error, telemetry

pytestmark = pytest.mark.integration


class _Recorder(BaseHTTPRequestHandler):
    received: list[dict[str, object]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.received.append(payload)
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        return


@pytest.fixture(scope="session")
def store() -> TimescaleStore:
    database = TimescaleStore(os.environ["DATABASE_URL"])
    database.ensure_schema()
    return database


@pytest.fixture
def webhook() -> tuple[str, list[dict[str, object]]]:
    _Recorder.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/alerts"
    try:
        yield url, _Recorder.received
    finally:
        server.shutdown()
        server.server_close()


def _settings(webhook_url: str, **overrides: object) -> IngestSettings:
    values: dict[str, object] = {
        "mqtt_host": os.environ.get("MQTT_HOST", "127.0.0.1"),
        "mqtt_port": int(os.environ.get("MQTT_PORT", "1883")),
        "mqtt_topic": "fleet/telemetry/#",
        "database_url": os.environ["DATABASE_URL"],
        "webhook_url": webhook_url,
        "low_battery_percent": 20.0,
        "critical_battery_percent": 10.0,
        "stale_after": timedelta(seconds=60),
        "stale_check_interval": timedelta(seconds=30),
        "alert_cooldown": timedelta(0),
        "client_id": f"it-{uuid4().hex}",
        "mqtt_timeout": 15.0,
    }
    values.update(overrides)
    return IngestSettings(**values)  # type: ignore[arg-type]


def _publish(settings: IngestSettings, sample: Telemetry) -> None:
    _publish_bytes(settings, sample.robot_id, json.dumps(sample.to_dict()).encode("utf-8"))


def _publish_bytes(settings: IngestSettings, robot_id: str, payload: bytes) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pub-{uuid4().hex}")
    try:
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        client.loop_start()
        message = client.publish(f"fleet/telemetry/{robot_id}", payload, qos=1)
        message.wait_for_publish(timeout=5)
    except (OSError, RuntimeError, ValueError):
        return
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except OSError:
            return


def _until(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError("timed out waiting for the pipeline")


def test_messages_are_stored_and_rules_reach_the_webhook(
    store: TimescaleStore,
    webhook: tuple[str, list[dict[str, object]]],
) -> None:
    webhook_url, received = webhook
    robot_id = f"it{uuid4().hex[:12]}"
    settings = _settings(webhook_url)
    service = IngestService(
        store=store,
        alerter=AlertEngine(stale_after=settings.stale_after, cooldown=settings.alert_cooldown),
        webhook=HttpWebhook(webhook_url),
        settings=settings,
    )
    healthy = telemetry(robot_id=robot_id, battery_percent=80.0, time=datetime.now(timezone.utc))
    low = telemetry(robot_id=robot_id, battery_percent=9.0, time=datetime.now(timezone.utc))
    fault = telemetry(
        robot_id=robot_id,
        battery_percent=40.0,
        time=datetime.now(timezone.utc),
        errors=(error("E_MOTOR_OVERTEMP", "critical"),),
        status="error",
    )
    service.start()
    try:
        _publish_bytes(settings, robot_id, b"not-json")
        _until(lambda: _publish(settings, healthy) or store.telemetry_for(robot_id))
        _until(
            lambda: (
                _publish(settings, low)
                or any(
                    item.alert_type is AlertType.LOW_BATTERY for item in store.alerts_for(robot_id)
                )
            )
        )
        _until(
            lambda: (
                _publish(settings, fault)
                or any(
                    item.alert_type is AlertType.ERROR_CODE for item in store.alerts_for(robot_id)
                )
            )
        )
        _until(
            lambda: {item.get("alert_type") for item in received} >= {"low_battery", "error_code"}
        )
    finally:
        service.stop()

    rows = store.telemetry_for(robot_id)
    assert any(row.battery_percent == 80.0 for row in rows)
    assert any(row.battery_percent == 9.0 for row in rows)
    kinds = {item.alert_type for item in store.alerts_for(robot_id)}
    assert AlertType.LOW_BATTERY in kinds
    assert AlertType.ERROR_CODE in kinds


def test_stale_heartbeat_fires_after_the_robot_goes_quiet(
    store: TimescaleStore,
    webhook: tuple[str, list[dict[str, object]]],
) -> None:
    webhook_url, received = webhook
    robot_id = f"it{uuid4().hex[:12]}"
    settings = _settings(
        webhook_url,
        stale_after=timedelta(seconds=1),
        stale_check_interval=timedelta(seconds=0.2),
    )
    service = IngestService(
        store=store,
        alerter=AlertEngine(stale_after=settings.stale_after, cooldown=settings.alert_cooldown),
        webhook=HttpWebhook(webhook_url),
        settings=settings,
    )
    sample = telemetry(robot_id=robot_id, time=datetime.now(timezone.utc))
    service.start()
    try:
        _until(lambda: _publish(settings, sample) or store.telemetry_for(robot_id))
        _until(
            lambda: any(
                item.alert_type is AlertType.STALE_HEARTBEAT for item in store.alerts_for(robot_id)
            ),
            timeout=8,
        )
        _until(lambda: any(item.get("alert_type") == "stale_heartbeat" for item in received))
    finally:
        service.stop()
