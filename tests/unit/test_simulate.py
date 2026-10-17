import json
import math
from datetime import datetime, timezone

from fleet_telemetry.schema import parse_telemetry
from fleet_telemetry.simulate import build_fleet, publish_samples, step


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object], int]] = []

    def publish(self, topic: str, payload: str, qos: int = 0) -> object:
        self.messages.append((topic, json.loads(payload), qos))
        return None


def test_demo_fleet_matches_the_four_stories() -> None:
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    fleet = build_fleet("demo")
    client = FakeClient()
    assert publish_samples(fleet, client, "fleet/telemetry", now) == 4
    by_id = {body["robot_id"]: body for _, body, _ in client.messages}
    assert by_id["amr-02"]["battery"]["percent"] == 18.0
    assert by_id["amr-03"]["errors"][0]["code"] == "E_LIDAR_TIMEOUT"
    assert by_id["amr-03"]["status"] == "error"
    for _, body, qos in client.messages:
        parse_telemetry(body)
        assert qos == 1
        assert body["pose"]["frame_id"] == "map"


def test_dropout_robot_stops_after_two_samples() -> None:
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    sim = build_fleet("demo")[3]
    assert sim.robot_id == "amr-04"
    assert step(sim, now) is not None
    assert step(sim, now) is not None
    assert step(sim, now) is None


def test_low_battery_robot_drains_after_each_sample() -> None:
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    sim = next(robot for robot in build_fleet("demo") if robot.kind == "low_battery")
    first = step(sim, now)
    second = step(sim, now)
    assert first is not None and second is not None
    assert first.battery_percent == 18.0
    assert math.isclose(second.battery_percent, 17.6)


def test_unknown_scenario_is_rejected() -> None:
    try:
        build_fleet("swarm")
    except ValueError as exc:
        assert "swarm" in str(exc)
    else:
        raise AssertionError("expected ValueError")
