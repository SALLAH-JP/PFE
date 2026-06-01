#!/usr/bin/env python3
"""
webbridge_node.py — Pont Web <-> ROS2 pour MARC.

Porté depuis web/server.py (option A : on garde la logique métier ici,
on branche ROS2 par-dessus). Au lieu de parler à l'Arduino en série,
ce nœud passe par les topics ROS2 publiés/souscrits par firmware_node :

  PUBLIE (commandes vers le robot)
    /cmd_motor   std_msgs/Int32MultiArray  [move, turn]
    /line_mode   std_msgs/Bool

  SOUSCRIT (état du robot, depuis firmware_node)
    /station     std_msgs/Int32
    /imu_yaw     std_msgs/Float32
    /distance    std_msgs/Float32
    /obstacles   std_msgs/Float32MultiArray  [centre, gauche, droite]

Le reste (Flask HTTPS, SSE vers le navigateur, LLM via ask_ollama, TTS,
matrice LED, transcription) est conservé tel quel depuis server.py.

Architecture des threads :
  - rclpy.spin(node)  -> thread principal (ROS2)
  - Flask app.run()   -> thread daemon séparé
Les deux partagent l'état global du nœud (protégé par GIL pour des
lectures/écritures simples ; pas de structure complexe partagée).
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
from std_msgs.msg import Bool, Int32, Float32, Int32MultiArray, Float32MultiArray, String

# ─────────────────────────────────────────────
#  FLASK
# ─────────────────────────────────────────────
from flask import Flask, request, jsonify, send_from_directory, Response

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
# remonte de src/marc_nodes/marc_nodes/ -> racine MARC/

ROOT = "/home/pi/MARC"
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

        # ── État robot (repris de server.py) ──
        self.robot_state = {
            "current": "base",
            "target":  None,
            "eyes":    1,
            "mode":    "idle",
        }
        self.line_following   = False
        self.destination_cible = None       # numéro de station visé
        self.station_actuelle  = -1
        self.robot_yaw        = 0.0
        self.robot_distance   = 0.0
        self.us_centre = -1.0
        self.us_gauche = -1.0
        self.us_droite = -1.0

        # ── Mappings (repris de server.py) ──
        self.STATION_NUMBERS = {
            "base": 0, "nao": 1, "vector": 2, "pepper": 3,
            "imp3d": 4, "baxter": 5, "bras": 6,
        }
        self.STATION_BY_NUMBER = {v: k for k, v in self.STATION_NUMBERS.items()}
        self.DESTINATION_MAP = {
            "Nao": "nao", "Vector": "vector", "Pepper": "pepper",
            "Imprimante3D": "imp3d", "Baxter": "baxter",
            "brasRobotique": "bras", "Base": "base",
        }

        # ── SSE ──
        self.sse_clients: list[queue.Queue] = []
        self.sse_lock = threading.Lock()

        # ── Publishers ROS2 (vers firmware_node) ──
        self.pub_cmd_motor = self.create_publisher(Int32MultiArray, "/cmd_motor", 10)
        self.pub_line_mode = self.create_publisher(Bool, "/line_mode", 10)

        # ── Subscribers ROS2 (depuis firmware_node) ──
        self.create_subscription(Int32, "/station", self.on_station, 10)
        self.create_subscription(Float32, "/imu_yaw", self.on_yaw, 10)
        self.create_subscription(Float32, "/distance", self.on_distance, 10)
        self.create_subscription(Float32MultiArray, "/obstacles", self.on_obstacles, 10)

        # ── Subscriber ROS2 (depuis voice_node) ──
        # Le voice_node publie son JSON de commande ici au lieu d'un POST HTTPS.
        self.create_subscription(String, "/vocal_command", self.on_vocal_command, 10)

        # ── Publisher ROS2 (vers led_node) ──
        # La matrice n'est plus pilotée ici : on publie des expressions.
        self.pub_led = self.create_publisher(String, "/led_expression", 10)

        self.get_logger().info("webbridge_node démarré")

    # ─────────────────────────────────────────
    #  HELPERS COMMANDE MOTEUR (remplace l'écriture série)
    # ─────────────────────────────────────────
    def set_motor(self, move: int, turn: int):
        """Publie une commande moteur sur /cmd_motor (vers firmware_node)."""
        msg = Int32MultiArray()
        msg.data = [int(move), int(turn)]
        self.pub_cmd_motor.publish(msg)

    def set_line_mode(self, enabled: bool):
        """Publie le mode suivi de ligne sur /line_mode."""
        self.line_following = enabled
        self.pub_line_mode.publish(Bool(data=bool(enabled)))
        self.get_logger().info(f"Mode ligne : {'ON' if enabled else 'OFF'}")
        self.broadcast_state()
        self.broadcast_log(f"Mode guide : {'ON' if enabled else 'OFF'}", "cmd")

    def set_led(self, expression: str):
        """Publie une expression sur /led_expression (vers led_node).
        Ex: "love", "neutral", "suspicious", "cry", "style:2", "idle:neutral"."""
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
    #  CALLBACKS ROS2 (état robot -> SSE navigateur)
    # ─────────────────────────────────────────
    def on_station(self, msg: Int32):
        self.station_actuelle = msg.data
        self.get_logger().info(f"Station reçue : {self.station_actuelle}")
        self.check_destination()

    def on_yaw(self, msg: Float32):
        self.robot_yaw = msg.data

    def on_distance(self, msg: Float32):
        self.robot_distance = msg.data

    def on_obstacles(self, msg: Float32MultiArray):
        if len(msg.data) >= 3:
            self.us_centre = msg.data[0]
            self.us_gauche = msg.data[1]
            self.us_droite = msg.data[2]

    def on_vocal_command(self, msg: String):
        """Reçoit le JSON publié par voice_node (équivalent de la route
        Flask /vocal_command, mais via ROS2 au lieu de HTTPS POST)."""
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"JSON invalide sur /vocal_command : {msg.data}")
            return
        # Les 'chat' n'entraînent aucune action robot (réponse déjà dite par voice_node)
        if payload.get("type") == "chat":
            return
        action = payload.get("action")
        if not action:
            return
        self.execute_action(payload)

    def check_destination(self):
        """Arrivée à destination (repris de server.py)."""
        if self.destination_cible is not None and self.station_actuelle == self.destination_cible:
            self.set_motor(0, 0)
            dest_id = self.STATION_BY_NUMBER.get(self.station_actuelle)
            if dest_id:
                self.robot_state["current"] = dest_id
            self.robot_state["target"] = None
            self.destination_cible = None
            self.get_logger().info(f"Arrivé station {self.station_actuelle} ({dest_id})")
            self.broadcast_state()
            self.broadcast_log(f"Arrivé à {dest_id}", "info")
            self.tts("Je suis arrivé à destination.")
            self.set_led("love")

    # ─────────────────────────────────────────
    #  SSE
    # ─────────────────────────────────────────
    def full_state(self) -> dict:
        return {**self.robot_state, "line_following": self.line_following}

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
        return f"""ÉTAT ACTUEL DU ROBOT :
