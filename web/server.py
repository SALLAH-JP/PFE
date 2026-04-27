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

from flask import Flask, request, jsonify, send_from_directory

# ── Imports voiceAssistant (fonctions partagées) ──
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(ROOT, "Local-Voice"))
sys.path.append(os.path.join(ROOT, "matrixLed"))
from voiceAssistant import speak, ask_ollama, recognizer

# ── Matrix LED (optionnel) ──
try:
    from gif_viewer import gifViewer
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
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


# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
GIF_DIR  = os.path.join(ROOT, "matrixLed")
GIF_IDLE = os.path.join(GIF_DIR, "style2", "blink.gif")


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
    print("✅  Matrix LED initialisée")

def show_gif(gif_path: str) -> None:
    if not MATRIX_AVAILABLE:
        return
    if not os.path.exists(gif_path):
        print(f"⚠️  GIF introuvable : {gif_path}")
        return
    gifViewer(gif_path, matrix)

def clear_matrix() -> None:
    if MATRIX_AVAILABLE and matrix:
        matrix.Clear()
        print("🖥️  Matrix effacée")


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
        send_mode(False)  # repasse en mode manuel
        print(f"✅  Arrivé station {station_actuelle} ({dest_id})")
        tts("Je suis arrivé à destination.")


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


TMP_DIR = tempfile.gettempdir()

def tts(text: str) -> None:
    threading.Thread(target=speak, args=(text,), daemon=True).start()


# ─────────────────────────────────────────────
#  CONTEXTE LLM DYNAMIQUE
# ─────────────────────────────────────────────
def build_extra_context() -> str:
    return f"""ÉTAT ACTUEL DU ROBOT :
- Mode guide (suivi de ligne) : {"ACTIF" if line_following else "INACTIF"}
- Position actuelle : {robot_state.get("current", "inconnue")}
- Destination en cours : {robot_state.get("target", "aucune")}

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
        dest_raw = payload.get("destination", "")
        dest_id  = DESTINATION_MAP.get(dest_raw, dest_raw.lower())
        num      = STATION_NUMBERS.get(dest_id)
        if num is not None:
            destination_cible     = num
            current_move          = 150
            current_turn          = 0
            send_mode(True)
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
        gif_path = os.path.join(GIF_DIR, f"style{style}", "blink.gif")
        show_gif(gif_path)
        result["style"] = style

    elif action == "shutdown":
        robot_state["mode"] = "idle"
        clear_matrix()
        show_gif(GIF_IDLE)
        result["mode"] = "idle"

    else:
        result["ok"]    = False
        result["error"] = f"Action inconnue : {action}"
        print(f"⚠️  Action inconnue : {action}")

    return result


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

    web_to_llm = {v: k for k, v in DESTINATION_MAP.items()}
    dest_name  = web_to_llm.get(destination, destination)

    llm_result = ask_ollama(
        f"Je dois me déplacer vers {dest_name}. Confirme brièvement.",
        extra_context=build_extra_context()
    )

    ai_reply = (
        llm_result.get("response", f"Je me dirige vers {dest_name}.")
        if llm_result else f"Je me dirige vers {dest_name}."
    )

    # ← N'exécute l'action QUE si le LLM génère une commande
    if llm_result and llm_result.get("type") == "commande":
        execute_action(llm_result)

    tts(ai_reply)

    return jsonify({
        "robot_state": full_state(),
        "ai_reply":    ai_reply,
    })


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

    return jsonify({"robot_state": full_state()})


# ─────────────────────────────────────────────
#  ROUTE : Texte direct (fallback Web Speech)
# ─────────────────────────────────────────────
@app.route("/send_text", methods=["POST"])
def send_text():
    data      = request.get_json()
    user_text = data.get("user_text", "")

    llm_result = ask_ollama(user_text, extra_context=build_extra_context())
    if not llm_result:
        return jsonify({"error": "LLM indisponible"}), 503

    ai_reply = llm_result.get("response", "")
    action   = llm_result.get("action")

    if llm_result.get("type") == "commande" and action:
        execute_action(llm_result)

    tts(ai_reply)

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
        tts("Je n'ai pas compris.")
        return jsonify({
            "transcript":  "",
            "ai_reply":    "Je n'ai pas compris.",
            "robot_state": full_state(),
        })

    llm_result = ask_ollama(transcript, extra_context=build_extra_context())
    if not llm_result:
        return jsonify({"error": "LLM indisponible"}), 503

    ai_reply = llm_result.get("response", "")
    action   = llm_result.get("action")

    if llm_result.get("type") == "commande" and action:
        execute_action(llm_result)

    tts(ai_reply)

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
    show_gif(GIF_IDLE)
    WEB_DIR = os.path.dirname(os.path.abspath(__file__))
    app.run(host="0.0.0.0", port=5000, debug=False, ssl_context=(
        os.path.join(WEB_DIR, 'cert.pem'),
        os.path.join(WEB_DIR, 'key.pem')
    ))
