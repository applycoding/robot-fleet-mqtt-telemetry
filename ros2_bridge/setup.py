from setuptools import find_packages, setup

package_name = "ros2_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/bridge.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Arpan Bom",
    maintainer_email="arpan.bom@users.noreply.github.com",
    description="Publish ROS 2 robot telemetry to MQTT.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "fleet_mqtt_bridge = ros2_bridge.bridge_node:main",
        ],
    },
)
