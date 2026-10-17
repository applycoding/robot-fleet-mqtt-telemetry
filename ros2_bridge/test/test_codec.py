import math
from datetime import datetime, timezone

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from sensor_msgs.msg import BatteryState

from ros2_bridge.codec import (
    battery_percent,
    battery_voltage,
    build_payload,
    errors_from_diagnostics,
    is_charging,
    yaw_from_quaternion,
)


def test_yaw_of_identity_and_quarter_turn() -> None:
    assert yaw_from_quaternion(0.0, 0.0, 0.0, 1.0) == 0.0
    half = math.sin(math.pi / 4.0)
    assert math.isclose(yaw_from_quaternion(0.0, 0.0, half, half), math.pi / 2.0, abs_tol=1e-9)
    assert yaw_from_quaternion(0.0, 0.0, 0.0, 0.0) == 0.0


def test_battery_percent_accepts_fraction_or_percent() -> None:
    assert battery_percent(0.42) == 42.0
    assert battery_percent(1.0) == 100.0
    assert battery_percent(55.0) == 55.0
    assert battery_percent(140.0) == 100.0
    assert battery_percent(-1.0) is None
    assert battery_percent(float("nan")) is None


def test_voltage_and_charging_flag() -> None:
    assert battery_voltage(24.2) == 24.2
    assert battery_voltage(float("nan")) == 0.0
    assert is_charging(BatteryState.POWER_SUPPLY_STATUS_CHARGING)
    assert not is_charging(BatteryState.POWER_SUPPLY_STATUS_DISCHARGING)


def test_diagnostics_skip_ok_and_map_severity() -> None:
    message = DiagnosticArray()
    ok = DiagnosticStatus()
    ok.name = "imu"
    ok.level = DiagnosticStatus.OK
    ok.message = "fine"
    warn = DiagnosticStatus()
    warn.name = "front_lidar"
    warn.level = DiagnosticStatus.WARN
    warn.message = "stale scan"
    error = DiagnosticStatus()
    error.name = "motor"
    error.level = DiagnosticStatus.ERROR
    error.message = "overtemp"
    message.status = [ok, warn, error]
    errors = errors_from_diagnostics(message)
    assert errors == [
        {"code": "front_lidar", "message": "stale scan", "severity": "warning"},
        {"code": "motor", "message": "overtemp", "severity": "critical"},
    ]


def test_build_payload_matches_the_fleet_wire_shape() -> None:
    payload = build_payload(
        robot_id="amr-01",
        when=datetime(2026, 10, 17, tzinfo=timezone.utc),
        x=1.5,
        y=-2.0,
        yaw=0.25,
        frame_id="map",
        battery_percent_value=42.0,
        battery_voltage_value=24.1,
        charging=False,
        status="navigating",
        errors=[],
    )
    assert payload["robot_id"] == "amr-01"
    assert payload["time"] == "2026-10-17T00:00:00Z"
    assert payload["pose"] == {"x": 1.5, "y": -2.0, "yaw": 0.25, "frame_id": "map"}
    assert payload["battery"]["percent"] == 42.0
    assert payload["status"] == "navigating"
    assert payload["errors"] == []
