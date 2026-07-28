"""Setup for Project 2501 (ROS 2 package: adaptive_mind_2501)."""

from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'adaptive_mind_2501'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        # Ament resource index (required for ros2 pkg / launch discovery)
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'LICENSE', 'README.md']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=[
        'setuptools',
        'networkx',
        'requests',
    ],
    zip_safe=True,
    author='Project 2501',
    author_email='loris@todo.com',
    maintainer='Project 2501',
    maintainer_email='loris@todo.com',
    keywords=['ROS', 'ROS2', 'Humble', 'Project2501', 'cognitive', 'robotics'],
    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
    ],
    description=(
        'Project 2501 — modular proactive cognitive control pipeline '
        'for autonomous robotics (ROS 2 Humble).'
    ),
    long_description=(
        'Project 2501 (Venticinque Zero Uno) provides DialogueParser, '
        'GraphMemory, TaskPlanner, SafetyGovernor, and ProactiveEngine '
        'orchestrated by AdaptiveMindBrain.'
    ),
    long_description_content_type='text/plain',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'brain_node = adaptive_mind_2501.brain_node:main',
        ],
    },
)
