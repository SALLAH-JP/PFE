#!/usr/bin/env python3
"""
navigation_node.py — Navigation spatiale de MARC en nœud ROS2.

Fusionne les deux stratégies existantes :
  - vision/navigation_service.py : visual servoing vers un marqueur ArUco (scan + approche)
  - vision/navigation_goto.py     : go-to-goal (x,y) vers des coordonnées de la salle

  SOUSCRIT
    /aruco/detections   std_msgs/String         (JSON, depuis camera_node)
    /pose               std_msgs/String         (JSON, depuis localization_node)
    /nav_goal           std_msgs/String         (JSON déclencheur, voir plus bas)

  PUBLIE
    /cmd_motor          std_msgs/Int32MultiArray  [move, turn]   (vers firmware_node)
    /nav_status         std_msgs/String           (JSON {event, target})

Note : l'évitement d'obstacles par ultrasons a été retiré (capteurs jugés
trop instables). L'évitement se fait désormais par marqueurs ArUco posés
sur les obstacles statiques (voir OBSTACLES_MAP ci-dessous) — positions
connues à l'avance, simple déviation latérale quand un obstacle se trouve
sur la trajectoire.

Déclencheur /nav_goal (JSON) :
    {"mode": "aruco", "id": 3}              → visual servoing vers le marqueur ID 3
    {"mode": "goto",  "x": 1.5, "y": 0.8}   → go-to-goal vers (x, y)

Note : la navigation est une logique séquentielle bloquante (scan, approche,
boucles de contrôle). Elle tourne dans un thread piloté par le dernier goal reçu,
pour ne pas figer le spin ROS2.
"""

import json
import math
import time
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32MultiArray

from marc_nodes.maps import obstacle_positions


# ── Paramètres ArUco (repris de navigation_service.py) ──
KNOWN_TARGETS = [0, 1, 2, 3, 4, 5, 6]
STOP_DISTANCE_M = 0.75
SCAN_TIMEOUT_S = 40
ANGLE_OFFSET = -5.5
TURN_SPEED = 45
FORWARD_SPEED = 80

# ── Paramètres go-to-goal (repris de navigation_goto.py) ──
GOAL_TOLERANCE_M = 0.25
ANGLE_TOLERANCE = 15
KP_TURN = 3.0
MAX_LOST = 20

# ── Évitement d'obstacles par marqueurs ArUco ──
# La carte des obstacles est définie dans maps.py (partagée avec localization_node,
# qui utilise les mêmes marqueurs pour recaler la pose vision).
# Convention d'IDs :
#   0-9   : stations (destinations)
#   10-49 : marqueurs de localisation pure
#   50-99 : obstacles fixes (meubles, machines) — servent aussi à la localisation
OBSTACLE_RADIUS_M = 0.5    # distance à laquelle on commence à dévier
OBSTACLE_CONE_DEG = 60     # demi-angle du cône devant le robot (au-delà, on ignore)
OBSTACLE_AVOID_DEG = 30    # intensité de la déviation (degrés vers le côté opposé)


