"""TimescaleDB storage for telemetry samples and alerts."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from fleet_telemetry.alerts import Alert, AlertType, Severity
from fleet_telemetry.schema import RobotError, Telemetry

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


class TimescaleStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def ensure_schema(self, attempts: int = 20, delay_seconds: float = 1.0) -> None:
        statements = split_sql(schema_sql())
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self._run_statements(statements)
                return
            except psycopg.OperationalError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(delay_seconds)
        assert last_error is not None
        raise last_error

    def ping(self) -> None:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> None:
            conn.execute("SELECT 1")

        self._with_conn(op)

    def insert_telemetry(self, telemetry: Telemetry) -> None:
        errors = Json([error.to_dict() for error in telemetry.errors])

        def op(conn: psycopg.Connection[dict[str, Any]]) -> None:
            conn.execute(
                """
                INSERT INTO robot_telemetry (
                    time, robot_id, x, y, yaw, frame_id,
                    battery_percent, battery_voltage, charging, status, errors
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    telemetry.time,
                    telemetry.robot_id,
                    telemetry.x,
                    telemetry.y,
                    telemetry.yaw,
                    telemetry.frame_id,
                    telemetry.battery_percent,
                    telemetry.battery_voltage,
                    telemetry.charging,
                    telemetry.status,
                    errors,
                ),
            )

        self._with_conn(op)

    def insert_alert(self, alert: Alert) -> None:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> None:
            conn.execute(
                """
                INSERT INTO alerts (time, robot_id, alert_type, severity, message, details)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    alert.time,
                    alert.robot_id,
                    alert.alert_type.value,
                    alert.severity.value,
                    alert.message,
                    Json(alert.details),
                ),
            )

        self._with_conn(op)

    def latest(self) -> list[Telemetry]:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> list[dict[str, Any]]:
            return list(
                conn.execute(
                    """
                    SELECT DISTINCT ON (robot_id)
                           time, robot_id, x, y, yaw, frame_id,
                           battery_percent, battery_voltage, charging, status, errors
                    FROM robot_telemetry
                    ORDER BY robot_id, time DESC
                    """
                ).fetchall()
            )

        return [_telemetry_from_row(row) for row in self._with_conn(op)]

    def latest_for(self, robot_id: str) -> Telemetry | None:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> dict[str, Any] | None:
            return conn.execute(
                """
                SELECT time, robot_id, x, y, yaw, frame_id,
                       battery_percent, battery_voltage, charging, status, errors
                FROM robot_telemetry
                WHERE robot_id = %s
                ORDER BY time DESC
                LIMIT 1
                """,
                (robot_id,),
            ).fetchone()

        row = self._with_conn(op)
        if row is None:
            return None
        return _telemetry_from_row(row)

    def history(self, robot_id: str, since: datetime) -> list[Telemetry]:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> list[dict[str, Any]]:
            return list(
                conn.execute(
                    """
                    SELECT time, robot_id, x, y, yaw, frame_id,
                           battery_percent, battery_voltage, charging, status, errors
                    FROM robot_telemetry
                    WHERE robot_id = %s AND time >= %s
                    ORDER BY time ASC
                    """,
                    (robot_id, since),
                ).fetchall()
            )

        return [_telemetry_from_row(row) for row in self._with_conn(op)]

    def recent_alerts(self, limit: int, robot_id: str | None = None) -> list[Alert]:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> list[dict[str, Any]]:
            if robot_id is None:
                return list(
                    conn.execute(
                        """
                        SELECT time, robot_id, alert_type, severity, message, details
                        FROM alerts
                        ORDER BY time DESC
                        LIMIT %s
                        """,
                        (limit,),
                    ).fetchall()
                )
            return list(
                conn.execute(
                    """
                    SELECT time, robot_id, alert_type, severity, message, details
                    FROM alerts
                    WHERE robot_id = %s
                    ORDER BY time DESC
                    LIMIT %s
                    """,
                    (robot_id, limit),
                ).fetchall()
            )

        return [_alert_from_row(row) for row in self._with_conn(op)]

    def telemetry_for(self, robot_id: str) -> list[Telemetry]:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> list[dict[str, Any]]:
            return list(
                conn.execute(
                    """
                    SELECT time, robot_id, x, y, yaw, frame_id,
                           battery_percent, battery_voltage, charging, status, errors
                    FROM robot_telemetry
                    WHERE robot_id = %s
                    ORDER BY time ASC
                    """,
                    (robot_id,),
                ).fetchall()
            )

        return [_telemetry_from_row(row) for row in self._with_conn(op)]

    def alerts_for(self, robot_id: str) -> list[Alert]:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> list[dict[str, Any]]:
            return list(
                conn.execute(
                    """
                    SELECT time, robot_id, alert_type, severity, message, details
                    FROM alerts
                    WHERE robot_id = %s
                    ORDER BY time ASC
                    """,
                    (robot_id,),
                ).fetchall()
            )

        return [_alert_from_row(row) for row in self._with_conn(op)]

    def _run_statements(self, statements: list[str]) -> None:
        def op(conn: psycopg.Connection[dict[str, Any]]) -> None:
            for statement in statements:
                conn.execute(statement)

        self._with_conn(op, attempts=1)

    def _with_conn(self, fn: Any, attempts: int = 2) -> Any:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with psycopg.connect(
                    self._database_url,
                    autocommit=True,
                    connect_timeout=5,
                    row_factory=dict_row,
                ) as conn:
                    return fn(conn)
            except psycopg.OperationalError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(0.2)
        assert last_error is not None
        raise last_error


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _telemetry_from_row(row: dict[str, Any]) -> Telemetry:
    raw_errors = row["errors"] or []
    errors = tuple(
        RobotError(code=item["code"], message=item["message"], severity=item["severity"])
        for item in raw_errors
    )
    return Telemetry(
        robot_id=row["robot_id"],
        time=_as_utc(row["time"]),
        x=row["x"],
        y=row["y"],
        yaw=row["yaw"],
        frame_id=row["frame_id"],
        battery_percent=row["battery_percent"],
        battery_voltage=row["battery_voltage"],
        charging=row["charging"],
        status=row["status"],
        errors=errors,
    )


def _alert_from_row(row: dict[str, Any]) -> Alert:
    return Alert(
        robot_id=row["robot_id"],
        alert_type=AlertType(row["alert_type"]),
        severity=Severity(row["severity"]),
        message=row["message"],
        time=_as_utc(row["time"]),
        details=row["details"] or {},
    )
