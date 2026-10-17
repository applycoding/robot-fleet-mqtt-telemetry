from datetime import datetime, timedelta, timezone

from fleet_telemetry.alerts import AlertEngine, AlertType, Severity
from tests.unit.factories import error, telemetry

T0 = datetime(2026, 10, 17, tzinfo=timezone.utc)


def engine(**overrides: object) -> AlertEngine:
    values: dict[str, object] = {
        "low_percent": 20.0,
        "critical_percent": 10.0,
        "stale_after": timedelta(seconds=15),
        "cooldown": timedelta(seconds=60),
    }
    values.update(overrides)
    return AlertEngine(**values)  # type: ignore[arg-type]


def test_healthy_battery_is_quiet() -> None:
    assert engine().evaluate(telemetry()) == []


def test_crossing_low_then_critical_alerts_once_each() -> None:
    alerter = engine(cooldown=timedelta(0))
    low = alerter.evaluate(telemetry(battery_percent=20.0, time=T0))
    repeat = alerter.evaluate(telemetry(battery_percent=15.0, time=T0 + timedelta(seconds=1)))
    critical = alerter.evaluate(telemetry(battery_percent=10.0, time=T0 + timedelta(seconds=2)))
    assert [item.severity for item in low] == [Severity.WARNING]
    assert low[0].alert_type is AlertType.LOW_BATTERY
    assert repeat == []
    assert [item.severity for item in critical] == [Severity.CRITICAL]


def test_charging_robot_does_not_raise_low_battery() -> None:
    alerter = engine()
    assert alerter.evaluate(telemetry(battery_percent=5.0, charging=True)) == []
    dropped = alerter.evaluate(telemetry(battery_percent=5.0, charging=False, time=T0))
    assert [item.severity for item in dropped] == [Severity.CRITICAL]


def test_cooldown_suppresses_a_flap_until_it_expires() -> None:
    alerter = engine(cooldown=timedelta(seconds=60))
    assert alerter.evaluate(telemetry(battery_percent=19.0, time=T0))
    alerter.evaluate(telemetry(battery_percent=80.0, time=T0 + timedelta(seconds=10)))
    suppressed = alerter.evaluate(telemetry(battery_percent=19.0, time=T0 + timedelta(seconds=20)))
    alerter.evaluate(telemetry(battery_percent=80.0, time=T0 + timedelta(seconds=30)))
    again = alerter.evaluate(telemetry(battery_percent=19.0, time=T0 + timedelta(seconds=80)))
    assert suppressed == []
    assert len(again) == 1


def test_new_error_codes_alert_until_they_clear() -> None:
    alerter = engine(cooldown=timedelta(0))
    first = alerter.evaluate(telemetry(errors=(error("E_LIDAR_TIMEOUT"),), time=T0))
    held = alerter.evaluate(
        telemetry(errors=(error("E_LIDAR_TIMEOUT"),), time=T0 + timedelta(seconds=1))
    )
    extra = alerter.evaluate(
        telemetry(
            errors=(error("E_LIDAR_TIMEOUT"), error("E_MOTOR_OVERTEMP", "critical")),
            time=T0 + timedelta(seconds=2),
        )
    )
    alerter.evaluate(telemetry(errors=(), time=T0 + timedelta(seconds=3)))
    returned = alerter.evaluate(
        telemetry(errors=(error("E_LIDAR_TIMEOUT"),), time=T0 + timedelta(seconds=4))
    )
    assert [item.details["code"] for item in first] == ["E_LIDAR_TIMEOUT"]
    assert held == []
    assert [item.details["code"] for item in extra] == ["E_MOTOR_OVERTEMP"]
    assert extra[0].severity is Severity.CRITICAL
    assert [item.details["code"] for item in returned] == ["E_LIDAR_TIMEOUT"]


def test_stale_heartbeat_alerts_once_until_a_sample_returns() -> None:
    alerter = engine(stale_after=timedelta(seconds=15))
    assert alerter.check_stale(T0) == []
    alerter.evaluate(telemetry(time=T0))
    assert alerter.check_stale(T0 + timedelta(seconds=15)) == []
    stale = alerter.check_stale(T0 + timedelta(seconds=16))
    again = alerter.check_stale(T0 + timedelta(seconds=40))
    alerter.evaluate(telemetry(time=T0 + timedelta(seconds=41)))
    rearmed = alerter.check_stale(T0 + timedelta(seconds=57))
    assert [item.alert_type for item in stale] == [AlertType.STALE_HEARTBEAT]
    assert stale[0].details["age_seconds"] == 16
    assert again == []
    assert len(rearmed) == 1
