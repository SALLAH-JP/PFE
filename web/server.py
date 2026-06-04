#!/usr/bin/env python3
"""
MARC Robot — server.py
Serveur Flask : reçoit les commandes depuis voiceAssistant.py (HTTP)
et depuis l'interface web (boutons/clics).
"""

import os
import sys
import time
import tempfile
import threading
import json
import queue

from flask import Flask, request, jsonify, send_from_directory, Response

# ── Imports voiceAssistant (fonctions partagées) ──
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(ROOT, "assistantVocale"))
sys.path.append(os.path.join(ROOT, "matrixLed"))
from voiceAssistant import speak, ask_ollama, ask_ollama_stream, TTSPlayer, recognizer

# ── Matrix LED (optionnel) ──
try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    from eye_manager import EyeManager
    MATRIX_AVAILABLE = True
except ImportError:
    print("⚠️  rgbmatrix non disponible (mode PC)")
    MATRIX_AVAILABLE = False


# ─────────────────────────────────────────────
#  SERIAL (Arduino)
# ─────────────────────────────────────────────
arduino      = None
ARDUINO_PORT = '/dev/ttyACM0'
ARDUINO_BAUD = 115200

try:
    import serial
    arduino = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=2)
    time.sleep(2)
    arduino.reset_input_buffer()
    SERIAL_AVAILABLE = True
    print(f'[Arduino] Connecté sur {ARDUINO_PORT} ✓')
except Exception as e:
    print(f'[Arduino] Non connecté (ignoré) : {e}')
    SERIAL_AVAILABLE = False
    arduino = None


from collections import deque
import statistics

_us_gauche_buf = deque(maxlen=10)
_us_droite_buf = deque(maxlen=10)

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
GIF_DIR  = os.path.join(ROOT, "matrixLed")


# ─────────────────────────────────────────────
#  ÉTAT GLOBAL
# ─────────────────────────────────────────────
robot_state = {
    "current": "base",
    "target":  None,
    "eyes":    1,
    "mode":    "idle",
}

current_move      = 0
current_turn      = 0
station_actuelle  = -1
destination_cible = None
line_following    = False
eyes = None
robot_yaw         = 0.0   # cap brut du BNO085 (degrés)
robot_distance    = 0.0   # distance cumulée parcourue (cm)
us_centre = -1.0
us_gauche = -1.0
us_droite = -1.0


# Numéro de station physique → id web
STATION_NUMBERS = {
    "base":   0,
    "nao":    1,
    "vector": 2,
    "pepper": 3,
    "imp3d":  4,
    "baxter": 5,
    "bras":   6,
}

# Numéro physique → id web (inverse)
STATION_BY_NUMBER = {v: k for k, v in STATION_NUMBERS.items()}

# Nom LLM → id web
DESTINATION_MAP = {
    "Nao":           "nao",
    "Vector":        "vector",
    "Pepper":        "pepper",
    "Imprimante3D":  "imp3d",
    "Baxter":        "baxter",
    "brasRobotique": "bras",
    "Base":          "base",
}


# ─────────────────────────────────────────────
#  HELPERS ÉTAT
# ─────────────────────────────────────────────
def full_state() -> dict:
    """Retourne robot_state enrichi avec line_following."""
    return {**robot_state, "line_following": line_following}


# ─────────────────────────────────────────────
#  SSE — Server-Sent Events (temps réel vers le navigateur)
# ─────────────────────────────────────────────
# Chaque navigateur connecté possède sa propre file. Quand on appelle
# broadcast(...), l'événement est posté dans toutes les files. Le générateur
# attaché à /events vide la file et l'écrit dans le flux HTTP du client.
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()


def _sse_format(event: str, data: dict) -> str:
    """Formate un message SSE conforme au protocole."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def broadcast(event: str, data: dict) -> None:
    """Envoie un événement à tous les navigateurs connectés."""
    msg = _sse_format(event, data)
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)


def broadcast_state() -> None:
    """Pousse l'état complet du robot à tous les clients."""
    broadcast("state", full_state())


def broadcast_speech(text: str) -> None:
    """Pousse une phrase prononcée par MARC à tous les clients."""
    broadcast("speech", {"text": text})


