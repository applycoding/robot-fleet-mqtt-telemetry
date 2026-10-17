"""Publish a small simulated fleet. No ROS runtime required."""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Never, Protocol

import paho.mqtt.client as mqtt

from fleet_telemetry.schema import TOPIC_PREFIX, RobotError, Telemetry

logger = logging.getLogger(__name__)

Kind = Literal["patrol", "low_battery", "fault", "dropout"]


class Publisher(Protocol):
    def publish(self, topic: str, payload: str, qos: int = 0) -> object: ...


@dataclass
class RobotSim:
    robot_id: str
    kind: Kind
    phase: float
    radius: float
    battery: float
    ticks: int = 0
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


def build_fleet(scenario: str) -> list[RobotSim]:
    match scenario:
        case "demo":
            return [
                RobotSim("amr-01", "patrol", phase=0.0, radius=2.0, battery=82.0),
                RobotSim("amr-02", "low_battery", phase=1.0, radius=1.2, battery=18.0),
                RobotSim("amr-03", "fault", phase=2.0, radius=1.6, battery=64.0),
                RobotSim("amr-04", "dropout", phase=0.4, radius=2.4, battery=76.0),
            ]
        case "patrol":
            return [RobotSim("amr-01", "patrol", phase=0.0, radius=2.0, battery=82.0)]
        case _:
            raise ValueError(f"unknown scenario {scenario!r}")


def step(sim: RobotSim, now: datetime) -> Telemetry | None:
    sim.ticks += 1
    if sim.kind == "dropout" and sim.ticks > 2:
        return None
    _advance(sim)
    battery = sim.battery
    if sim.kind == "low_battery":
        sim.battery = max(4.0, sim.battery - 0.4)
    status, errors = _status_and_errors(sim.kind)
    return Telemetry(
        robot_id=sim.robot_id,
        time=now,
        x=sim.x,
        y=sim.y,
        yaw=sim.yaw,
        frame_id="map",
        battery_percent=battery,
        battery_voltage=_voltage(battery),
        charging=False,
        status=status,
        errors=errors,
    )


def publish_samples(
    fleet: list[RobotSim],
    client: Publisher,
    topic_prefix: str,
    now: datetime,
) -> int:
    published = 0
    for sim in fleet:
        sample = step(sim, now)
        if sample is None:
            continue
        client.publish(
            f"{topic_prefix}/{sample.robot_id}",
            json.dumps(sample.to_dict()),
            qos=1,
        )
        published += 1
    return published


def _advance(sim: RobotSim) -> None:
    angle = sim.phase + sim.ticks * 0.2
    sim.x = sim.radius * math.cos(angle)
    sim.y = sim.radius * math.sin(angle)
    heading = angle + math.pi / 2
    sim.yaw = math.atan2(math.sin(heading), math.cos(heading))


def _voltage(percent: float) -> float:
    return 21.0 + (percent / 100.0) * 6.0


def _status_and_errors(kind: Kind) -> tuple[str, tuple[RobotError, ...]]:
    match kind:
        case "patrol" | "dropout":
            return "navigating", ()
        case "low_battery":
            return "navigating", ()
        case "fault":
            return "error", (
                RobotError(
                    code="E_LIDAR_TIMEOUT",
                    message="front lidar scan is stale",
                    severity="warning",
                ),
            )
        case _ as unreachable:
            _never: Never = unreachable
            raise AssertionError(_never)


def _run(
    scenario: str,
    topic_prefix: str,
    period: float,
    client: mqtt.Client,
    stop: threading.Event,
) -> None:
    fleet = build_fleet(scenario)
    logger.info("simulating %s robot(s) on %s", len(fleet), topic_prefix)
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        count = publish_samples(fleet, client, topic_prefix, now)
        logger.info("published %s telemetry sample(s)", count)
        stop.wait(period)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    scenario = os.environ.get("SCENARIO", "demo")
    topic_prefix = os.environ.get("MQTT_TOPIC_PREFIX", TOPIC_PREFIX)
    hz = float(os.environ.get("PUBLISH_HZ", "1"))
    if hz < 0.1 or hz > 10:
        raise ValueError("PUBLISH_HZ must be between 0.1 and 10")
    build_fleet(scenario)
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=os.environ.get("MQTT_CLIENT_ID", f"fleet-sim-{os.getpid()}"),
    )
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    stop = threading.Event()

    def _handle(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    try:
        _run(scenario, topic_prefix, 1.0 / hz, client, stop)
    finally:
        client.loop_stop()
        client.disconnect()
