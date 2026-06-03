#!/usr/bin/env python3
"""
mission_node.py — Résolveur de destination + parcours de MARC (ROS2).

C'est le « cerveau de trajet ». Il sépare deux choses que navigation_node ne
veut plus connaître : QUOI atteindre (une station nommée, ou un point brut) et
PAR OÙ passer (la suite de points de passage qui contourne les obstacles fixes).

  SOUSCRIT
    /mission_station  std_msgs/String        nom de station ("pepper", "nao"…)
    /mission_point    geometry_msgs/Point    point brut (x, y) — trajet direct
    /goal_cancel ?    non : on écoute /nav_cancel pour annuler une mission
    /nav_cancel       std_msgs/Empty         annule la mission en cours
    /goal_status      std_msgs/String        (JSON {event,target}) retour bas niveau
                      depuis navigation_node — sert à enchaîner les waypoints
    /pose             std_msgs/String        (JSON {x,y,...}) position courante,
                      pour planifier le chemin depuis là où se trouve MARC

  PUBLIE
    /nav_goal     geometry_msgs/Pose2D  un waypoint (x,y,theta) à la fois.
                  theta porté seulement sur le DERNIER waypoint (cap final).
    /goal_cancel  std_msgs/Empty        coupe le déplacement bas niveau
    /nav_status   std_msgs/String       (JSON {event,target}) statut MISSION pour l'UI
                  event ∈ {start, arrived, lost, cancelled}

Fonctionnement : à la réception d'une destination, on construit une liste de
waypoints. On publie le premier sur /nav_goal. Chaque fois que navigation_node
signale "arrived" sur /goal_status, on envoie le waypoint suivant. Au dernier,
on émet "arrived" niveau mission. Tout est événementiel (pas de boucle bloquante).

Le chemin est planifié (Dijkstra) sur le graphe de points sûrs WAYPOINTS/EDGES
de maps.py, depuis la position courante de MARC jusqu'à la cible. Vaut pour une
station (cible = STATION_POS) comme pour un point libre (/mission_point). Si le
graphe est vide ou la pose inconnue, on retombe sur un trajet direct.
"""

import json
import math
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Empty
from geometry_msgs.msg import Point, Pose2D

from marc_nodes.maps import plan_path, station_pos, station_heading, STATION_POS


