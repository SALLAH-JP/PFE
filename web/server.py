#!/usr/bin/env python3
"""
MARC Robot — server.py
Serveur Flask : STT (Google Speech Recognition) + LLM (Ollama) + TTS (gTTS)
"""

import os
import tempfile
import requests
import speech_recognition as sr
from pydub import AudioSegment
from flask import Flask, request, jsonify, send_from_directory, send_file
from gtts import gTTS

recognizer = sr.Recognizer()
print("✅ Google Speech Recognition prêt")

app = Flask(__name__, static_folder=".")

TMP_DIR = tempfile.gettempdir()

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "mistral-large-3:675b-cloud"

SYSTEM_PROMPT = (
    "Tu es Nash, un robot assistant vocal qui guide les visiteurs dans un laboratoire de robotique. "
    "Réponds de façon très courte, maximum 2 phrases. "
    "Les stations disponibles sont : Nao, Imprimante 3D, Pepper, Robot 3, Robot 4, Robot 5, Robot 6. "
    "Si l'utilisateur mentionne une destination, extrais-la et réponds naturellement."
)

VOICE_MAP = {
    'nao':        'nao',
    'imprimante': 'imp3d',
    'impression': 'imp3d',
    '3d':         'imp3d',
    'pepper':     'pepper',
    'robot 3':    'robot3',
    'troisième':  'robot3',
    'robot 4':    'robot4',
    'quatrième':  'robot4',
    'robot 5':    'robot5',
    'cinq':       'robot5',
    'robot 6':    'robot6',
    'six':        'robot6',
}

robot_state = {
    "current": "nao",
    "target":  None,
}

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def detect_destination(text: str):
    lower = text.lower()
    for keyword, station in VOICE_MAP.items():
        if keyword in lower:
            return station
    return None


def ask_ollama(user_text: str) -> str:
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_text},
                ],
                "stream": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Ollama erreur : {e}")
        return "Je n'ai pas pu traiter ta demande."


def make_tts(text: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=TMP_DIR, prefix="nash_tts_")
    gTTS(text=text, lang="fr").save(tmp.name)
    return tmp.name


def convert_to_wav(input_path: str):
    """Convertit un fichier audio (webm/ogg/mp4) en WAV pour SpeechRecognition."""
    output_path = input_path.rsplit(".", 1)[0] + ".wav"
    try:
        audio = AudioSegment.from_file(input_path)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(output_path, format="wav")
        return output_path
    except Exception as e:
        print(f"❌ Conversion audio erreur : {e}")
        return None


# ─────────────────────────────────────────────
#  ROUTES STATIQUES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)


# ─────────────────────────────────────────────
#  ÉTAT DU ROBOT
# ─────────────────────────────────────────────
@app.route("/status")
def status():
    return jsonify({"robot_state": robot_state})


# ─────────────────────────────────────────────
#  COMMANDE DE DÉPLACEMENT (clic carte/SVG)
# ─────────────────────────────────────────────
@app.route("/command", methods=["POST"])
def command():
    data        = request.get_json()
    destination = data.get("destination")

    if not destination:
        return jsonify({"error": "destination manquante"}), 400

    robot_state["target"] = destination
    ai_reply = ask_ollama(f"Je dois me déplacer vers {destination}. Confirme brièvement.")

    tts_file = make_tts(ai_reply)
    tts_key  = os.path.basename(tts_file)

    robot_state["current"] = destination
    robot_state["target"]  = None

    return jsonify({
        "robot_state": robot_state,
        "ai_reply":    ai_reply,
        "tts_url":     f"/tts/{tts_key}",
    })


# ─────────────────────────────────────────────
#  TRANSCRIPTION AUDIO → Google STT + LLM
# ─────────────────────────────────────────────
@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "Aucun fichier audio"}), 400

    audio_file = request.files["audio"]
    mimetype   = audio_file.mimetype or ""

    if "webm" in mimetype:
        suffix = ".webm"
    elif "ogg" in mimetype:
        suffix = ".ogg"
    elif "mp4" in mimetype:
        suffix = ".mp4"
    else:
        suffix = ".webm"

    # Sauvegarder l'audio reçu
    tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=TMP_DIR)
    audio_file.save(tmp_input.name)
    tmp_input.close()

    # Convertir en WAV (nécessaire pour SpeechRecognition)
    wav_path = convert_to_wav(tmp_input.name)
    os.unlink(tmp_input.name)

    if not wav_path:
        return jsonify({"error": "Conversion audio échouée — installe ffmpeg"}), 500

    # Transcription Google
    transcript = ""
    try:
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
        transcript = recognizer.recognize_google(audio_data, language="fr-FR")
        print(f"✅ Transcription : {transcript}")
    except sr.UnknownValueError:
        print("⚠️ Audio incompréhensible")
    except sr.RequestError as e:
        print(f"❌ Google STT erreur : {e}")
        return jsonify({"error": "Google STT indisponible"}), 503
    finally:
        try: os.unlink(wav_path)
        except: pass

    if not transcript:
        return jsonify({
            "transcript":  "",
            "ai_reply":    "Je n'ai pas compris.",
            "destination": None,
            "robot_state": robot_state,
        })

    destination = detect_destination(transcript)
    ai_reply    = ask_ollama(transcript)

    if destination:
        robot_state["current"] = destination
        robot_state["target"]  = None

    tts_file = make_tts(ai_reply)
    tts_key  = os.path.basename(tts_file)

    return jsonify({
        "transcript":  transcript,
        "ai_reply":    ai_reply,
        "destination": destination,
        "robot_state": robot_state,
        "tts_url":     f"/tts/{tts_key}",
    })


# ─────────────────────────────────────────────
#  TEXTE DIRECT + LLM (fallback)
# ─────────────────────────────────────────────
@app.route("/send_text", methods=["POST"])
def send_text():
    data      = request.get_json()
    user_text = data.get("user_text", "")

    destination = detect_destination(user_text)
    ai_reply    = ask_ollama(user_text)

    if destination:
        robot_state["current"] = destination
        robot_state["target"]  = None

    tts_file = make_tts(ai_reply)
    tts_key  = os.path.basename(tts_file)

    return jsonify({
        "ai_reply":    ai_reply,
        "destination": destination,
        "robot_state": robot_state,
        "tts_url":     f"/tts/{tts_key}",
    })


# ─────────────────────────────────────────────
#  SERVIR LES FICHIERS TTS
# ─────────────────────────────────────────────
@app.route("/tts/<filename>")
def tts_file(filename):
    path = os.path.join(TMP_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"error": "fichier introuvable"}), 404
    return send_file(path, mimetype="audio/mpeg")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 MARC Robot server démarré sur http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
