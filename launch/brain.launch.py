"""Launch Adaptive Mind 2501 brain node (ROS 2 Humble)."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    pkg_share = get_package_share_directory('adaptive_mind_2501')
    config = os.path.join(pkg_share, 'config', 'brain_params.yaml')

    return LaunchDescription([
        Node(
            package='adaptive_mind_2501',
            executable='brain_node',
            name='adaptive_mind_brain',
            output='screen',
            emulate_tty=True,
            parameters=[config],
            remappings=[],
        ),
    ])
