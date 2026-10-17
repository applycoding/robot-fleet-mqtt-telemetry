"""Wire format shared by the simulator, the ingestion service, and the ROS 2 bridge."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

ROBOT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
TOPIC_PREFIX = "fleet/telemetry"
MAX_ERRORS = 20


class PayloadError(ValueError):
    """A telemetry payload is not usable and should be discarded."""


@dataclass(frozen=True)
class RobotError:
    code: str
    message: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass(frozen=True)
class Telemetry:
    robot_id: str
    time: datetime
    x: float
    y: float
    yaw: float
    frame_id: str
    battery_percent: float
    battery_voltage: float
    charging: bool
    status: str
    errors: tuple[RobotError, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "time": format_time(self.time),
            "pose": {
                "x": self.x,
                "y": self.y,
                "yaw": self.yaw,
                "frame_id": self.frame_id,
            },
            "battery": {
                "percent": self.battery_percent,
                "voltage": self.battery_voltage,
                "charging": self.charging,
            },
            "status": self.status,
            "errors": [error.to_dict() for error in self.errors],
        }


def format_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise PayloadError("time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def is_online(sample_time: datetime, now: datetime, stale_after: timedelta) -> bool:
    return now - sample_time <= stale_after


def parse_telemetry(payload: bytes | str | dict[str, Any]) -> Telemetry:
    data = _decode(payload)
    if not isinstance(data, dict):
        raise PayloadError("payload must be a JSON object")
    pose = _require_dict(data, "pose")
    battery = _require_dict(data, "battery")
    errors = _parse_errors(data.get("errors", []))
    return Telemetry(
        robot_id=_require_robot_id(data.get("robot_id")),
        time=_parse_time(data.get("time")),
        x=_require_finite(pose, "x"),
        y=_require_finite(pose, "y"),
        yaw=_require_finite(pose, "yaw"),
        frame_id=_require_text(pose.get("frame_id"), "frame_id", 64),
        battery_percent=_require_percent(battery.get("percent")),
        battery_voltage=_require_voltage(battery.get("voltage")),
        charging=_require_bool(battery.get("charging"), "charging"),
        status=_require_text(data.get("status"), "status", 64),
        errors=errors,
    )


def _decode(payload: bytes | str | dict[str, Any]) -> Any:
    if isinstance(payload, dict):
        return payload
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        return json.loads(text)
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError("payload is not valid JSON") from exc


def _require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise PayloadError(f"{key} must be an object")
    return value


def _require_robot_id(value: Any) -> str:
    if not isinstance(value, str) or ROBOT_ID_RE.fullmatch(value) is None:
        raise PayloadError("robot_id must match [A-Za-z0-9_-]{1,64}")
    return value


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise PayloadError("time must be an ISO-8601 string")
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PayloadError("time must be an ISO-8601 string") from exc
    if parsed.tzinfo is None:
        raise PayloadError("time must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_finite(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise PayloadError(f"{key} must be finite")
    return number


def _require_percent(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError("battery percent must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 100:
        raise PayloadError("battery percent must be between 0 and 100")
    return number


def _require_voltage(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError("battery voltage must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 100:
        raise PayloadError("battery voltage must be between 0 and 100")
    return number


def _require_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise PayloadError(f"{key} must be a boolean")
    return value


def _require_text(value: Any, key: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PayloadError(f"{key} must be a string")
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise PayloadError(f"{key} must be 1-{limit} characters without control characters")
    return value


def _parse_errors(value: Any) -> tuple[RobotError, ...]:
    if not isinstance(value, list):
        raise PayloadError("errors must be a list")
    if len(value) > MAX_ERRORS:
        raise PayloadError(f"errors must contain at most {MAX_ERRORS} items")
    return tuple(_parse_error(item) for item in value)


def _parse_error(value: Any) -> RobotError:
    if not isinstance(value, dict):
        raise PayloadError("error must be an object")
    severity = _require_text(value.get("severity"), "severity", 16)
    if severity not in {"warning", "critical"}:
        raise PayloadError("error severity must be warning or critical")
    return RobotError(
        code=_require_text(value.get("code"), "code", 64),
        message=_require_text(value.get("message"), "message", 200),
        severity=severity,
    )
