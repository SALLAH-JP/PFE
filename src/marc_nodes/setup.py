from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'marc_nodes'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Installe les launch files pour 'ros2 launch marc_nodes ...'
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools', 'pyserial'],
    zip_safe=True,
    maintainer='Jean-Paul',
    maintainer_email='you@example.com',
    description='Noeuds ROS2 du robot MARC',
    license='MIT',
    entry_points={
        'console_scripts': [
            # ros2 run marc_nodes firmware_node
            'firmware_node = marc_nodes.firmware_node:main',
            # ros2 run marc_nodes webbridge_node
            'webbridge_node = marc_nodes.webbridge_node:main',
            # ros2 run marc_nodes voice_node
            'voice_node = marc_nodes.voice_node:main',
            # ros2 run marc_nodes led_node
            'led_node = marc_nodes.led_node:main',
            # ── Vision ──
            # ros2 run marc_nodes camera_node
            'camera_node = marc_nodes.camera_node:main',
            # ros2 run marc_nodes localization_node
            'localization_node = marc_nodes.localization_node:main',
            # ros2 run marc_nodes navigation_node
            'navigation_node = marc_nodes.navigation_node:main',
        ],
    },
)