def broadcast_log(message: str, level: str = "info") -> None:
    """Pousse une entrée de journal à tous les clients."""
    broadcast("log", {"message": message, "level": level})


# ─────────────────────────────────────────────
#  MATRIX LED
# ─────────────────────────────────────────────
matrix = None
if MATRIX_AVAILABLE:
    options = RGBMatrixOptions()
    options.rows = 32
    options.cols = 64
    options.led_rgb_sequence = "RBG"
    options.brightness = 75
    options.disable_hardware_pulsing = True
    options.hardware_mapping = "regular"
    matrix = RGBMatrix(options=options)

    eyes = EyeManager(matrix, GIF_DIR, style=2)
    eyes.start()
    print("✅  Matrix LED initialisée")


def clear_matrix():
    if MATRIX_AVAILABLE and matrix:
        matrix.Clear()
        if eyes: eyes.stop()


# ─────────────────────────────────────────────
#  SERIAL WORKER
# ─────────────────────────────────────────────
def serial_worker():
    global station_actuelle
    buffer = ""

    while True:
        if SERIAL_AVAILABLE:
            try:
                # 1. Envoie la commande moteur
                cmd = f"C:{current_move}:{current_turn}\n"
                arduino.write(cmd.encode())

                # 2. Lit les réponses (non bloquant)
                if arduino.in_waiting:
                    chunk = arduino.read(arduino.in_waiting).decode(errors="ignore")
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("S:"):
                            try:
                                station_actuelle = int(line[2:])
                                print(f"📍 Station : {station_actuelle}")
                                check_destination()
                            except ValueError:
                                pass
                        elif line.startswith("Y:"):
                            try:
                                global robot_yaw
                                robot_yaw = float(line[2:])
                            except ValueError:
                                pass
                        elif line.startswith("D:"):
                            try:
                                global robot_distance
                                robot_distance = float(line[2:])
                            except ValueError:
                                pass
                        elif line.startswith("U:"):
                            try:
                                global us_centre, us_gauche, us_droite
                                p = line[2:].split(":")
                                us_centre = float(p[0])
                                raw_g = float(p[1])
                                raw_d = float(p[2])
                                _us_gauche_buf.append(raw_g)
                                _us_droite_buf.append(raw_d)
                                us_gauche = statistics.median(_us_gauche_buf)
                                us_droite = statistics.median(_us_droite_buf)
                            except (ValueError, IndexError):
                                pass
            except Exception as e:
                print(f"❌  Serial error: {e}")

        time.sleep(0.005)  # 200 Hz


def check_destination():
    global current_move, current_turn, destination_cible, line_following
    if destination_cible is not None and station_actuelle == destination_cible:
        current_move      = 0
        current_turn      = 0
        # Met à jour la position courante sur l'interface web
        dest_id = STATION_BY_NUMBER.get(station_actuelle)
        if dest_id:
            robot_state["current"] = dest_id
        robot_state["target"] = None
        destination_cible     = None
        print(f"✅  Arrivé station {station_actuelle} ({dest_id})")
        broadcast_state()
        broadcast_log(f"Arrivé à {dest_id}", "info")
        tts("Je suis arrivé à destination.")
        if eyes: eyes.play("love")


def send_serial_timed(move: int, turn: int, duration: float | None):
    global current_move, current_turn
    current_move = move
    current_turn = turn
    if duration:
        def stop_after():
            time.sleep(duration)
            global current_move, current_turn
            current_move = 0
            current_turn = 0
        threading.Thread(target=stop_after, daemon=True).start()


def send_mode(enabled: bool):
    global line_following
    line_following = enabled
    if SERIAL_AVAILABLE:
        try:
            arduino.write(f"M:{'1' if enabled else '0'}\n".encode())
        except Exception as e:
            print(f"❌  Erreur envoi mode : {e}")
    print(f"🚦 Mode ligne : {'ON' if enabled else 'OFF'}")
    broadcast_state()
    broadcast_log(f"Mode guide : {'ON' if enabled else 'OFF'}", "cmd")


