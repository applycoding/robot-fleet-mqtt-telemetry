"""Read-only fleet status API."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from fleet_telemetry.alerts import Alert
from fleet_telemetry.schema import ROBOT_ID_RE, Telemetry, is_online
from fleet_telemetry.store import TimescaleStore

logger = logging.getLogger(__name__)


class PoseModel(BaseModel):
    x: float
    y: float
    yaw: float
    frame_id: str


class BatteryModel(BaseModel):
    percent: float
    voltage: float
    charging: bool


class ErrorModel(BaseModel):
    code: str
    message: str
    severity: str


class SampleModel(BaseModel):
    robot_id: str
    time: datetime
    pose: PoseModel
    battery: BatteryModel
    status: str
    errors: list[ErrorModel]


class RobotStateModel(SampleModel):
    online: bool


class FleetResponse(BaseModel):
    robots: list[RobotStateModel]
    stale_after_seconds: float


class HistoryResponse(BaseModel):
    robot_id: str
    samples: list[SampleModel]


class AlertModel(BaseModel):
    robot_id: str
    alert_type: str
    severity: str
    message: str
    time: datetime
    details: dict[str, object] = Field(default_factory=dict)


class AlertListResponse(BaseModel):
    alerts: list[AlertModel]


def sample_model(sample: Telemetry) -> SampleModel:
    return SampleModel(
        robot_id=sample.robot_id,
        time=sample.time,
        pose=PoseModel(
            x=sample.x,
            y=sample.y,
            yaw=sample.yaw,
            frame_id=sample.frame_id,
        ),
        battery=BatteryModel(
            percent=sample.battery_percent,
            voltage=sample.battery_voltage,
            charging=sample.charging,
        ),
        status=sample.status,
        errors=[
            ErrorModel(code=error.code, message=error.message, severity=error.severity)
            for error in sample.errors
        ],
    )


def robot_state(sample: Telemetry, now: datetime, stale_after: timedelta) -> RobotStateModel:
    base = sample_model(sample)
    return RobotStateModel(**base.model_dump(), online=is_online(sample.time, now, stale_after))


def alert_model(alert: Alert) -> AlertModel:
    return AlertModel(
        robot_id=alert.robot_id,
        alert_type=alert.alert_type.value,
        severity=alert.severity.value,
        message=alert.message,
        time=alert.time,
        details=alert.details,
    )


def create_app(database_url: str, stale_after: timedelta) -> FastAPI:
    store = TimescaleStore(database_url)
    app = FastAPI(title="Fleet telemetry", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            store.ping()
        except Exception as exc:
            logger.exception("database ping failed")
            raise HTTPException(status_code=503, detail="degraded") from exc
        return {"status": "ok"}

    @app.get("/fleet", response_model=FleetResponse)
    def fleet() -> FleetResponse:
        now = datetime.now(timezone.utc)
        robots = [robot_state(sample, now, stale_after) for sample in store.latest()]
        robots.sort(key=lambda item: item.robot_id)
        return FleetResponse(robots=robots, stale_after_seconds=stale_after.total_seconds())

    @app.get("/fleet/{robot_id}", response_model=RobotStateModel)
    def robot(robot_id: str) -> RobotStateModel:
        _check_robot_id(robot_id)
        sample = store.latest_for(robot_id)
        if sample is None:
            raise HTTPException(status_code=404, detail="robot not found")
        return robot_state(sample, datetime.now(timezone.utc), stale_after)

    @app.get("/fleet/{robot_id}/history", response_model=HistoryResponse)
    def history(
        robot_id: str,
        minutes: Annotated[int, Query(ge=1, le=1440)] = 15,
    ) -> HistoryResponse:
        _check_robot_id(robot_id)
        if store.latest_for(robot_id) is None:
            raise HTTPException(status_code=404, detail="robot not found")
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        samples = [sample_model(sample) for sample in store.history(robot_id, since)]
        return HistoryResponse(robot_id=robot_id, samples=samples)

    @app.get("/alerts", response_model=AlertListResponse)
    def alerts(
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        robot_id: str | None = None,
    ) -> AlertListResponse:
        if robot_id is not None:
            _check_robot_id(robot_id)
        rows = store.recent_alerts(limit, robot_id)
        return AlertListResponse(alerts=[alert_model(row) for row in rows])

    return app


def _check_robot_id(robot_id: str) -> None:
    if ROBOT_ID_RE.fullmatch(robot_id) is None:
        raise HTTPException(status_code=400, detail="invalid robot id")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database_url = os.environ["DATABASE_URL"]
    port = int(os.environ.get("PORT", "8000"))
    stale_after = timedelta(seconds=float(os.environ.get("STALE_AFTER_SECONDS", "15")))
    uvicorn.run(
        create_app(database_url, stale_after),
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
