#!/usr/bin/env python3
"""
firmware_node.py — Pont ROS2 <-> Arduino Mega pour MARC.

Reprend la logique du serial_worker de web/server.py, exposée en ROS2 :

  ENTRÉES (souscriptions)
    /cmd_motor   std_msgs/Int32MultiArray  -> [move, turn]  =>  série "C:move:turn"
    /line_mode   std_msgs/Bool             -> True/False     =>  série "M:1" / "M:0"

  SORTIES (publications)
    /station     std_msgs/Int32            <-  série "S:n"
    /imu_yaw     std_msgs/Float32          <-  série "Y:yaw"   (degrés)
    /distance    std_msgs/Float32          <-  série "D:dist"  (cm cumulés)
    /obstacles   std_msgs/Float32MultiArray <- série "U:c:g:d" (cm) [centre, gauche, droite]

Note : on garde volontairement des types std_msgs simples pour ce premier nœud.
Les types robotiques propres (Odometry, LaserScan, Twist + TF) viendront quand
on branchera l'odométrie et le LiDAR/Nav2.
"""

import threading
from collections import deque
import statistics

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int32, Float32, Int32MultiArray, Float32MultiArray

import serial


# ─────────────────────────────────────────────
#  PARAMÈTRES (surchageables au lancement)
# ─────────────────────────────────────────────
DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 115200


class FirmwareNode(Node):
    def __init__(self):
        super().__init__("firmware_node")

        # Paramètres ROS2 (modifiables : --ros-args -p port:=/dev/ttyACM1)
        self.declare_parameter("port", DEFAULT_PORT)
        self.declare_parameter("baud", DEFAULT_BAUD)
        port = self.get_parameter("port").get_parameter_value().string_value
        baud = self.get_parameter("baud").get_parameter_value().integer_value

        # État moteur courant (envoyé en continu à l'Arduino, comme serial_worker)
        self.current_move = 0
        self.current_turn = 0

        # Filtrage médian des ultrasons latéraux (repris de server.py)
        self._us_gauche_buf = deque(maxlen=10)
        self._us_droite_buf = deque(maxlen=10)

        # ── Connexion série ──
        self.serial_ok = False
        self.arduino = None
        try:
            self.arduino = serial.Serial(port, baud, timeout=2)
            # Laisse l'Arduino redémarrer après ouverture du port
            self.create_rate(1).sleep() if False else None
            import time
            time.sleep(2)
            self.arduino.reset_input_buffer()
            self.serial_ok = True
            self.get_logger().info(f"[Arduino] Connecté sur {port} @ {baud}")
        except Exception as e:
            self.get_logger().warn(f"[Arduino] Non connecté (ignoré) : {e}")

        # ── Publishers ──
        self.pub_station   = self.create_publisher(Int32, "/station", 10)
        self.pub_yaw       = self.create_publisher(Float32, "/imu_yaw", 10)
        self.pub_distance  = self.create_publisher(Float32, "/distance", 10)
        self.pub_obstacles = self.create_publisher(Float32MultiArray, "/obstacles", 10)

        # ── Subscribers ──
        self.create_subscription(Int32MultiArray, "/cmd_motor", self.on_cmd_motor, 10)
        self.create_subscription(Bool, "/line_mode", self.on_line_mode, 10)

        # ── Boucle série dans un thread dédié (comme serial_worker) ──
        self._buffer = ""
        self._last_station = None
        self._running = True
        self._thread = threading.Thread(target=self._serial_loop, daemon=True)
        self._thread.start()

        self.get_logger().info("firmware_node démarré")

    # ─────────────────────────────────────────
    #  CALLBACKS (entrées ROS2 -> série)
    # ─────────────────────────────────────────
    def on_cmd_motor(self, msg: Int32MultiArray):
        """Reçoit [move, turn] et met à jour la commande courante."""
        if len(msg.data) >= 2:
            self.current_move = int(msg.data[0])
            self.current_turn = int(msg.data[1])

    def on_line_mode(self, msg: Bool):
        """Active/désactive le suivi de ligne côté Arduino (M:1 / M:0)."""
        if not self.serial_ok:
            return
        try:
            self.arduino.write(f"M:{'1' if msg.data else '0'}\n".encode())
            self.get_logger().info(f"Mode ligne : {'ON' if msg.data else 'OFF'}")
        except Exception as e:
            self.get_logger().error(f"Erreur envoi mode : {e}")

    # ─────────────────────────────────────────
    #  BOUCLE SÉRIE (série -> ROS2)
    # ─────────────────────────────────────────
    def _serial_loop(self):
        import time
        while self._running and rclpy.ok():
            if not self.serial_ok:
                time.sleep(0.1)
                continue
            try:
                # 1. Envoie la commande moteur (comme serial_worker)
                cmd = f"C:{self.current_move}:{self.current_turn}\n"
                self.arduino.write(cmd.encode())

                # 2. Lit les réponses (non bloquant)
                if self.arduino.in_waiting:
                    chunk = self.arduino.read(self.arduino.in_waiting).decode(errors="ignore")
                    self._buffer += chunk
                    while "\n" in self._buffer:
                        line, self._buffer = self._buffer.split("\n", 1)
                        self._handle_line(line.strip())
            except Exception as e:
                self.get_logger().error(f"Serial error : {e}")

            time.sleep(0.005)  # ~200 Hz, identique à server.py

    def _handle_line(self, line: str):
        if not line:
            return

        if line.startswith("S:"):
            try:
                station = int(line[2:])
                if station != self._last_station:
                    self._last_station = station
                    self.pub_station.publish(Int32(data=station))
                    self.get_logger().info(f"Station : {station}")
            except ValueError:
                pass

        elif line.startswith("Y:"):
            try:
                self.pub_yaw.publish(Float32(data=float(line[2:])))
            except ValueError:
                pass

        elif line.startswith("D:"):
            try:
                self.pub_distance.publish(Float32(data=float(line[2:])))
            except ValueError:
                pass

        elif line.startswith("U:"):
            try:
                p = line[2:].split(":")
                centre = float(p[0])
                raw_g = float(p[1])
                raw_d = float(p[2])
                self._us_gauche_buf.append(raw_g)
                self._us_droite_buf.append(raw_d)
                gauche = statistics.median(self._us_gauche_buf)
                droite = statistics.median(self._us_droite_buf)
                msg = Float32MultiArray()
                msg.data = [centre, gauche, droite]
                self.pub_obstacles.publish(msg)
            except (ValueError, IndexError):
                pass

    def destroy_node(self):
        self._running = False
        if self.arduino and self.serial_ok:
            try:
                self.arduino.write(b"C:0:0\n")  # stop moteurs en sortie
            except Exception:
                pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = FirmwareNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