TMP_DIR = tempfile.gettempdir()

# Lecteur vocal EN FLUX partagé : un seul chemin audio (haut-parleur du Pi),
# file FIFO, synthèse edge-tts streamée vers mpg123.
web_tts = TTSPlayer()


def tts(text: str) -> None:
    """Énoncé court mono-bloc (ex. « Je n'ai pas compris »)."""
    if eyes: eyes.play("neutral")
    broadcast_speech(text)
    web_tts.say(text)


def speak_and_act_streaming(user_text: str, extra_context: str = "") -> dict:
    """
    Pipeline web EN FLUX.

    Interroge le LLM en streaming : chaque phrase est, dès qu'elle est prête,
    (1) prononcée par MARC sur le haut-parleur du Pi (web_tts) et
    (2) poussée au navigateur en temps réel via SSE (affichage progressif).
    L'éventuelle commande est exécutée à la fin, une fois le JSON complet reçu.

    Retourne le dict LLM complet (sans la clé interne "_spoke").
    """
    if eyes: eyes.play("neutral")
    broadcast("speech_start", {})

    def on_sentence(sentence: str) -> None:
        web_tts.say(sentence)                                   # Pi parle (en flux)
        broadcast("speech", {"text": sentence, "partial": True})  # navigateur

    result = ask_ollama_stream(user_text, on_sentence, extra_context=extra_context)
    result.pop("_spoke", None)

    ai_reply = result.get("response", "")

    # Exécution de l'action après réception du JSON complet
    if result.get("type") == "commande" and result.get("action"):
        execute_action(result)

    # Fin de l'énoncé : texte complet pour le journal + filet de sécurité d'affichage
    broadcast("speech", {"text": ai_reply, "partial": False, "done": True})
    return result


# ─────────────────────────────────────────────
#  CONTEXTE LLM DYNAMIQUE
# ─────────────────────────────────────────────
def build_extra_context() -> str:
    return f"""ÉTAT ACTUEL DU ROBOT :
- Mode guide (suivi de ligne) : {"ACTIF" if line_following else "INACTIF"}
- Position actuelle : {robot_state.get("current", "inconnue")}
- Destination en cours : {robot_state.get("target", "aucune")}
- Adresse IP locale : {get_local_ip()}

Règles :
- Si l'utilisateur demande un moveTo ET que le mode guide est INACTIF, ne génère PAS d'action moveTo.
  Réponds en chat pour l'informer que le mode guide est inactif et demande s'il veut l'activer.
- Si l'utilisateur confirme vouloir activer le mode guide, génère :
  {{"type": "commande", "action": "enableLineFollowing", "response": "Mode guide activé, je me dirige vers <destination>.", "destination": "<destination>"}}
- Si le mode guide est ACTIF, génère les actions moveTo normalement."""


# ─────────────────────────────────────────────
#  EXÉCUTION DES ACTIONS
# ─────────────────────────────────────────────
def execute_action(payload: dict) -> dict:
    global destination_cible, current_move, current_turn
    action = payload.get("action")
    result = {"ok": True, "action": action}

    print(f"⚙️  Exécution : {json.dumps(payload, ensure_ascii=False)}")

    if action == "moveTo":
        if eyes: eyes.play("suspicious")
        dest_raw = payload.get("destination", "")
        dest_id  = DESTINATION_MAP.get(dest_raw, dest_raw.lower())
        num      = STATION_NUMBERS.get(dest_id)
        if num is not None:
            destination_cible     = num
            current_move          = 150
            current_turn          = 0
        robot_state["target"] = dest_id
        result["destination"] = dest_id

    elif action == "enableLineFollowing":
        dest_raw = payload.get("destination", "")
        dest_id  = DESTINATION_MAP.get(dest_raw, dest_raw.lower())
        num      = STATION_NUMBERS.get(dest_id)
        send_mode(True)
        if num is not None:
            destination_cible     = num
            current_move          = 150
            current_turn          = 0
        robot_state["target"] = dest_id
        result["destination"] = dest_id

    elif action == "disableLineFollowing":
        if eyes: eyes.set_idle("neutral")
        send_mode(False)
        current_move      = 0
        current_turn      = 0
        destination_cible = None
        robot_state["target"] = None
        result["mode"] = "manual"

    elif action == "moveForward":
        duration = payload.get("temps")
        send_serial_timed(150, 0, duration)
        result["duration"] = duration

    elif action == "moveBackward":
        duration = payload.get("temps")
        send_serial_timed(-150, 0, duration)
        result["duration"] = duration

    elif action == "turnLeft":
        duration = payload.get("temps")
        send_serial_timed(0, -200, duration)
        result["duration"] = duration

    elif action == "turnRight":
        duration = payload.get("temps")
        send_serial_timed(0, 200, duration)
        result["duration"] = duration

    elif action == "turn":
        send_serial_timed(0, 200, 2.0)

    elif action == "changeEyes":
        style    = payload.get("style", 1)
        robot_state["eyes"] = style
        if eyes: eyes.set_style(style)
        result["style"] = style

    elif action == "shutdown":
        robot_state["mode"] = "idle"
        clear_matrix()
        result["mode"] = "idle"

    else:
        result["ok"]    = False
        result["error"] = f"Action inconnue : {action}"
        print(f"⚠️  Action inconnue : {action}")

    broadcast_state()
    return result


