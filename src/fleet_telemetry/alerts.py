"""Threshold rules for low battery, stale heartbeats, and newly reported error codes."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Never

from fleet_telemetry.schema import Telemetry

Band = Literal["ok", "low", "critical"]


class AlertType(str, Enum):
    LOW_BATTERY = "low_battery"
    STALE_HEARTBEAT = "stale_heartbeat"
    ERROR_CODE = "error_code"


class Severity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    robot_id: str
    alert_type: AlertType
    severity: Severity
    message: str
    time: datetime
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "robot_id": self.robot_id,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "time": self.time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "details": self.details,
        }


class AlertEngine:
    """Edge-triggered rules.

    A condition notifies once when it starts, and again after it clears and returns.
    A cooldown suppresses a repeat of the same key while a value chatters around a threshold.
    """

    def __init__(
        self,
        *,
        low_percent: float = 20.0,
        critical_percent: float = 10.0,
        stale_after: timedelta = timedelta(seconds=15),
        cooldown: timedelta = timedelta(seconds=60),
    ) -> None:
        if critical_percent > low_percent:
            raise ValueError("critical battery threshold must be at or below the low threshold")
        if low_percent < 0 or critical_percent < 0:
            raise ValueError("battery thresholds must be non-negative")
        self._low_percent = low_percent
        self._critical_percent = critical_percent
        self._stale_after = stale_after
        self._cooldown = cooldown
        self._lock = threading.Lock()
        self._last_seen: dict[str, datetime] = {}
        self._stale_open: set[str] = set()
        self._battery_band: dict[str, Band] = {}
        self._active_errors: dict[str, set[str]] = {}
        self._last_fired: dict[tuple[str, str, str], datetime] = {}

    def evaluate(self, telemetry: Telemetry) -> list[Alert]:
        with self._lock:
            self._last_seen[telemetry.robot_id] = telemetry.time
            self._stale_open.discard(telemetry.robot_id)
            alerts: list[Alert] = []
            alerts.extend(self._battery_alerts(telemetry))
            alerts.extend(self._error_alerts(telemetry))
            return alerts

    def check_stale(self, now: datetime) -> list[Alert]:
        with self._lock:
            alerts: list[Alert] = []
            for robot_id, last_seen in self._last_seen.items():
                age = now - last_seen
                if age <= self._stale_after or robot_id in self._stale_open:
                    continue
                self._stale_open.add(robot_id)
                seconds = max(0, int(age.total_seconds()))
                alerts.append(
                    Alert(
                        robot_id=robot_id,
                        alert_type=AlertType.STALE_HEARTBEAT,
                        severity=Severity.WARNING,
                        message=f"{robot_id} heartbeat is stale (last sample {seconds}s ago)",
                        time=now,
                        details={
                            "last_seen": last_seen.astimezone(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "age_seconds": seconds,
                            "stale_after_seconds": self._stale_after.total_seconds(),
                        },
                    )
                )
            return alerts

    def _battery_alerts(self, telemetry: Telemetry) -> list[Alert]:
        band = _battery_band(telemetry, self._low_percent, self._critical_percent)
        previous = self._battery_band.get(telemetry.robot_id, "ok")
        self._battery_band[telemetry.robot_id] = band
        if _band_rank(band) <= _band_rank(previous):
            return []
        key = (telemetry.robot_id, AlertType.LOW_BATTERY.value, band)
        if self._in_cooldown(key, telemetry.time):
            return []
        self._last_fired[key] = telemetry.time
        threshold = self._critical_percent if band == "critical" else self._low_percent
        severity = Severity.CRITICAL if band == "critical" else Severity.WARNING
        return [
            Alert(
                robot_id=telemetry.robot_id,
                alert_type=AlertType.LOW_BATTERY,
                severity=severity,
                message=(
                    f"{telemetry.robot_id} battery at {telemetry.battery_percent:.1f}% "
                    f"(threshold {threshold:.0f}%)"
                ),
                time=telemetry.time,
                details={
                    "battery_percent": telemetry.battery_percent,
                    "threshold_percent": threshold,
                    "charging": telemetry.charging,
                    "band": band,
                },
            )
        ]

    def _error_alerts(self, telemetry: Telemetry) -> list[Alert]:
        current = {error.code: error for error in telemetry.errors}
        previous = self._active_errors.get(telemetry.robot_id, set())
        alerts: list[Alert] = []
        for code, error in current.items():
            if code in previous:
                continue
            key = (telemetry.robot_id, AlertType.ERROR_CODE.value, code)
            if self._in_cooldown(key, telemetry.time):
                continue
            self._last_fired[key] = telemetry.time
            alerts.append(
                Alert(
                    robot_id=telemetry.robot_id,
                    alert_type=AlertType.ERROR_CODE,
                    severity=_alert_severity(error.severity),
                    message=f"{telemetry.robot_id} reported {error.code}: {error.message}",
                    time=telemetry.time,
                    details={
                        "code": error.code,
                        "error_message": error.message,
                        "error_severity": error.severity,
                    },
                )
            )
        self._active_errors[telemetry.robot_id] = set(current)
        return alerts

    def _in_cooldown(self, key: tuple[str, str, str], now: datetime) -> bool:
        last = self._last_fired.get(key)
        if last is None:
            return False
        return now - last < self._cooldown


def _battery_band(telemetry: Telemetry, low_percent: float, critical_percent: float) -> Band:
    if telemetry.charging:
        return "ok"
    if telemetry.battery_percent <= critical_percent:
        return "critical"
    if telemetry.battery_percent <= low_percent:
        return "low"
    return "ok"


def _band_rank(band: Band) -> int:
    match band:
        case "ok":
            return 0
        case "low":
            return 1
        case "critical":
            return 2
        case _ as unreachable:
            _never: Never = unreachable
            raise AssertionError(_never)


def _alert_severity(severity: str) -> Severity:
    match severity:
        case "warning":
            return Severity.WARNING
        case "critical":
            return Severity.CRITICAL
        case _ as unreachable:
            _never: Never = unreachable
            raise AssertionError(_never)
