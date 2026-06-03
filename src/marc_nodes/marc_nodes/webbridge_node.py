#!/usr/bin/env python3
"""
webbridge_node.py — Pont Web <-> ROS2 pour MARC.

Ce nœud sert l'interface web (Flask HTTPS + SSE) et fait l'interprétation
LLM. Toute la navigation passe désormais par /nav_goal (visual servoing
ArUco ou go-to-goal x/y) — le suivi de ligne a été abandonné.

  PUBLIE (commandes vers le robot)
    /cmd_motor       std_msgs/Int32MultiArray  [move, turn]    (pilotage direct)
    /nav_goal        std_msgs/String  (JSON : aruco|goto|stop) (navigation autonome)
    /led_expression  std_msgs/String                           (yeux)

  SOUSCRIT (état du robot)
    /imu_yaw     std_msgs/Float32                 (cap BNO085, debug)
    /distance    std_msgs/Float32                 (distance cumulée, debug)
    /pose        std_msgs/String  (JSON x/y/heading)  (localisation temps réel)
    /nav_status  std_msgs/String  (JSON event/target)  (statut navigation)
    /vocal_command std_msgs/String  (JSON depuis voice_node)

Architecture des threads :
  - rclpy.spin(node)  -> thread principal (ROS2)
  - Flask app.run()   -> thread daemon séparé
"""

import os
import sys
import time
import json
import queue
import socket
import tempfile
import threading
import statistics
from collections import deque

# ─────────────────────────────────────────────
#  ROS2
# ─────────────────────────────────────────────
import rclpy
from rclpy.node import Node
from std_msgs.msg import Empty, Float32, Int32MultiArray, String
from geometry_msgs.msg import Point

# ─────────────────────────────────────────────
#  FLASK
# ─────────────────────────────────────────────
from flask import Flask, request, jsonify, send_from_directory, Response

# ─────────────────────────────────────────────
#  Stations (mapping nom -> ID ArUco) depuis maps.py
# ─────────────────────────────────────────────
from marc_nodes.maps import STATION_POS

# ─────────────────────────────────────────────
#  Imports du projet existant (LLM, TTS, STT, LED)
#  On résout les chemins par rapport à la racine du dépôt MARC.
#  Layout attendu :
#     MARC/
#       ├── src/marc_nodes/marc_nodes/webbridge_node.py   <- CE FICHIER
#       ├── assistantVocale/voiceAssistant.py
#       ├── matrixLed/eye_manager.py
#       └── web/ (index.html, app.js, style.css, cert.pem, key.pem)
# ─────────────────────────────────────────────
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# remonte de src/marc_nodes/marc_nodes/ -> racine MARC/
# (override par MARC_ROOT car colcon installe les fichiers ailleurs)
ROOT = os.environ.get("MARC_ROOT") or os.path.abspath(os.path.join(THIS_DIR, "..", "..", ".."))
WEB_DIR = os.path.join(ROOT, "web")
GIF_DIR = os.path.join(ROOT, "matrixLed")

sys.path.append(os.path.join(ROOT, "assistantVocale"))

from voiceAssistant import speak, ask_ollama, recognizer  # noqa: E402

# La matrice LED n'est PLUS pilotée ici : elle appartient à led_node.
# Le webbridge publie des expressions sur /led_expression (voir set_led).