class NavigationNode(Node):
    def __init__(self):
        super().__init__("navigation_node")

        # Dernières valeurs reçues
        self.detections = {}
        self.pose = None

        self.create_subscription(String, "/aruco/detections", self.on_detections, 10)
        self.create_subscription(String, "/pose", self.on_pose, 10)
        self.create_subscription(String, "/nav_goal", self.on_goal, 10)

        self.pub_cmd = self.create_publisher(Int32MultiArray, "/cmd_motor", 10)
        self.pub_status = self.create_publisher(String, "/nav_status", 10)

        self._nav_thread = None
        self.get_logger().info("navigation_node démarré — en attente de /nav_goal")

    # ── Callbacks ──
    def on_detections(self, msg: String):
        try:
            self.detections = json.loads(msg.data)
        except json.JSONDecodeError:
            self.detections = {}

    def on_pose(self, msg: String):
        try:
            self.pose = json.loads(msg.data)
        except json.JSONDecodeError:
            self.pose = None

    def on_goal(self, msg: String):
        """Reçoit un objectif et lance la navigation dans un thread."""
        if self._nav_thread and self._nav_thread.is_alive():
            self.get_logger().warn("Navigation déjà en cours — goal ignoré")
            return
        try:
            goal = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"/nav_goal JSON invalide : {msg.data}")
            return

        mode = goal.get("mode")
        if mode == "aruco":
            target = goal.get("id")
            self._nav_thread = threading.Thread(
                target=self.go_to_target, args=(target,), daemon=True)
            self._nav_thread.start()
        elif mode == "goto":
            x, y = goal.get("x"), goal.get("y")
            self._nav_thread = threading.Thread(
                target=self.go_to_xy, args=(x, y), daemon=True)
            self._nav_thread.start()
        else:
            self.get_logger().warn(f"Mode de navigation inconnu : {mode}")

    # ── Helpers moteur / status ──
    def send_motor(self, move, turn):
        msg = Int32MultiArray()
        msg.data = [int(move), int(turn)]
        self.pub_cmd.publish(msg)

    def stop(self):
        self.send_motor(0, 0)

    def notify(self, event, target):
        msg = String()
        msg.data = json.dumps({"event": event, "target": target})
        self.pub_status.publish(msg)

    @staticmethod
    def angle_diff(target, current):
        return (target - current + 180) % 360 - 180

    # ═════════════════════════════════════════
    #  STRATÉGIE 1 : Visual servoing ArUco (navigation_service.py)
    # ═════════════════════════════════════════
    def scan_for_target(self, target_id):
        self.get_logger().info(f"Recherche cible {target_id}")
        start = time.time()
        while time.time() - start < SCAN_TIMEOUT_S:
            if str(target_id) in self.detections:
                self.stop()
                self.get_logger().info(f"Cible {target_id} trouvée")
                return True
            self.send_motor(0, -TURN_SPEED)
            time.sleep(0.35)
            self.stop()
            time.sleep(0.4)
        self.get_logger().warn(f"Timeout : cible {target_id} introuvable")
        return False

    def approach_target(self, target_id):
        self.get_logger().info(f"Approche cible {target_id}")
        lost = 0
        MAX_LOST_LOCAL = 8
        KP = 4.0
        while True:
            if str(target_id) not in self.detections:
                lost += 1
                if lost > MAX_LOST_LOCAL:
                    self.get_logger().warn("Cible perdue")
                    self.stop()
                    return False
                time.sleep(0.05)
                continue
            lost = 0
            d = self.detections[str(target_id)]
            distance = d.get("distance")
            angle = d.get("angle")
            if distance is None or angle is None:
                self.stop()
                return False
            angle = angle - ANGLE_OFFSET
            if distance < STOP_DISTANCE_M:
                self.stop()
                self.get_logger().info(f"Arrivé ({distance:.2f}m)")
                return True
            turn = int(-KP * angle)
            turn = max(-120, min(120, turn))
            if abs(angle) > 5:
                self.send_motor(0, turn)
            else:
                self.send_motor(FORWARD_SPEED, turn)
            time.sleep(0.08)

    def go_to_target(self, target_id):
        if target_id not in KNOWN_TARGETS:
            self.get_logger().warn(f"ID {target_id} inconnu")
            return
        self.notify("start", target_id)
        if not self.scan_for_target(target_id):
            self.notify("lost", target_id)
            self.stop()
            return
        arrived = self.approach_target(target_id)
        self.stop()
        self.notify("arrived" if arrived else "lost", target_id)

    # ═════════════════════════════════════════
    #  STRATÉGIE 2 : Go-to-goal (x,y) avec évitement (navigation_goto.py)
    # ═════════════════════════════════════════
    def go_to_xy(self, goal_x, goal_y):
        self.get_logger().info(f"Go-to-goal ({goal_x}, {goal_y})")
        self.notify("start", f"({goal_x},{goal_y})")
        lost = 0

        while True:
            if self.pose is None:
                lost += 1
                if lost > MAX_LOST:
                    self.get_logger().warn("Localisation perdue, arrêt")
                    self.stop()
                    self.notify("lost", f"({goal_x},{goal_y})")
                    return
                time.sleep(0.05)
                continue
            lost = 0

            x, y, heading = self.pose["x"], self.pose["y"], self.pose["heading"]
            dx, dy = goal_x - x, goal_y - y
            distance = math.hypot(dx, dy)

            if distance < GOAL_TOLERANCE_M:
                self.stop()
                self.get_logger().info(f"Arrivé ({distance:.2f}m)")
                self.notify("arrived", f"({goal_x},{goal_y})")
                return

            # Navigation directe vers la cible avec correction de cap
            bearing = math.degrees(math.atan2(dx, -dy))
            err = self.angle_diff(bearing, heading)

            # ── Évitement d'obstacles ArUco ──
            # Pour chaque obstacle dans la carte partagée, on regarde s'il est
            # devant nous (dans le cône) et assez proche. Si oui, on dévie
            # du côté opposé.
            side_avoidance = 0
            closest_dist = float("inf")
            for obs_id, (ox, oy) in obstacle_positions().items():
                dist_to_obs = math.hypot(ox - x, oy - y)
                if dist_to_obs > OBSTACLE_RADIUS_M + 0.3:
                    continue
                # Angle de l'obstacle par rapport au cap actuel
                obs_bearing = math.degrees(math.atan2(ox - x, -(oy - y)))
                obs_angle_rel = self.angle_diff(obs_bearing, heading)
                # Devant nous (dans le cône) ?
                if abs(obs_angle_rel) < OBSTACLE_CONE_DEG and dist_to_obs < closest_dist:
                    closest_dist = dist_to_obs
                    # Dévie du côté opposé à l'obstacle
                    side_avoidance = OBSTACLE_AVOID_DEG if obs_angle_rel < 0 else -OBSTACLE_AVOID_DEG

            if abs(err) > ANGLE_TOLERANCE:
                # Trop désaligné : rotation sur place
                turn = TURN_SPEED if err > 0 else -TURN_SPEED
                self.send_motor(0, turn)
            else:
                # Aligné : avance avec correction proportionnelle + déviation obstacle
                turn = max(-60, min(60, int(KP_TURN * err) + side_avoidance))
                self.send_motor(FORWARD_SPEED, turn)

            time.sleep(0.1)


def main(args=None):
    rclpy.init(args=args)
    node = NavigationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
