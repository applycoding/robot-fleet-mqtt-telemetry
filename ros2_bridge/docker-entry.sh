#!/bin/bash
set -euo pipefail
source /opt/ros/humble/setup.bash
source /ws/install/setup.bash
exec ros2 run ros2_bridge fleet_mqtt_bridge --ros-args \
  -p "robot_id:=${ROBOT_ID:-amr-ros}" \
  -p "mqtt_host:=${MQTT_HOST:-mosquitto}" \
  -p "mqtt_port:=${MQTT_PORT:-1883}" \
  -p "demo_motion:=${DEMO_MOTION:-true}"
