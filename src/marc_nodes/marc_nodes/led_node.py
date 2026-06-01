#!/usr/bin/env python3
"""
led_node.py — Matrice LED de MARC en nœud ROS2.

Ce nœud est le SEUL propriétaire de la matrice RGB 64×32. Tout autre nœud
qui veut afficher une expression PUBLIE sur le topic /led_expression au lieu
d'accéder directement au matériel — ça évite les conflits d'accès GPIO.

  SOUSCRIT
    /led_expression   std_msgs/String

Protocole du message (String) :
    "love" | "neutral" | "suspicious" | "cry" | "blink" | "disappear"
                                  → joue l'animation une fois
    "idle:neutral"                → change l'animation idle de base
    "style:2"                     → change le style (recharge les GIFs)

Réutilise EyeManager (matrixLed/eye_manager.py) sans le réécrire.

Note : sur ce robot, l'accès GPIO se fait sans sudo grâce à
    setcap 'cap_sys_rawio+ep' sur le binaire Python.
Donc ce nœud se lance comme les autres (ros2 run), pas besoin de sudo.
"""

import os
import sys

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# ─────────────────────────────────────────────
#  Chemins du projet
#     MARC/
#       ├── src/marc_nodes/marc_nodes/led_node.py   <- CE FICHIER
#       └── matrixLed/eye_manager.py + style1/ style2/ ...
# ─────────────────────────────────────────────
ROOT = "/home/pi/MARC"
GIF_DIR = os.path.join(ROOT, "matrixLed")
sys.path.append(GIF_DIR)

# EyeManager est importé toujours ; rgbmatrix peut manquer sur PC.
from eye_manager import EyeManager  # noqa: E402

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    MATRIX_AVAILABLE = True
except ImportError:
    print("⚠️  rgbmatrix non disponible (mode PC) — led_node tournera à vide")
    MATRIX_AVAILABLE = False


# Animations connues (doivent correspondre aux clés d'EyeManager.ANIMATIONS)
KNOWN_ANIMATIONS = {"neutral", "blink", "suspicious", "disappear", "cry", "love"}


class LedNode(Node):
    def __init__(self):
        super().__init__("led_node")

        # Paramètre : style de départ
        self.declare_parameter("style", 2)
        style = self.get_parameter("style").get_parameter_value().integer_value

        self.eyes = None
        self.matrix = None

        if MATRIX_AVAILABLE:
            options = RGBMatrixOptions()
            options.rows = 32
            options.cols = 64
            options.led_rgb_sequence = "RBG"
            options.brightness = 75
            options.disable_hardware_pulsing = True
            options.hardware_mapping = "regular"
            self.matrix = RGBMatrix(options=options)
            self.eyes = EyeManager(self.matrix, GIF_DIR, style=style)
            self.eyes.start()
            self.get_logger().info(f"Matrice LED initialisée (style {style})")
        else:
            self.get_logger().warn("Matrice indisponible — les messages seront seulement journalisés")

        self.create_subscription(String, "/led_expression", self.on_expression, 10)
        self.get_logger().info("led_node démarré — écoute /led_expression")

    def on_expression(self, msg: String):
        cmd = msg.data.strip()
        self.get_logger().info(f"/led_expression : {cmd}")

        if not self.eyes:
            return  # mode PC : rien à piloter

        # ── style:N ──
        if cmd.startswith("style:"):
            try:
                style = int(cmd.split(":", 1)[1])
                self.eyes.set_style(style)
            except ValueError:
                self.get_logger().warn(f"Style invalide : {cmd}")
            return

        # ── idle:nom ──
        if cmd.startswith("idle:"):
            anim = cmd.split(":", 1)[1]
            if anim in KNOWN_ANIMATIONS:
                self.eyes.set_idle(anim)
            else:
                self.get_logger().warn(f"Idle inconnu : {anim}")
            return

        # ── animation simple ──
        if cmd in KNOWN_ANIMATIONS:
            self.eyes.play(cmd)
        else:
            self.get_logger().warn(f"Animation inconnue : {cmd}")

    def destroy_node(self):
        if self.eyes:
            self.eyes.stop()
        if MATRIX_AVAILABLE and self.matrix:
            self.matrix.Clear()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = LedNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
