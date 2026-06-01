#!/usr/bin/env python3
"""
localization_node.py — Localisation de MARC en nœud ROS2.

Porté depuis vision/localization.py. Calcule la position (x, y, cap) du robot
par fusion :
  - position absolue quand un marqueur ArUco connu est visible (recalage)
  - intégration odométrique sinon (cap IMU + distance parcourue)

  SOUSCRIT
    /aruco/detections   std_msgs/String    (JSON, depuis camera_node)
    /imu_yaw            std_msgs/Float32    (cap brut BNO085, depuis firmware_node)
    /distance           std_msgs/Float32    (distance cumulée cm, depuis firmware_node)

  PUBLIE
    /pose               std_msgs/String     (JSON {x, y, heading, source})

Convention salle (reprise de localization.py) :
  origine = intersection de carreaux ; 0° = -Y ; +90° = +X ; sens trigo.
"""

import json
import math

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Float32

from marc_nodes.maps import all_positions


# Carte des marqueurs (positions x, y en mètres) — chargée depuis maps.py
# Contient tous les marqueurs fixes connus, y compris les obstacles
# (les obstacles servent aussi à recaler la pose vision).
MARKERS_MAP = all_positions()

# Offset IMU : yaw brut quand MARC est face à -Y (cap 0° salle)
YAW_OFFSET = -5.5


class LocalizationNode(Node):
    def __init__(self):
        super().__init__("localization_node")

        # Dernières valeurs reçues
        self.detections = {}
        self.yaw_brut = None
        self.distance_cum = None

        # État de fusion (repris de _fused dans localization.py)
        self.fx = 0.0
        self.fy = 0.0
        self.fheading = 0.0
        self.last_distance = None
        self.valid = False

        self.create_subscription(String, "/aruco/detections", self.on_detections, 10)
        self.create_subscription(Float32, "/imu_yaw", self.on_yaw, 10)
        self.create_subscription(Float32, "/distance", self.on_distance, 10)

        self.pub_pose = self.create_publisher(String, "/pose", 10)

        # Recalcule et publie la pose à 10 Hz
        self.create_timer(0.1, self.update_and_publish)

        self.get_logger().info("localization_node démarré")

    # ── Callbacks ──
    def on_detections(self, msg: String):
        try:
            self.detections = json.loads(msg.data)
        except json.JSONDecodeError:
            self.detections = {}

    def on_yaw(self, msg: Float32):
        self.yaw_brut = msg.data

    def on_distance(self, msg: Float32):
        self.distance_cum = msg.data

    # ── Conversion du cap (reprise de heading_salle) ──
    def heading_salle(self, yaw_brut):
        h = yaw_brut - YAW_OFFSET
        return (h + 180) % 360 - 180

    # ── Localisation par vision (reprise de localize) ──
    def localize_vision(self, heading):
        positions = []
        for mid_str, data in self.detections.items():
            mid = int(mid_str)
            if mid not in MARKERS_MAP:
                continue
            d = data.get("distance")
            a = data.get("angle")
            if d is None or a is None:
                continue
            mx, my = MARKERS_MAP[mid]
            bearing = heading - a
            rad = math.radians(bearing)
            marc_x = mx - d * math.sin(rad)
            marc_y = my + d * math.cos(rad)
            positions.append((marc_x, marc_y))

        if not positions:
            return None
        avg_x = sum(p[0] for p in positions) / len(positions)
        avg_y = sum(p[1] for p in positions) / len(positions)
        return avg_x, avg_y

    # ── Fusion vision + odométrie (reprise de localize_fused) ──
    def update_and_publish(self):
        if self.yaw_brut is None:
            return
        heading = self.heading_salle(self.yaw_brut)

        # 1. Tentative vision (recalage absolu)
        vision = self.localize_vision(heading)
        if vision is not None:
            self.fx, self.fy = vision
            self.fheading = heading
            self.last_distance = self.distance_cum
            self.valid = True
            self._publish(self.fx, self.fy, heading, "vision")
            return

        # 2. Odométrie seule
        if not self.valid or self.last_distance is None or self.distance_cum is None:
            return
        delta_m = (self.distance_cum - self.last_distance) / 100.0  # cm → m
        rad = math.radians(heading)
        self.fx += delta_m * math.sin(rad)
        self.fy += delta_m * (-math.cos(rad))
        self.fheading = heading
        self.last_distance = self.distance_cum
        self._publish(self.fx, self.fy, heading, "odometry")

    def _publish(self, x, y, heading, source):
        msg = String()
        msg.data = json.dumps({
            "x": round(x, 3),
            "y": round(y, 3),
            "heading": round(heading, 1),
            "source": source,
        })
        self.pub_pose.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = LocalizationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
