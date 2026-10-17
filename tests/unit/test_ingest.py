import json
from datetime import timedelta

from fleet_telemetry.alerts import Alert, AlertEngine, AlertType
from fleet_telemetry.ingest import IngestService
from fleet_telemetry.schema import Telemetry
from tests.unit.factories import error, telemetry


class MemoryStore:
    def __init__(self) -> None:
        self.telemetry: list[Telemetry] = []
        self.alerts: list[Alert] = []

    def insert_telemetry(self, sample: Telemetry) -> None:
        self.telemetry.append(sample)

    def insert_alert(self, alert: Alert) -> None:
        self.alerts.append(alert)


class ListWebhook:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class BoomWebhook:
    def send(self, alert: Alert) -> None:
        raise OSError("webhook down")


def _service(webhook: object | None = None) -> tuple[IngestService, MemoryStore]:
    store = MemoryStore()
    sender = ListWebhook() if webhook is None else webhook
    service = IngestService(
        store=store,
        alerter=AlertEngine(cooldown=timedelta(0)),
        webhook=sender,  # type: ignore[arg-type]
    )
    return service, store


def test_valid_payload_is_stored_and_forwarded() -> None:
    service, store = _service()
    sample = telemetry(battery_percent=9.0, errors=(error(),))
    service.handle_payload(json.dumps(sample.to_dict()).encode("utf-8"))
    assert store.telemetry == [sample]
    assert [item.alert_type for item in store.alerts] == [
        AlertType.LOW_BATTERY,
        AlertType.ERROR_CODE,
    ]
    assert service.webhook.alerts == store.alerts  # type: ignore[attr-defined]


def test_invalid_payload_is_discarded() -> None:
    service, store = _service()
    service.handle_payload(b"not-json")
    assert store.telemetry == []
    assert store.alerts == []


def test_webhook_failure_still_records_the_alert() -> None:
    service, store = _service(BoomWebhook())
    service.handle_payload(json.dumps(telemetry(battery_percent=5.0).to_dict()))
    assert len(store.alerts) == 1
    assert store.telemetry


def test_stale_check_uses_the_injected_clock() -> None:
    service, store = _service()
    sample = telemetry()
    service.handle_payload(json.dumps(sample.to_dict()))
    service.run_stale_check(sample.time + timedelta(seconds=10))
    assert store.alerts == []
    service.run_stale_check(sample.time + timedelta(seconds=16))
    assert [item.alert_type for item in store.alerts] == [AlertType.STALE_HEARTBEAT]
