"""Builders for unit tests."""

from datetime import datetime, timezone

from fleet_telemetry.schema import RobotError, Telemetry


def telemetry(**overrides: object) -> Telemetry:
    values: dict[str, object] = {
        "robot_id": "amr-01",
        "time": datetime(2026, 10, 17, tzinfo=timezone.utc),
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.0,
        "frame_id": "map",
        "battery_percent": 80.0,
        "battery_voltage": 25.0,
        "charging": False,
        "status": "navigating",
        "errors": (),
    }
    values.update(overrides)
    return Telemetry(**values)  # type: ignore[arg-type]


def error(code: str = "E_LIDAR_TIMEOUT", severity: str = "warning") -> RobotError:
    return RobotError(code=code, message=f"{code} detected", severity=severity)