class MissionNode(Node):
    def __init__(self):
        super().__init__("mission_node")

        self._lock = threading.Lock()
        self._route: list[tuple[float, float]] = []
        self._idx = 0
        self._target_label = None      # pour le statut UI ("pepper" ou "(x,y)")
        self._final_heading = None     # cap final en degrés, ou None
        self._pose = None              # dernière position connue de MARC
        self._pending = None           # (goal_xy, label, heading) en attente de pose
        self._localizing = False       # scan de localisation en cours
        self._active = False

        self.create_subscription(String, "/mission_station", self.on_station, 10)
        self.create_subscription(Point,  "/mission_point",   self.on_point,   10)
        self.create_subscription(Empty,  "/nav_cancel",      self.on_cancel,  10)
        self.create_subscription(String, "/goal_status",     self.on_goal_status, 10)
        self.create_subscription(String, "/pose",            self.on_pose,        10)

        self.pub_goal   = self.create_publisher(Pose2D, "/nav_goal",    10)
        self.pub_cancel = self.create_publisher(Empty,  "/goal_cancel", 10)
        self.pub_status = self.create_publisher(String, "/nav_status",  10)
        self.pub_localize = self.create_publisher(Empty, "/nav_localize", 10)

        self.get_logger().info("mission_node démarré — en attente de destination")

    # ── Pose courante ──
    def on_pose(self, msg: String):
        try:
            d = json.loads(msg.data)
            if d.get("x") is not None:
                self._pose = (float(d["x"]), float(d["y"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            return
        # Si on attendait une pose pour planifier (démarrage à froid), c'est le moment.
        if self._localizing and self._pending is not None and self._pose is not None:
            with self._lock:
                pending = self._pending
                self._pending = None
                self._localizing = False
            goal_xy, label, heading = pending
            self.get_logger().info("Pose acquise — planification du trajet différé")
            self.start_mission(self.plan_route_to(goal_xy), label, heading)

    # ── Aiguillage : planifier tout de suite, ou localiser d'abord ──
    def request_goal(self, goal_xy, label, heading):
        if self._pose is not None:
            self.start_mission(self.plan_route_to(goal_xy), label, heading)
            return
        # Pas encore de pose (démarrage à froid) : on lance un scan de
        # localisation, et on_pose reprendra la planification dès qu'elle arrive.
        self.get_logger().info("Pose inconnue — localisation avant le trajet")
        with self._lock:
            self._pending = (goal_xy, label, heading)
            self._localizing = True
        self.notify("start", label)
        self.pub_localize.publish(Empty())

    # ── Planification d'un trajet vers une cible (x, y) ──
    def plan_route_to(self, goal_xy):
        """Construit la liste de waypoints jusqu'à goal_xy en passant par le
        graphe de points sûrs, depuis la position courante de MARC. Retombe sur
        un trajet direct si la pose est inconnue ou si le graphe ne couvre pas."""
        if self._pose is None:
            self.get_logger().warn("Pose courante inconnue — trajet direct")
            return [goal_xy]
        nodes = plan_path(self._pose, goal_xy)
        if not nodes:
            self.get_logger().warn("Aucun chemin dans le graphe — trajet direct")
            return [goal_xy]
        return nodes + [goal_xy]

    # ── Réception d'une destination ──
    def on_station(self, msg: String):
        name = msg.data.strip().lower()
        if name not in STATION_POS:
            self.get_logger().warn(f"Station inconnue : {name}")
            self.notify("lost", name)
            return
        pos = station_pos(name)
        if pos is None:
            self.get_logger().warn(
                f"Position de '{name}' non définie (à mesurer dans maps.py)")
            self.notify("lost", name)
            return
        self.request_goal(pos, name, station_heading(name))

    def on_point(self, msg: Point):
        goal = (float(msg.x), float(msg.y))
        label = f"({msg.x:.2f},{msg.y:.2f})"
        self.request_goal(goal, label, None)

    def on_cancel(self, _msg):
        with self._lock:
            self._active = False
            self._route = []
            self._idx = 0
        self.pub_cancel.publish(Empty())   # coupe le déplacement bas niveau
        self.notify("cancelled", self._target_label)
        self.get_logger().info("Mission annulée")

    # ── Enchaînement des waypoints ──
    def start_mission(self, route, label, final_heading=None):
        with self._lock:
            self._route = list(route)
            self._idx = 0
            self._target_label = label
            self._final_heading = final_heading
            self._active = True
        self.get_logger().info(f"Mission → {label} : {len(route)} waypoint(s)")
        self.notify("start", label)
        self.send_current_waypoint()

    def send_current_waypoint(self):
        with self._lock:
            if not self._active or self._idx >= len(self._route):
                return
            wx, wy = self._route[self._idx]
            n = len(self._route)
            i = self._idx
            is_last = (i == n - 1)
            heading = self._final_heading
        # Le cap final n'est imposé que sur le dernier waypoint ; sinon NaN
        # (= "pas de contrainte de cap" côté navigation_node).
        theta = math.radians(heading) if (is_last and heading is not None) else float("nan")
        self.get_logger().info(
            f"Waypoint {i + 1}/{n} → ({wx:.2f}, {wy:.2f})"
            + (f" cap {heading:.0f}°" if (is_last and heading is not None) else ""))
        g = Pose2D()
        g.x, g.y, g.theta = float(wx), float(wy), theta
        self.pub_goal.publish(g)

    def on_goal_status(self, msg: String):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        event = data.get("event")

        # Échec du scan de localisation : on abandonne le trajet différé.
        if event == "lost" and self._localizing:
            with self._lock:
                self._pending = None
                self._localizing = False
            self.get_logger().warn("Localisation échouée — trajet annulé")
            self.notify("lost", self._target_label)
            return
        # "localized" : on s'appuie sur /pose côté mission, rien à faire ici.
        if event == "localized":
            return

        with self._lock:
            if not self._active:
                return

        if event == "arrived":
            with self._lock:
                self._idx += 1
                done = self._idx >= len(self._route)
            if done:
                with self._lock:
                    self._active = False
                self.get_logger().info(f"Destination atteinte : {self._target_label}")
                self.notify("arrived", self._target_label)
            else:
                self.send_current_waypoint()

        elif event == "lost":
            with self._lock:
                self._active = False
            self.get_logger().warn(f"Trajet perdu vers {self._target_label}")
            self.notify("lost", self._target_label)

        # "cancelled" / "start" bas niveau : rien à faire au niveau mission.

    def notify(self, event, target):
        m = String()
        m.data = json.dumps({"event": event, "target": target})
        self.pub_status.publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = MissionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
