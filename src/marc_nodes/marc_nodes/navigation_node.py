#!/usr/bin/env python3
"""
navigation_node.py — Contrôleur go-to-goal pur de MARC (ROS2).

Ne fait plus qu'UNE chose : amener MARC à un point (x, y) dans le repère salle,
par correction de cap puis avance, en s'appuyant sur la pose de localization_node.

  Plus de visual servoing ArUco, plus d'évitement d'obstacles : la sécurité de
  trajectoire est assurée en amont par mission_node, qui découpe chaque trajet
  en points de passage (waypoints) tracés hors-ligne pour contourner les
  obstacles fixes connus.

  SOUSCRIT
    /nav_goal      geometry_msgs/Pose2D  (x, y, theta) — point + cap final.
                   theta = NaN → pas de contrainte de cap (waypoint de passage)
    /pose          std_msgs/String       (JSON {x, y, heading, source})
    /goal_cancel   std_msgs/Empty        annule le déplacement en cours
    /nav_localize  std_msgs/Empty        scan de localisation (rotation) jusqu'à
                                          acquérir la pose au démarrage à froid

  PUBLIE
    /cmd_motor     std_msgs/Int32MultiArray  [move, turn]   (vers firmware_node)
    /goal_status   std_msgs/String           (JSON {event, target})
                   event ∈ {start, arrived, lost, cancelled}

La boucle de contrôle est bloquante : elle tourne dans un thread piloté par le
dernier point reçu. Un nouveau point ou un /goal_cancel interrompt la boucle.
"""

import json
import math
import time
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32MultiArray, Empty
from geometry_msgs.msg import Pose2D


# ── Paramètres go-to-goal ──
GOAL_TOLERANCE_M = 0.25     # distance à laquelle on considère le point atteint
ANGLE_TOLERANCE  = 15       # au-delà : rotation sur place avant d'avancer (deg)
HEADING_TOLERANCE = 8       # tolérance sur le cap final à l'arrivée (deg)
KP_TURN          = 3.0      # gain proportionnel de correction de cap
TURN_SPEED       = 25       # consigne de rotation sur place
FORWARD_SPEED    = 300      # consigne d'avance
MAX_LOST         = 20       # cycles sans pose avant d'abandonner
SCAN_TURN_SPEED  = 25       # vitesse de rotation du scan de localisation
SCAN_TIMEOUT_S   = 40       # abandon du scan si aucun marqueur trouvé (s)


