#!/usr/bin/env bash
# Build and test the Humble bridge inside ros:humble-ros-base.
set -eo pipefail

cd "$(dirname "$0")/.."

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3-colcon-common-extensions \
  python3-pytest \
  python3-paho-mqtt \
  ros-humble-geometry-msgs \
  ros-humble-sensor-msgs \
  ros-humble-diagnostic-msgs \
  ros-humble-launch-ros

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
colcon build --paths ros2_bridge --event-handlers console_direct+
# shellcheck disable=SC1091
source install/setup.bash
(cd ros2_bridge && python3 -m pytest -q)
python3 -c 'import runpy; runpy.run_path("ros2_bridge/launch/bridge.launch.py")'
