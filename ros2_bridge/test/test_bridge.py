import json
import math

import pytest
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from geometry_msgs.msg import PoseStamped
from rclpy.parameter import Parameter
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

from ros2_bridge.bridge_node import FleetMqttBridge


class RecordingMqtt:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def publish_json(self, payload: str) -> None:
        self.payloads.append(payload)


@pytest.fixture(scope="module")
def ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def _bridge(recorder: RecordingMqtt, name: str, demo: bool) -> FleetMqttBridge:
    return FleetMqttBridge(
        mqtt_client=recorder,
        node_name=name,
        parameter_overrides=[
            Parameter("demo_motion", Parameter.Type.BOOL, demo),
            Parameter("publish_period_sec", Parameter.Type.DOUBLE, 0.2),
            Parameter("robot_id", Parameter.Type.STRING, "amr-01"),
        ],
    )


def test_demo_motion_publishes_without_topics(ros_context: object) -> None:
    del ros_context
    recorder = RecordingMqtt()
    node = _bridge(recorder, "bridge_demo", demo=True)
    try:
        node._on_timer()
        body = json.loads(recorder.payloads[-1])
        assert body["robot_id"] == "amr-01"
        assert body["battery"]["percent"] == 78.0
        assert body["status"] == "navigating"
        assert body["pose"]["frame_id"] == "map"
    finally:
        node.destroy_node()


def test_topics_override_demo_and_reach_mqtt(ros_context: object) -> None:
    del ros_context
    recorder = RecordingMqtt()
    node = _bridge(recorder, "bridge_topics", demo=False)
    try:
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.pose.position.x = 1.5
        pose.pose.position.y = -2.0
        pose.pose.orientation.w = 1.0
        battery = BatteryState()
        battery.percentage = 0.42
        battery.voltage = 24.1
        battery.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        diagnostics = DiagnosticArray()
        warning = DiagnosticStatus()
        warning.name = "front_lidar"
        warning.level = DiagnosticStatus.WARN
        warning.message = "stale scan"
        diagnostics.status = [warning]
        status = String()
        status.data = "navigating"
        node._on_pose(pose)
        node._on_battery(battery)
        node._on_status(status)
        node._on_diagnostics(diagnostics)
        node._on_timer()
        body = json.loads(recorder.payloads[-1])
        assert body["pose"]["x"] == 1.5
        assert body["pose"]["y"] == -2.0
        assert math.isclose(body["battery"]["percent"], 42.0)
        assert body["battery"]["voltage"] == 24.1
        assert body["battery"]["charging"] is False
        assert body["status"] == "navigating"
        assert body["errors"][0]["code"] == "front_lidar"
    finally:
        node.destroy_node()