class NavigationNode(Node):
    def __init__(self):
        super().__init__("navigation_node")

        self.pose = None
        self._cancel = threading.Event()
        self._nav_thread = None
        self._lock = threading.Lock()

        self.create_subscription(Pose2D, "/nav_goal",    self.on_goal,   10)
        self.create_subscription(String, "/pose",        self.on_pose,   10)
        self.create_subscription(Empty,  "/goal_cancel", self.on_cancel, 10)
        self.create_subscription(Empty,  "/nav_localize", self.on_localize, 10)

        self.pub_cmd    = self.create_publisher(Int32MultiArray, "/cmd_motor",   10)
        self.pub_status = self.create_publisher(String,          "/goal_status", 10)

        self.get_logger().info("navigation_node démarré — go-to-goal pur, en attente de /nav_goal")

    # ── Callbacks ──
    def on_pose(self, msg: String):
        try:
            self.pose = json.loads(msg.data)
        except json.JSONDecodeError:
            self.pose = None

    def on_cancel(self, _msg):
        self._stop_current()
        self.stop()
        self.notify("cancelled", None)
        self.get_logger().info("Déplacement annulé (/goal_cancel)")

    def on_goal(self, msg: Pose2D):
        """Reçoit un objectif (x, y, theta) et (re)lance la boucle go-to-goal.
        theta en radians ; NaN → pas de contrainte de cap final."""
        self._stop_current()
        self._cancel.clear()
        gx, gy = float(msg.x), float(msg.y)
        gh_deg = math.degrees(float(msg.theta))
        goal_heading = None if math.isnan(gh_deg) else gh_deg
        with self._lock:
            self._nav_thread = threading.Thread(
                target=self.go_to_xy, args=(gx, gy, goal_heading), daemon=True)
            self._nav_thread.start()

    def on_localize(self, _msg):
        """Lance un scan de localisation : rotation sur place jusqu'à acquérir la pose."""
        self._stop_current()
        self._cancel.clear()
        with self._lock:
            self._nav_thread = threading.Thread(target=self.localize_scan, daemon=True)
            self._nav_thread.start()

    def _stop_current(self):
        """Interrompt proprement la boucle en cours, le cas échéant."""
        t = self._nav_thread
        if t and t.is_alive():
            self._cancel.set()
            t.join(timeout=1.0)
        self._cancel.clear()

    # ── Helpers ──
    def send_motor(self, move, turn):
        m = Int32MultiArray()
        m.data = [int(move), int(turn)]
        self.pub_cmd.publish(m)

    def stop(self):
        self.send_motor(0, 0)

    def notify(self, event, target):
        m = String()
        m.data = json.dumps({"event": event, "target": target})
        self.pub_status.publish(m)

    @staticmethod
    def angle_diff(target, current):
        return (target - current + 180) % 360 - 180

    # ── Scan de localisation (démarrage à froid) ──
    def localize_scan(self):
        """Tourne sur place par à-coups jusqu'à ce qu'une pose soit disponible.

        Avant le premier recalage vision, localization_node ne publie rien : la
        rotation balaie la salle jusqu'à ce qu'un marqueur entre dans le champ.
        On tourne par pas avec une pause, pour laisser la détection ArUco se faire
        sans flou de mouvement. Idempotent : si la pose est déjà connue, retour
        immédiat.
        """
        self.get_logger().info("Localisation : scan jusqu'à acquisition de la pose")
        start = time.time()
        while rclpy.ok() and not self._cancel.is_set():
            if self.pose is not None:
                self.stop()
                self.get_logger().info("Localisation : pose acquise")
                self.notify("localized", None)
                return
            if time.time() - start > SCAN_TIMEOUT_S:
                self.stop()
                self.get_logger().warn("Localisation : aucun marqueur trouvé (timeout)")
                self.notify("lost", None)
                return
            self.send_motor(0, SCAN_TURN_SPEED)
            time.sleep(0.35)
            self.stop()
            time.sleep(0.4)
        self.stop()

    # ── Boucle go-to-goal ──
    def go_to_xy(self, goal_x, goal_y, goal_heading=None):
        tag = f"({goal_x:.2f},{goal_y:.2f})"
        self.get_logger().info(f"Go-to-goal {tag}"
                               + (f" cap {goal_heading:.0f}°" if goal_heading is not None else ""))
        self.notify("start", tag)
        lost = 0

        # ── Phase 1 : rejoindre la position (x, y) ──
        while rclpy.ok() and not self._cancel.is_set():
            if self.pose is None:
                lost += 1
                if lost > MAX_LOST:
                    self.get_logger().warn("Localisation perdue, arrêt")
                    self.stop()
                    self.notify("lost", tag)
                    return
                time.sleep(0.05)
                continue
            lost = 0

            x, y, heading = self.pose["x"], self.pose["y"], self.pose["heading"]
            dx, dy = goal_x - x, goal_y - y
            distance = math.hypot(dx, dy)

            if distance < GOAL_TOLERANCE_M:
                self.stop()
                self.get_logger().info(f"Position {tag} atteinte ({distance:.2f} m)")
                break

            bearing = math.degrees(math.atan2(dx, -dy))
            err = self.angle_diff(bearing, heading)

            if abs(err) > ANGLE_TOLERANCE:
                self.send_motor(0, TURN_SPEED if err > 0 else -TURN_SPEED)
            else:
                turn = max(-60, min(60, int(KP_TURN * err)))
                self.send_motor(FORWARD_SPEED, turn)

            time.sleep(0.1)

        if self._cancel.is_set() or not rclpy.ok():
            self.stop()
            return

        # ── Phase 2 : s'orienter vers le cap final (si demandé) ──
        if goal_heading is not None:
            self.get_logger().info(f"Orientation finale vers {goal_heading:.0f}°")
            lost = 0
            while rclpy.ok() and not self._cancel.is_set():
                if self.pose is None:
                    lost += 1
                    if lost > MAX_LOST:
                        self.get_logger().warn("Localisation perdue pendant l'orientation")
                        self.stop()
                        self.notify("lost", tag)
                        return
                    time.sleep(0.05)
                    continue
                lost = 0

                err = self.angle_diff(goal_heading, self.pose["heading"])
                if abs(err) < HEADING_TOLERANCE:
                    self.stop()
                    self.get_logger().info(f"Cap final atteint ({self.pose['heading']:.0f}°)")
                    break
                self.send_motor(0, TURN_SPEED if err > 0 else -TURN_SPEED)
                time.sleep(0.1)

            if self._cancel.is_set() or not rclpy.ok():
                self.stop()
                return

        self.stop()
        self.notify("arrived", tag)


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