- Mode guide (suivi de ligne) : {"ACTIF" if self.line_following else "INACTIF"}
- Position actuelle : {self.robot_state.get("current", "inconnue")}
- Destination en cours : {self.robot_state.get("target", "aucune")}
- Adresse IP locale : {self.get_local_ip()}

Règles :
- Si l'utilisateur demande un moveTo ET que le mode guide est INACTIF, ne génère PAS d'action moveTo.
  Réponds en chat pour l'informer que le mode guide est inactif et demande s'il veut l'activer.
- Si l'utilisateur confirme vouloir activer le mode guide, génère :
  {{"type": "commande", "action": "enableLineFollowing", "response": "Mode guide activé, je me dirige vers <destination>.", "destination": "<destination>"}}
- Si le mode guide est ACTIF, génère les actions moveTo normalement."""

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
            self.set_led("suspicious")
            dest_raw = payload.get("destination", "")
            dest_id  = self.DESTINATION_MAP.get(dest_raw, dest_raw.lower())
            num      = self.STATION_NUMBERS.get(dest_id)
            if num is not None:
                self.destination_cible = num
                self.set_motor(150, 0)
            self.robot_state["target"] = dest_id
            result["destination"] = dest_id

        elif action == "enableLineFollowing":
            dest_raw = payload.get("destination", "")
            dest_id  = self.DESTINATION_MAP.get(dest_raw, dest_raw.lower())
            num      = self.STATION_NUMBERS.get(dest_id)
            self.set_line_mode(True)
            if num is not None:
                self.destination_cible = num
                self.set_motor(150, 0)
            self.robot_state["target"] = dest_id
            result["destination"] = dest_id

        elif action == "disableLineFollowing":
            self.set_led("idle:neutral")
            self.set_line_mode(False)
            self.set_motor(0, 0)
            self.destination_cible = None
            self.robot_state["target"] = None
            result["mode"] = "manual"

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
    """Contrôle moteur direct (navigation ArUco). Refusé si mode ligne actif."""
    if node.line_following:
        return jsonify({"ok": False, "error": "mode ligne actif"}), 409
    data = request.get_json()
    move = int(data.get("move", 0))
    turn = int(data.get("turn", 0))
    node.set_motor(move, turn)
    return jsonify({"ok": True, "move": move, "turn": turn})


@app.route("/odometry")
def odometry():
    return jsonify({"distance": node.robot_distance, "yaw": node.robot_yaw})


@app.route("/obstacles")
def obstacles():
    return jsonify({"centre": node.us_centre, "gauche": node.us_gauche, "droite": node.us_droite})


@app.route("/navigation", methods=["POST"])
def navigation():
    data = request.get_json()
    event = data.get("event")
    target = data.get("target")
    if event == "start":
        node.robot_state["target"] = target
        node.broadcast_log(f"Navigation ArUco → {target}", "cmd")
    elif event == "arrived":
        node.robot_state["current"] = target
        node.robot_state["target"] = None
        node.broadcast_log(f"Arrivé à la cible {target}", "info")
        node.tts("Je suis arrivé.")
    elif event == "lost":
        node.broadcast_log(f"Cible {target} introuvable", "err")
    node.broadcast_state()
    return jsonify({"ok": True})


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


@app.route("/line_following", methods=["POST"])
def toggle_line_following():
    data = request.get_json()
    enabled = data.get("enabled", not node.line_following)
    node.set_line_mode(enabled)
    if not enabled:
        node.set_motor(0, 0)
        node.destination_cible = None
        node.robot_state["target"] = None
        node.broadcast_state()
    return jsonify({"robot_state": node.full_state()})


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
