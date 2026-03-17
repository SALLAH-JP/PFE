#!/usr/bin/env python3
"""
Pipeline vocal : Wake word "Salut Nash" → discussion → "Merci"
STT (Vosk small FR) → LLM (Ollama qwen2.5:1.5b) → TTS (pyttsx3)
"""

import json
import subprocess
import time
import threading
import requests
import pyaudio
import pyttsx3
from vosk import Model, KaldiRecognizer

import sys
sys.path.append("/home/pi/PFE/matrixLed")

from gif_viewer import gifViewer


# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL    = "http://11.0.0.103:11434/api/chat"
OLLAMA_MODEL  = "qwen2.5:1.5b"
VOSK_MODEL    = "vosk-model"
SAMPLE_RATE   = 44100
CHUNK         = 4096

WAKE_WORDS    = ["salut nash", "salut nache", "salut nasch"]
STOP_WORDS    = ["merci", "merci nash"]

SYSTEM_PROMPT = (
    "Tu es Nash, un robot assistant vocal. "
    "Réponds de façon très courte, maximum 2 phrases. "
    "Tu peux recevoir des ordres de déplacement : avance, recule, "
    "tourne à gauche, tourne à droite, stop."
)

# ─────────────────────────────────────────────
#  ÉTATS
# ─────────────────────────────────────────────
STATE_IDLE   = "idle"
STATE_ACTIVE = "active"

# ─────────────────────────────────────────────
#  INITIALISATION
# ─────────────────────────────────────────────
print("⏳  Chargement de Vosk…")
vosk_model = Model(VOSK_MODEL)
print("✅  Vosk prêt.")

print("⏳  Initialisation pyttsx3…")
tts_engine = pyttsx3.init()
tts_engine.setProperty("rate", 170)
tts_engine.setProperty("volume", 1.0)

# Voix française si disponible
for voice in tts_engine.getProperty("voices"):
    if "fr" in voice.id.lower() or "french" in voice.name.lower():
        tts_engine.setProperty("voice", voice.id)
        print(f"✅  Voix française : {voice.name}")
        break

conversation_history: list[dict] = []


# ─────────────────────────────────────────────
#  AUDIO
# ─────────────────────────────────────────────
def open_stream(pa: pyaudio.PyAudio):
    device_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and "usb" in info["name"].lower():
            device_index = i
            print(f"✅  Micro : {info['name']}")
            break

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=CHUNK
    )
    return stream


# ─────────────────────────────────────────────
#  STT — Vosk
# ─────────────────────────────────────────────
def listen_once(stream, recognizer: KaldiRecognizer) -> str | None:
    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            return text if text else None

        partial = json.loads(recognizer.PartialResult()).get("partial", "")
        if partial:
            print(f"\r   {partial}…", end="", flush=True)


# ─────────────────────────────────────────────
#  LLM — Ollama
# ─────────────────────────────────────────────
def ask_ollama(user_text: str) -> str:
    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

    print("🤖  Nash : ", end="", flush=True)
    full_response = ""

    try:
        with requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": True},
            stream=True,
            timeout=60
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                token = chunk.get("message", {}).get("content", "")
                full_response += token
                print(token, end="", flush=True)
                if chunk.get("done"):
                    break

        print()
        conversation_history.append({"role": "assistant", "content": full_response})
        return full_response

    except requests.exceptions.ConnectionError:
        print("\n❌  Ollama inaccessible")
        return ""
    except Exception as e:
        print(f"\n❌  Erreur : {e}")
        return ""


# ─────────────────────────────────────────────
#  TTS — pyttsx3
# ─────────────────────────────────────────────
def speak(text: str) -> None:
    print(f"🔊  {text[:60]}{'…' if len(text) > 60 else ''}")
    tts_engine.say(text)
    tts_engine.runAndWait()


# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
def main():
    print("\n╔══════════════════════════════════════════════╗")
    print("║  Nash  —  Vosk → qwen2.5:1.5b → pyttsx3     ║")
    print("║  Wake word : 'Salut Nash'                     ║")
    print("║  Stop      : 'Merci'                          ║")
    print("║  Ctrl+C pour quitter                          ║")
    print("╚══════════════════════════════════════════════╝\n")


    gifViewer("/home/pi/PFE/matrixLed/style2/blink.gif")

    # Vérification Ollama
    try:
        resp = requests.get("http://11.0.0.103:11434/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"✅  Ollama — modèles : {models}")
        if not any(OLLAMA_MODEL in m for m in models):
            print(f"⚠️  Modèle manquant — lancez : ollama pull {OLLAMA_MODEL}")
    except Exception:
        print("❌  Ollama inaccessible — lancez : ollama serve")
        return

    pa = pyaudio.PyAudio()
    state = STATE_ACTIVE

    recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
    stream = open_stream(pa)

    print("\n😴  En attente de 'Salut Nash'…")

    while True:
        try:
            text = listen_once(stream, recognizer)
            if not text:
                continue

            # ── MODE IDLE ──
            if state == STATE_IDLE:
                if any(w in text.lower() for w in WAKE_WORDS):
                    print(f"\n🟢  Wake word détecté : '{text}'")
                    state = STATE_ACTIVE
                    conversation_history.clear()
                    speak("Oui, je vous écoute.")
                    print("💬  Mode discussion — dites 'Merci' pour terminer\n")
                else:
                    print(f"\r😴  (ignoré : '{text}')", end="", flush=True)

            # ── MODE ACTIF ──
            elif state == STATE_ACTIVE:
                print(f"\n👤  Vous : {text}")

                if any(w in text.lower() for w in STOP_WORDS):
                    speak("De rien, à bientôt !")
                    state = STATE_IDLE
                    conversation_history.clear()
                    print("\n😴  En attente de 'Salut Nash'…")
                    continue

                response = ask_ollama(text)
                if response:
                    speak(response)

        except KeyboardInterrupt:
            print("\n\n👋  Arrêt du robot.")
            break
        except Exception as e:
            print(f"\n⚠️  Erreur : {e}")
            time.sleep(1)

    stream.stop_stream()
    stream.close()
    pa.terminate()


if __name__ == "__main__":
    main()