def get_local_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

# ─────────────────────────────────────────────
#  FLASK APP
# ─────────────────────────────────────────────
app = Flask(__name__, static_folder=".")


# ── Statique ──
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)


# ── État du robot ──
@app.route("/status")
def status():
    return jsonify({"robot_state": full_state()})

# ── Contrôle moteur direct (pour navigation ArUco) ──
@app.route("/motor", methods=["POST"])
def motor():
    """Injecte les commandes moteur depuis le service de navigation."""
    global current_move, current_turn
    # Sécurité : refuse si le mode suivi de ligne est actif
    if line_following:
        return jsonify({"ok": False, "error": "mode ligne actif"}), 409
    data = request.get_json()
    current_move = int(data.get("move", 0))
    current_turn = int(data.get("turn", 0))
    return jsonify({"ok": True, "move": current_move, "turn": current_turn})


# ── Distance cumulée (odométrie) ──
@app.route("/odometry")
def odometry():
    return jsonify({"distance": robot_distance, "yaw": robot_yaw})

@app.route("/obstacles")
def obstacles():
    return jsonify({"centre": us_centre, "gauche": us_gauche, "droite": us_droite})

# ── Navigation : démarrer/arrêter ──
@app.route("/navigation", methods=["POST"])
def navigation():
    """Reçoit l'état de navigation pour mise à jour de l'interface."""
    data = request.get_json()
    event = data.get("event")
    target = data.get("target")
    if event == "start":
        robot_state["target"] = target
        broadcast_log(f"Navigation ArUco → {target}", "cmd")
    elif event == "arrived":
        robot_state["current"] = target
        robot_state["target"] = None
        broadcast_log(f"Arrivé à la cible {target}", "info")
        tts("Je suis arrivé.")
    elif event == "lost":
        broadcast_log(f"Cible {target} introuvable", "err")
    broadcast_state()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  ROUTE : Flux d'événements temps réel (SSE)
# ─────────────────────────────────────────────
@app.route("/events")
def events():
    """
    Le navigateur ouvre EventSource('/events') et reste connecté.
    À chaque broadcast(), tous les clients reçoivent l'événement.
    Le générateur envoie aussi un ping toutes les 15 s pour
    empêcher les proxies de couper la connexion.
    """
    def stream():
        q: queue.Queue = queue.Queue(maxsize=50)
        with sse_lock:
            sse_clients.append(q)

        # Envoi immédiat de l'état courant à la connexion
        yield _sse_format("state", full_state())

        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield msg
                except queue.Empty:
                    # Keep-alive (commentaire SSE, ignoré côté client)
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # désactive le buffering nginx si présent
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────
#  ROUTE : Commande depuis voiceAssistant.py
# ─────────────────────────────────────────────
@app.route("/vocal_command", methods=["POST"])
def vocal_command():
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "payload vide"}), 400

    action   = payload.get("action")
    type_    = payload.get("type")
    response = payload.get("response", "")

    if type_ == "chat":
        return jsonify({"ok": True, "type": "chat", "robot_state": full_state()})

    if not action:
        return jsonify({"error": "action manquante"}), 400

    result = execute_action(payload)
    result["robot_state"] = full_state()
    result["response"]    = response
    return jsonify(result)


