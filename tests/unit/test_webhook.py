import json
from datetime import datetime, timezone

from fleet_telemetry.alerts import Alert, AlertType, Severity
from fleet_telemetry.webhook import HttpWebhook, NullWebhook


def test_http_webhook_posts_alert_json(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> object:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

    def _urlopen(request: object, timeout: float) -> _Response:
        captured["url"] = request.full_url  # type: ignore[attr-defined]
        captured["body"] = json.loads(request.data.decode())  # type: ignore[attr-defined]
        captured["content_type"] = request.get_header("Content-type")  # type: ignore[attr-defined]
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("fleet_telemetry.webhook.urllib.request.urlopen", _urlopen)
    alert = Alert(
        robot_id="amr-03",
        alert_type=AlertType.ERROR_CODE,
        severity=Severity.WARNING,
        message="amr-03 reported E_LIDAR_TIMEOUT: front lidar scan is stale",
        time=datetime(2026, 10, 17, tzinfo=timezone.utc),
        details={"code": "E_LIDAR_TIMEOUT"},
    )
    HttpWebhook("http://hook.test/alerts", timeout=2).send(alert)
    assert captured["url"] == "http://hook.test/alerts"
    assert captured["timeout"] == 2
    assert captured["content_type"] == "application/json"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["alert_type"] == "error_code"
    assert body["robot_id"] == "amr-03"


def test_null_webhook_drops_the_alert() -> None:
    alert = Alert(
        robot_id="amr-01",
        alert_type=AlertType.STALE_HEARTBEAT,
        severity=Severity.WARNING,
        message="stale",
        time=datetime(2026, 10, 17, tzinfo=timezone.utc),
        details={},
    )
    NullWebhook().send(alert)
