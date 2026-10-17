"""Aggregate pose, battery, status, and diagnostics, then publish telemetry JSON."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Protocol

import paho.mqtt.client as mqtt
import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

from ros2_bridge.codec import (
    battery_percent,
    battery_voltage,
    build_payload,
    errors_from_diagnostics,
    is_charging,
    yaw_from_quaternion,
)


class MqttPublisher(Protocol):
    def publish_json(self, payload: str) -> None: ...


def make_mqtt_client(client_id: str) -> mqtt.Client:
    version = getattr(mqtt, "CallbackAPIVersion", None)
    if version is None:
        return mqtt.Client(client_id=client_id, clean_session=True)
    return mqtt.Client(version.VERSION1, client_id=client_id)


class PahoPublisher:
    def __init__(self, host: str, port: int, topic: str, client_id: str) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._client = make_mqtt_client(client_id)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    def connect(self) -> None:
        last_error: OSError | None = None
        for attempt in range(1, 11):
            try:
                self._client.connect(self._host, self._port, keepalive=30)
                self._client.loop_start()
                return
            except OSError as exc:
                last_error = exc
                if attempt == 10:
                    break
                time.sleep(1.0)
        assert last_error is not None
        raise last_error

    def publish_json(self, payload: str) -> None:
        self._client.publish(self._topic, payload, qos=1)

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


class FleetMqttBridge(Node):
    def __init__(
        self,
        mqtt_client: MqttPublisher | None = None,
        node_name: str = "fleet_mqtt_bridge",
        **kwargs: Any,
    ) -> None:
        super().__init__(node_name, **kwargs)
        self.declare_parameter("robot_id", "amr-01")
        self.declare_parameter("mqtt_host", "localhost")
        self.declare_parameter("mqtt_port", 1883)
        self.declare_parameter("mqtt_topic_prefix", "fleet/telemetry")
        self.declare_parameter("publish_period_sec", 1.0)
        self.declare_parameter("pose_topic", "pose")
        self.declare_parameter("battery_topic", "battery_state")
        self.declare_parameter("status_topic", "robot_status")
        self.declare_parameter("diagnostics_topic", "diagnostics")
        self.declare_parameter("demo_motion", False)

        self._robot_id = str(self.get_parameter("robot_id").value)
        prefix = str(self.get_parameter("mqtt_topic_prefix").value).rstrip("/")
        topic = f"{prefix}/{self._robot_id}"
        period = float(self.get_parameter("publish_period_sec").value)
        self._demo_motion = bool(self.get_parameter("demo_motion").value)
        self._demo_ticks = 0

        self._x: float | None = None
        self._y: float | None = None
        self._yaw: float | None = None
        self._frame_id = "map"
        self._battery_percent: float | None = None
        self._battery_voltage = 0.0
        self._charging = False
        self._status: str | None = None
        self._errors: list[dict[str, str]] = []
        self._pose_from_topic = False
        self._battery_from_topic = False

        self._owned_mqtt = mqtt_client is None
        if mqtt_client is None:
            host = str(self.get_parameter("mqtt_host").value)
            port = int(self.get_parameter("mqtt_port").value)
            owned = PahoPublisher(host, port, topic, f"fleet-bridge-{self._robot_id}")
            owned.connect()
            self._mqtt: MqttPublisher = owned
        else:
            self._mqtt = mqtt_client

        self.create_subscription(
            PoseStamped, str(self.get_parameter("pose_topic").value), self._on_pose, 10
        )
        self.create_subscription(
            BatteryState, str(self.get_parameter("battery_topic").value), self._on_battery, 10
        )
        self.create_subscription(
            String, str(self.get_parameter("status_topic").value), self._on_status, 10
        )
        self.create_subscription(
            DiagnosticArray,
            str(self.get_parameter("diagnostics_topic").value),
            self._on_diagnostics,
            10,
        )
        self.create_timer(period, self._on_timer)
        self.get_logger().info(f"publishing telemetry to {topic}")

    def destroy_node(self) -> bool:
        if self._owned_mqtt and isinstance(self._mqtt, PahoPublisher):
            self._mqtt.disconnect()
        return super().destroy_node()

    def _on_pose(self, message: PoseStamped) -> None:
        self._x = float(message.pose.position.x)
        self._y = float(message.pose.position.y)
        orientation = message.pose.orientation
        self._yaw = yaw_from_quaternion(orientation.x, orientation.y, orientation.z, orientation.w)
        frame = message.header.frame_id.strip()
        self._frame_id = frame or "map"
        self._pose_from_topic = True

    def _on_battery(self, message: BatteryState) -> None:
        percent = battery_percent(float(message.percentage))
        if percent is None:
            return
        self._battery_percent = percent
        self._battery_voltage = battery_voltage(float(message.voltage))
        self._charging = is_charging(int(message.power_supply_status))
        self._battery_from_topic = True

    def _on_status(self, message: String) -> None:
        text = message.data.strip()
        if text:
            self._status = text

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        self._errors = errors_from_diagnostics(message)

    def _apply_demo(self) -> None:
        self._demo_ticks += 1
        angle = self._demo_ticks * 0.15
        if not self._pose_from_topic:
            self._x = 2.0 * math.cos(angle)
            self._y = 2.0 * math.sin(angle)
            self._yaw = math.atan2(math.sin(angle + math.pi / 2), math.cos(angle + math.pi / 2))
            self._frame_id = "map"
        if not self._battery_from_topic:
            self._battery_percent = 78.0
            self._battery_voltage = 25.6
            self._charging = False
        if self._status is None:
            self._status = "navigating"

    def _on_timer(self) -> None:
        if self._demo_motion:
            self._apply_demo()
        if self._x is None or self._y is None or self._yaw is None or self._battery_percent is None:
            return
        payload = build_payload(
            robot_id=self._robot_id,
            when=datetime.now(timezone.utc),
            x=self._x,
            y=self._y,
            yaw=self._yaw,
            frame_id=self._frame_id,
            battery_percent_value=self._battery_percent,
            battery_voltage_value=self._battery_voltage,
            charging=self._charging,
            status=self._status or "unknown",
            errors=self._errors,
        )
        self._mqtt.publish_json(json.dumps(payload))


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = FleetMqttBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