# ═════════════════════════════════════════════
#  NŒUD ROS2
# ═════════════════════════════════════════════
class WebBridgeNode(Node):
    def __init__(self):
        super().__init__("webbridge_node")

        # ── État robot ──
        self.robot_state = {
            "current": None,       # nom de la dernière station atteinte
            "target":  None,       # nom de la destination visée
            "eyes":    1,
            "mode":    "idle",     # idle / navigating
        }
        # Localisation temps réel (depuis /pose)
        self.pose = {"x": None, "y": None, "heading": None, "source": None}
        # Données capteur de bas niveau (logs/debug, plus utilisées pour l'évitement)
        self.robot_yaw      = 0.0
        self.robot_distance = 0.0

        # ── Mapping noms LLM -> clés stations ──
        # Le LLM peut sortir "Nao", "Pepper", etc. ; on normalise vers
        # les clés utilisées par maps.STATIONS ("nao", "pepper"...).
        self.DESTINATION_MAP = {
            "Nao": "nao", "Vector": "vector", "Pepper": "pepper",
            "Imprimante3D": "imp3d", "Baxter": "baxter",
            "brasRobotique": "bras",
        }

        # ── SSE ──
        self.sse_clients: list[queue.Queue] = []
        self.sse_lock = threading.Lock()

        # ── Publishers ROS2 ──
        # /cmd_motor pour le pilotage manuel (route /motor de Flask).
        # /nav_goal pour déclencher une navigation autonome.
        # /led_expression pour les yeux.
        self.pub_cmd_motor = self.create_publisher(Int32MultiArray, "/cmd_motor", 10)
        # Navigation : on s'adresse à mission_node, jamais directement au contrôleur.
        #   /mission_station : destination nommée (mission_node résout le parcours)
        #   /mission_point   : point brut (x, y) → trajet direct
        #   /nav_cancel      : annule la mission en cours
        self.pub_mission_station = self.create_publisher(String, "/mission_station", 10)
        self.pub_mission_point   = self.create_publisher(Point,  "/mission_point",   10)
        self.pub_nav_cancel      = self.create_publisher(Empty,  "/nav_cancel",      10)
        self.pub_led       = self.create_publisher(String, "/led_expression", 10)
        # /imu_tare : déclenche une re-tare du cap (yaw) côté firmware_node.
        self.pub_imu_tare  = self.create_publisher(Empty, "/imu_tare", 10)
        # /nav_localize : déclenche un scan de localisation (rotation) si besoin.
        self.pub_nav_localize = self.create_publisher(Empty, "/nav_localize", 10)

        # ── Subscribers ROS2 ──
        # Capteurs bas niveau (debug)
        self.create_subscription(Float32, "/imu_yaw",  self.on_yaw, 10)
        self.create_subscription(Float32, "/distance", self.on_distance, 10)
        # Localisation + statut navigation (pour pousser au navigateur en SSE)
        self.create_subscription(String, "/pose",        self.on_pose, 10)
        self.create_subscription(String, "/nav_status",  self.on_nav_status, 10)
        # Commande vocale (depuis voice_node)
        self.create_subscription(String, "/vocal_command", self.on_vocal_command, 10)

        self.get_logger().info("webbridge_node démarré")

    # ─────────────────────────────────────────
    #  HELPERS COMMANDE MOTEUR (remplace l'écriture série)
    # ─────────────────────────────────────────
    def set_motor(self, move: int, turn: int):
        """Publie une commande moteur sur /cmd_motor (vers firmware_node)."""
        msg = Int32MultiArray()
        msg.data = [int(move), int(turn)]
        self.pub_cmd_motor.publish(msg)

    def set_led(self, expression: str):
        """Publie une expression sur /led_expression (vers led_node)."""
        self.pub_led.publish(String(data=expression))

    def set_motor_timed(self, move: int, turn: int, duration):
        """Commande moteur pendant 'duration' secondes puis stop."""
        self.set_motor(move, turn)
        if duration:
            def stop_after():
                time.sleep(float(duration))
                self.set_motor(0, 0)
            threading.Thread(target=stop_after, daemon=True).start()

    # ─────────────────────────────────────────
    #  NAVIGATION — délégation à mission_node
    # ─────────────────────────────────────────
    def nav_goto_station(self, station_key: str) -> bool:
        """Demande une navigation vers une station nommée.
        mission_node résout le parcours (waypoints) et pilote navigation_node."""
        name = station_key.lower()
        if name not in STATION_POS:
            self.get_logger().warn(f"Station inconnue : {station_key}")
            return False
        self.pub_mission_station.publish(String(data=name))
        self.robot_state["target"] = name
        self.robot_state["mode"]   = "navigating"
        self.broadcast_state()
        self.broadcast_log(f"Navigation → {station_key}", "cmd")
        return True

    def nav_goto_xy(self, x: float, y: float) -> bool:
        """Demande un trajet direct vers un point (x, y) en mètres."""
        p = Point()
        p.x, p.y, p.z = float(x), float(y), 0.0
        self.pub_mission_point.publish(p)
        self.robot_state["target"] = f"({x:.2f}, {y:.2f})"
        self.robot_state["mode"]   = "navigating"
        self.broadcast_state()
        self.broadcast_log(f"Navigation → ({x:.2f}, {y:.2f})", "cmd")
        return True

    def nav_stop(self):
        """Annule la mission en cours et coupe les moteurs."""
        self.pub_nav_cancel.publish(Empty())
        # Sécurité : on coupe aussi directement les moteurs au cas où.
        self.set_motor(0, 0)
        self.robot_state["target"] = None
        self.robot_state["mode"]   = "idle"
        self.broadcast_state()
        self.broadcast_log("STOP — navigation interrompue", "err")

    def imu_tare(self):
        """Publie sur /imu_tare : firmware_node refixe le cap courant à 0°."""
        self.pub_imu_tare.publish(Empty())
        self.broadcast_log("Cap réinitialisé (tare yaw)", "cmd")

    def localize(self):
        """Déclenche un scan de localisation (rotation jusqu'à acquérir la pose)."""
        self.pub_nav_localize.publish(Empty())
        self.broadcast_log("Localisation — recherche d'un marqueur", "cmd")

    # ─────────────────────────────────────────
    #  CALLBACKS ROS2 (état robot -> SSE navigateur)
    # ─────────────────────────────────────────
    def on_yaw(self, msg: Float32):
        self.robot_yaw = msg.data

    def on_distance(self, msg: Float32):
        self.robot_distance = msg.data

    def on_pose(self, msg: String):
        """Reçoit la pose depuis localization_node et la pousse au navigateur."""
        try:
            self.pose = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        # Push direct au navigateur (SSE) pour affichage temps réel
        self.broadcast("pose", self.pose)

    def on_nav_status(self, msg: String):
        """Reçoit les événements de navigation depuis navigation_node
        (start / arrived / lost / cancelled) et les transforme en état."""
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        event  = data.get("event")
        target = data.get("target")

        if event == "arrived":
            # MARC est arrivé : on consigne la station courante.
            if isinstance(target, str) and not target.startswith("("):
                # cible nommée (visual servoing)
                # On retrouve le nom de station si target est un ID ArUco
                self.robot_state["current"] = self.robot_state.get("target") or self.robot_state.get("current")
            self.robot_state["target"] = None
            self.robot_state["mode"]   = "idle"
            self.broadcast_log(f"Arrivé à {target}", "info")
            self.tts("Je suis arrivé.")
            self.set_led("love")
        elif event == "lost":
            self.robot_state["target"] = None
            self.robot_state["mode"]   = "idle"
            self.broadcast_log(f"Cible {target} introuvable", "err")
            self.tts("Je n'ai pas trouvé la destination.")
            self.set_led("cry")
        elif event == "cancelled":
            self.robot_state["target"] = None
            self.robot_state["mode"]   = "idle"
            self.broadcast_log("Navigation annulée", "info")
        elif event == "start":
            self.robot_state["mode"] = "navigating"

        self.broadcast_state()

    def on_vocal_command(self, msg: String):
        """Reçoit le JSON publié par voice_node."""
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"JSON invalide sur /vocal_command : {msg.data}")
            return
        if payload.get("type") == "chat":
            return
        action = payload.get("action")
        if not action:
            return
        self.execute_action(payload)

    # ─────────────────────────────────────────
    #  SSE
    # ─────────────────────────────────────────
    def full_state(self) -> dict:
        """État poussé au navigateur (clé 'pose' incluse pour affichage)."""
        return {**self.robot_state, "pose": self.pose}

    @staticmethod
    def _sse_format(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def broadcast(self, event: str, data: dict):
        msg = self._sse_format(event, data)
        with self.sse_lock:
            dead = []
            for q in self.sse_clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.sse_clients.remove(q)

    def broadcast_state(self):
        self.broadcast("state", self.full_state())

    def broadcast_speech(self, text: str):
        self.broadcast("speech", {"text": text})

    def broadcast_log(self, message: str, level: str = "info"):
        self.broadcast("log", {"message": message, "level": level})

    # ─────────────────────────────────────────
    #  TTS / LED
    # ─────────────────────────────────────────
    def tts(self, text: str):
        self.set_led("neutral")
        self.broadcast_speech(text)
        threading.Thread(target=speak, args=(text,), daemon=True).start()

    def clear_matrix(self):
        # La matrice appartient à led_node ; on lui demande l'état neutre.
        self.set_led("idle:neutral")

    # ─────────────────────────────────────────
    #  CONTEXTE LLM (repris de server.py)
    # ─────────────────────────────────────────
    def build_extra_context(self) -> str:
        pose = self.pose or {}
        px, py, ph = pose.get("x"), pose.get("y"), pose.get("heading")
        pose_str = (f"x={px}m, y={py}m, cap={ph}°"
                    if px is not None else "inconnue (aucun marqueur visible)")
        return f"""ÉTAT ACTUEL DU ROBOT :
- Position actuelle : {pose_str}
- Dernière station atteinte : {self.robot_state.get("current") or "aucune"}
- Destination en cours : {self.robot_state.get("target") or "aucune"}
- Mode : {self.robot_state.get("mode")}

Règles :
- MARC navigue vers des coordonnées de la salle (localisation + planification de chemin).
- Pour aller vers une station, génère :
  {{"type": "commande", "action": "moveTo", "response": "Je me dirige vers <destination>.", "destination": "<Nao|Vector|Pepper|Imprimante3D|Baxter|brasRobotique>"}}
- Pour aller vers un point précis, génère :
  {{"type": "commande", "action": "goTo", "response": "Je m'y rends.", "x": <mètres>, "y": <mètres>}}
- Pour arrêter le déplacement en cours, génère :
  {{"type": "commande", "action": "stopNav", "response": "J'arrête."}}
- Pour réinitialiser le cap, génère :
  {{"type": "commande", "action": "resetYaw", "response": "Cap réinitialisé."}}"""

    @staticmethod
    def get_local_ip() -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    # ─────────────────────────────────────────
    #  EXÉCUTION DES ACTIONS (repris de server.py, série -> set_motor)
    # ─────────────────────────────────────────
    def execute_action(self, payload: dict) -> dict:
        action = payload.get("action")
        result = {"ok": True, "action": action}
        self.get_logger().info(f"Exécution : {json.dumps(payload, ensure_ascii=False)}")

        if action == "moveTo":
            # Navigation autonome vers une station (mission_node + graphe de waypoints).
            self.set_led("suspicious")
            dest_raw = payload.get("destination", "")
            station_key = self.DESTINATION_MAP.get(dest_raw, dest_raw.lower())
            ok = self.nav_goto_station(station_key)
            result["destination"] = station_key
            result["ok"] = ok

        elif action == "goTo":
            # Go-to-goal direct (x, y) en mètres.
            try:
                x = float(payload.get("x"))
                y = float(payload.get("y"))
                self.set_led("suspicious")
                self.nav_goto_xy(x, y)
                result["x"], result["y"] = x, y
            except (TypeError, ValueError):
                result["ok"] = False
                result["error"] = "x/y manquants ou invalides"

        elif action == "stopNav":
            # Arrêt d'une navigation en cours.
            self.nav_stop()
            self.set_led("idle:neutral")

        elif action == "resetYaw":
            # Réinitialisation du cap (tare yaw) via firmware_node.
            self.imu_tare()

        elif action == "localize":
            # Scan de localisation (rotation jusqu'à voir un marqueur).
            self.set_led("suspicious")
            self.localize()

        elif action == "moveForward":
            self.set_motor_timed(150, 0, payload.get("temps"))
            result["duration"] = payload.get("temps")

        elif action == "moveBackward":
            self.set_motor_timed(-150, 0, payload.get("temps"))
            result["duration"] = payload.get("temps")

        elif action == "turnLeft":
            self.set_motor_timed(0, -200, payload.get("temps"))
            result["duration"] = payload.get("temps")

        elif action == "turnRight":
            self.set_motor_timed(0, 200, payload.get("temps"))
            result["duration"] = payload.get("temps")

        elif action == "turn":
            self.set_motor_timed(0, 200, 2.0)

        elif action == "changeEyes":
            style = payload.get("style", 1)
            self.robot_state["eyes"] = style
            self.set_led(f"style:{style}")
            result["style"] = style

        elif action == "shutdown":
            self.nav_stop()
            self.robot_state["mode"] = "idle"
            self.clear_matrix()
            result["mode"] = "idle"

        else:
            result["ok"] = False
            result["error"] = f"Action inconnue : {action}"
            self.get_logger().warn(f"Action inconnue : {action}")

        self.broadcast_state()
        return result


# ═════════════════════════════════════════════
#  FLASK — défini après le nœud, utilise une référence globale
# ═════════════════════════════════════════════
node: WebBridgeNode = None
app = Flask(__name__, static_folder=WEB_DIR)
TMP_DIR = tempfile.gettempdir()


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@app.route("/status")
def status():
    return jsonify({"robot_state": node.full_state()})


@app.route("/motor", methods=["POST"])
def motor():
    """Contrôle moteur direct (utilisé en debug ou par scripts externes)."""
    data = request.get_json()
    move = int(data.get("move", 0))
    turn = int(data.get("turn", 0))
    node.set_motor(move, turn)
    return jsonify({"ok": True, "move": move, "turn": turn})


@app.route("/nav_stop", methods=["POST"])
def nav_stop():
    """Arrête immédiatement la navigation en cours."""
    node.nav_stop()
    return jsonify({"ok": True})


@app.route("/imu_tare", methods=["POST"])
def imu_tare():
    """Re-tare le cap (yaw) : le firmware refixe l'orientation courante à 0°."""
    node.imu_tare()
    return jsonify({"ok": True})


@app.route("/localize", methods=["POST"])
def localize():
    """Déclenche un scan de localisation (rotation jusqu'à acquérir la pose)."""
    node.localize()
    return jsonify({"ok": True})


@app.route("/odometry")
def odometry():
    return jsonify({"distance": node.robot_distance, "yaw": node.robot_yaw})


@app.route("/pose")
def pose():
    """Dernière pose connue depuis localization_node."""
    return jsonify(node.pose)


@app.route("/events")
def events():
    def stream():
        q: queue.Queue = queue.Queue(maxsize=50)
        with node.sse_lock:
            node.sse_clients.append(q)
        yield node._sse_format("state", node.full_state())
        try:
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with node.sse_lock:
                if q in node.sse_clients:
                    node.sse_clients.remove(q)

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.route("/vocal_command", methods=["POST"])
def vocal_command():
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "payload vide"}), 400
    type_ = payload.get("type")
    response = payload.get("response", "")
    if type_ == "chat":
        return jsonify({"ok": True, "type": "chat", "robot_state": node.full_state()})
    action = payload.get("action")
    if not action:
        return jsonify({"error": "action manquante"}), 400
    result = node.execute_action(payload)
    result["robot_state"] = node.full_state()
    result["response"] = response
    return jsonify(result)