# ─────────────────────────────────────────────
#  ROUTE : Commande depuis l'interface web (clic)
# ─────────────────────────────────────────────
@app.route("/command", methods=["POST"])
@app.route("/command", methods=["POST"])
def command():
    data        = request.get_json()
    destination = data.get("destination")

    if not destination:
        return jsonify({"error": "destination manquante"}), 400

    # Libellé lisible pour la confirmation parlée
    STATION_LABELS = {
        "base":   "la base",
        "nao":    "NAO",
        "vector": "Vector",
        "pepper": "Pepper",
        "imp3d":  "l'imprimante 3D",
        "baxter": "Baxter",
        "bras":   "le bras robotique",
    }
    label = STATION_LABELS.get(destination, destination)

    # Exécution DIRECTE — clic station : on ne passe plus par le LLM
    execute_action({"action": "moveTo", "destination": destination})

    # Confirmation vocale fixe
    ai_reply = f"Je me dirige vers {label}."
    tts(ai_reply)

    return jsonify({"robot_state": full_state(), "ai_reply": ai_reply})


# ─────────────────────────────────────────────
#  ROUTE : Toggle mode guide
# ─────────────────────────────────────────────
@app.route("/line_following", methods=["POST"])
def toggle_line_following():
    global current_move, current_turn, destination_cible
    data    = request.get_json()
    enabled = data.get("enabled", not line_following)
    send_mode(enabled)

    if not enabled:
        current_move      = 0
        current_turn      = 0
        destination_cible = None
        robot_state["target"] = None
        broadcast_state()  # target a changé après send_mode

    return jsonify({"robot_state": full_state()})


# ─────────────────────────────────────────────
#  ROUTE : Texte direct (fallback Web Speech)
# ─────────────────────────────────────────────
@app.route("/send_text", methods=["POST"])
def send_text():
    data      = request.get_json()
    user_text = data.get("user_text", "")

    # LLM + TTS en flux (parle + affiche pendant la génération, exécute à la fin)
    llm_result = speak_and_act_streaming(user_text, extra_context=build_extra_context())
    ai_reply   = llm_result.get("response", "")

    return jsonify({
        "ai_reply":    ai_reply,
        "robot_state": full_state(),
    })


# ─────────────────────────────────────────────
#  ROUTE : Transcription audio (bouton micro web)
# ─────────────────────────────────────────────
import speech_recognition as sr
from pydub import AudioSegment

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
    mimetype   = audio_file.mimetype or ""
    suffix     = (
        ".webm" if "webm" in mimetype else
        ".ogg"  if "ogg"  in mimetype else
        ".mp4"  if "mp4"  in mimetype else ".webm"
    )

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
        try: os.unlink(wav_path)
        except: pass

    if not transcript:
        gif_path = os.path.join(GIF_DIR, f"style{style}", "sad.gif")
        show_gif(gif_path)
        tts("Je n'ai pas compris.")
        return jsonify({
            "transcript":  "",
            "ai_reply":    "Je n'ai pas compris.",
            "robot_state": full_state(),
        })

    # LLM + TTS en flux (parle + affiche pendant la génération, exécute à la fin)
    llm_result = speak_and_act_streaming(transcript, extra_context=build_extra_context())
    ai_reply   = llm_result.get("response", "")

    return jsonify({
        "transcript":  transcript,
        "ai_reply":    ai_reply,
        "robot_state": full_state(),
    })


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀  MARC Robot server démarré sur https://11.255.255.119:5000")
    threading.Thread(target=serial_worker, daemon=True).start()
    WEB_DIR = os.path.dirname(os.path.abspath(__file__))
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, ssl_context=(
        os.path.join(WEB_DIR, 'cert.pem'),
        os.path.join(WEB_DIR, 'key.pem')
    ))
