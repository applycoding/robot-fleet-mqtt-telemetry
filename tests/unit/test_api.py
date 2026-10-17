from datetime import datetime, timedelta, timezone

from fleet_telemetry.alerts import Alert, AlertType, Severity
from fleet_telemetry.api import alert_model, robot_state
from tests.unit.factories import telemetry


def test_robot_state_marks_a_fresh_sample_online() -> None:
    sample = telemetry()
    state = robot_state(sample, sample.time + timedelta(seconds=5), timedelta(seconds=15))
    assert state.online is True
    assert state.pose.x == 1.0
    assert state.battery.percent == 80.0
    assert state.robot_id == "amr-01"


def test_robot_state_marks_an_old_sample_offline() -> None:
    sample = telemetry()
    state = robot_state(sample, sample.time + timedelta(seconds=30), timedelta(seconds=15))
    assert state.online is False


def test_alert_model_uses_wire_values() -> None:
    alert = Alert(
        robot_id="amr-02",
        alert_type=AlertType.LOW_BATTERY,
        severity=Severity.WARNING,
        message="amr-02 battery at 18.0%",
        time=datetime(2026, 10, 17, tzinfo=timezone.utc),
        details={"battery_percent": 18.0},
    )
    model = alert_model(alert)
    assert model.alert_type == "low_battery"
    assert model.severity == "warning"
    assert model.details["battery_percent"] == 18.0
