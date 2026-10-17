from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            Node(
                package="ros2_bridge",
                executable="fleet_mqtt_bridge",
                name="fleet_mqtt_bridge",
                output="screen",
                parameters=[
                    {
                        "robot_id": "amr-01",
                        "mqtt_host": "localhost",
                        "mqtt_port": 1883,
                        "mqtt_topic_prefix": "fleet/telemetry",
                        "demo_motion": False,
                    }
                ],
            )
        ]
    )