@app.route("/command", methods=["POST"])
def command():
    data = request.get_json()
    destination = data.get("destination")
    if not destination:
        return jsonify({"error": "destination manquante"}), 400
    web_to_llm = {v: k for k, v in node.DESTINATION_MAP.items()}
    dest_name = web_to_llm.get(destination, destination)
    llm_result = ask_ollama(
        f"Je dois me déplacer vers {dest_name}. Confirme brièvement.",
        extra_context=node.build_extra_context()
    )
    ai_reply = (llm_result.get("response", f"Je me dirige vers {dest_name}.")
                if llm_result else f"Je me dirige vers {dest_name}.")
    if llm_result and llm_result.get("type") == "commande":
        node.execute_action(llm_result)
    node.tts(ai_reply)
    return jsonify({"robot_state": node.full_state(), "ai_reply": ai_reply})


@app.route("/send_text", methods=["POST"])
def send_text():
    data = request.get_json()
    user_text = data.get("user_text", "")
    llm_result = ask_ollama(user_text, extra_context=node.build_extra_context())
    if not llm_result:
        return jsonify({"error": "LLM indisponible"}), 503
    ai_reply = llm_result.get("response", "")
    if llm_result.get("type") == "commande" and llm_result.get("action"):
        node.execute_action(llm_result)
    node.tts(ai_reply)
    return jsonify({"ai_reply": ai_reply, "robot_state": node.full_state()})


