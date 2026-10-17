"""Pure helpers that turn ROS messages into the fleet telemetry JSON object.

sensor_msgs/BatteryState.percentage is documented as 0.0 to 1.0. Some drivers
publish 0 to 100 instead. Values in [0, 1] are treated as a fraction, and values
above 1 are treated as a percent. A negative or non-finite reading means unknown.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from sensor_msgs.msg import BatteryState


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    norm = x * x + y * y + z * z + w * w
    if norm < 1e-12:
        return 0.0
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def battery_percent(percentage: float) -> float | None:
    if not math.isfinite(percentage) or percentage < 0.0:
        return None
    if percentage <= 1.0:
        return percentage * 100.0
    return min(percentage, 100.0)


def battery_voltage(voltage: float) -> float:
    if not math.isfinite(voltage) or voltage < 0.0:
        return 0.0
    return voltage


def is_charging(power_supply_status: int) -> bool:
    return power_supply_status == BatteryState.POWER_SUPPLY_STATUS_CHARGING


def errors_from_diagnostics(message: DiagnosticArray) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for status in message.status:
        if status.level == DiagnosticStatus.OK:
            continue
        severity = "critical" if status.level >= DiagnosticStatus.ERROR else "warning"
        name = status.name.strip()[:64] or "DIAGNOSTIC"
        detail = status.message.strip()[:200] or name
        errors.append({"code": name, "message": detail, "severity": severity})
    return errors


def build_payload(
    *,
    robot_id: str,
    when: datetime,
    x: float,
    y: float,
    yaw: float,
    frame_id: str,
    battery_percent_value: float,
    battery_voltage_value: float,
    charging: bool,
    status: str,
    errors: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "robot_id": robot_id,
        "time": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pose": {"x": x, "y": y, "yaw": yaw, "frame_id": frame_id or "map"},
        "battery": {
            "percent": battery_percent_value,
            "voltage": battery_voltage_value,
            "charging": charging,
        },
        "status": status,
        "errors": errors,
    }
