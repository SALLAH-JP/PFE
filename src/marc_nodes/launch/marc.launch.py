#!/usr/bin/env python3
"""
marc.launch.py — Démarre tout le système MARC en une commande.

    ros2 launch marc_nodes marc.launch.py

Lance les quatre nœuds :
    firmware_node   — pont série Arduino (/dev/ttyACM0)
    webbridge_node  — Flask HTTPS + interface web + LLM + SSE
    voice_node      — pipeline vocal (micro → STT → LLM → TTS)
    led_node        — matrice LED RGB

Arguments optionnels :
    port:=/dev/ttyACM1     port série de l'Arduino (défaut /dev/ttyACM0)
    style:=1               style de départ des yeux (défaut 2)
    use_voice:=false       ne pas lancer le pipeline vocal
    use_led:=false         ne pas lancer la matrice LED

Exemple :
    ros2 launch marc_nodes marc.launch.py port:=/dev/ttyACM1 use_voice:=false
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # ── Arguments ──
    port_arg = DeclareLaunchArgument(
        "port", default_value="/dev/ttyACM0",
        description="Port série de l'Arduino Mega")
    style_arg = DeclareLaunchArgument(
        "style", default_value="2",
        description="Style de départ des yeux LED")
    use_voice_arg = DeclareLaunchArgument(
        "use_voice", default_value="true",
        description="Lancer le pipeline vocal")
    use_led_arg = DeclareLaunchArgument(
        "use_led", default_value="true",
        description="Lancer la matrice LED")
    use_vision_arg = DeclareLaunchArgument(
        "use_vision", default_value="true",
        description="Lancer la chaîne vision (camera + localization + navigation)")

    port      = LaunchConfiguration("port")
    style     = LaunchConfiguration("style")
    use_voice = LaunchConfiguration("use_voice")
    use_led   = LaunchConfiguration("use_led")
    use_vision = LaunchConfiguration("use_vision")

    # ── Nœuds ──
    firmware_node = Node(
        package="marc_nodes",
        executable="firmware_node",
        name="firmware_node",
        output="screen",
        parameters=[{"port": port}],
    )

    webbridge_node = Node(
        package="marc_nodes",
        executable="webbridge_node",
        name="webbridge_node",
        output="screen",
    )

    voice_node = Node(
        package="marc_nodes",
        executable="voice_node",
        name="voice_node",
        output="screen",
        condition=IfCondition(use_voice),
    )

    led_node = Node(
        package="marc_nodes",
        executable="led_node",
        name="led_node",
        output="screen",
        parameters=[{"style": style}],
        condition=IfCondition(use_led),
    )

    # ── Vision ──
    camera_node = Node(
        package="marc_nodes",
        executable="camera_node",
        name="camera_node",
        output="screen",
        condition=IfCondition(use_vision),
    )

    localization_node = Node(
        package="marc_nodes",
        executable="localization_node",
        name="localization_node",
        output="screen",
        condition=IfCondition(use_vision),
    )

    navigation_node = Node(
        package="marc_nodes",
        executable="navigation_node",
        name="navigation_node",
        output="screen",
        condition=IfCondition(use_vision),
    )

    return LaunchDescription([
        port_arg,
        style_arg,
        use_voice_arg,
        use_led_arg,
        use_vision_arg,
        firmware_node,
        webbridge_node,
        voice_node,
        led_node,
        camera_node,
        localization_node,
        navigation_node,
    ])