# ── Transcription audio (bouton micro web) ──
import speech_recognition as sr  # noqa: E402
from pydub import AudioSegment    # noqa: E402


def convert_to_wav(input_path: str):
    output_path = input_path.rsplit(".", 1)[0] + ".wav"
    try:
        audio = AudioSegment.from_file(input_path)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(output_path, format="wav")
        return output_path
    except Exception as e:
        print(f"❌  Conversion audio erreur : {e}")
        return None


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "Aucun fichier audio"}), 400
    audio_file = request.files["audio"]
    mimetype = audio_file.mimetype or ""
    suffix = (".webm" if "webm" in mimetype else
              ".ogg" if "ogg" in mimetype else
              ".mp4" if "mp4" in mimetype else ".webm")
    tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=TMP_DIR)
    audio_file.save(tmp_input.name)
    tmp_input.close()
    wav_path = convert_to_wav(tmp_input.name)
    os.unlink(tmp_input.name)
    if not wav_path:
        return jsonify({"error": "Conversion audio échouée — installe ffmpeg"}), 500
    transcript = ""
    try:
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
        transcript = recognizer.recognize_google(audio_data, language="fr-FR")
        print(f"✅  Transcription web : {transcript}")
    except sr.UnknownValueError:
        print("⚠️  Audio incompréhensible")
    except sr.RequestError as e:
        print(f"❌  Google STT erreur : {e}")
        return jsonify({"error": "Google STT indisponible"}), 503
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass
    if not transcript:
        node.tts("Je n'ai pas compris.")
        node.set_led("cry")
        return jsonify({"transcript": "", "ai_reply": "Je n'ai pas compris.",
                        "robot_state": node.full_state()})
    llm_result = ask_ollama(transcript, extra_context=node.build_extra_context())
    if not llm_result:
        return jsonify({"error": "LLM indisponible"}), 503
    ai_reply = llm_result.get("response", "")
    if llm_result.get("type") == "commande" and llm_result.get("action"):
        node.execute_action(llm_result)
    node.tts(ai_reply)
    return jsonify({"transcript": transcript, "ai_reply": ai_reply,
                    "robot_state": node.full_state()})


# ═════════════════════════════════════════════
#  MAIN — lance Flask (thread) + rclpy.spin (principal)
# ═════════════════════════════════════════════
def run_flask():
    cert = os.path.join(WEB_DIR, "cert.pem")
    key  = os.path.join(WEB_DIR, "key.pem")
    print("🚀  MARC webbridge — Flask HTTPS sur https://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True,
            use_reloader=False,  # IMPORTANT : reloader incompatible avec un thread
            ssl_context=(cert, key))


def main(args=None):
    global node
    rclpy.init(args=args)
    node = WebBridgeNode()

    # Flask dans un thread daemon
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.clear_matrix()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
