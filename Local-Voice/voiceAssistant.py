#!/usr/bin/env python3
"""
Pipeline vocal : Wake word "Salut Nash" → discussion → "Merci"
STT (SpeechRecognition + Google) → LLM (Ollama qwen2.5:1.5b) → TTS (pyttsx3)
"""

import json
import time
import requests
import speech_recognition as sr
from gtts import gTTS
import subprocess
import tempfile
import os

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_MODEL  = "mistral-large-3:675b-cloud"

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
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = 0.5

conversation_history: list[dict] = []


# ─────────────────────────────────────────────
#  STT — Google
# ─────────────────────────────────────────────

with sr.Microphone(device_index=1) as source:
    print("🎙️  Calibration...")
    recognizer.adjust_for_ambient_noise(source, duration=1)
    print("✅  Calibration terminée")


def listen_once() -> str | None:
    try:
        with sr.Microphone(device_index=1) as source:
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)

        text = recognizer.recognize_google(audio, language="fr-FR")
        return text.strip().lower()

    except sr.WaitTimeoutError:
        return None
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"❌  Erreur Google STT : {e}")
        return None


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
        print("\n❌  Ollama inaccessible — lancez : ollama serve")
        return ""
    except Exception as e:
        print(f"\n❌  Erreur : {e}")
        return ""


# ─────────────────────────────────────────────
#  TTS — pyttsx3
# ─────────────────────────────────────────────
def speak2(text: str) -> None:
    print(f"🔊  {text[:60]}{'…' if len(text) > 60 else ''}")
    engine = pyttsx3.init()
    engine.setProperty("rate", 170)
    engine.setProperty("volume", 1.0)
    for voice in engine.getProperty("voices"):
        if "fr" in voice.id.lower() or "french" in voice.name.lower():
            engine.setProperty("voice", voice.id)
            break
    engine.say(text)
    engine.runAndWait()
    engine.stop()


def speak(text: str) -> None:
    print(f"🔊  {text[:60]}{'…' if len(text) > 60 else ''}")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp = f.name
    gTTS(text=text, lang="fr").save(tmp)
    subprocess.run(["mpg123", "-q", tmp])
    os.unlink(tmp)

# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
def main():
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Nash  —  Google STT → mistral-large- → pyttsx3   ║")
    print("║  Wake word : 'Salut Nash'                         ║")
    print("║  Stop      : 'Merci'                              ║")
    print("║  Ctrl+C pour quitter                              ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Vérification Ollama
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"✅  Ollama — modèles : {models}")
        if not any(OLLAMA_MODEL in m for m in models):
            print(f"⚠️  Modèle manquant — lancez : ollama pull {OLLAMA_MODEL}")
    except Exception:
        print("❌  Ollama inaccessible — lancez : ollama serve")
        return

    state = STATE_IDLE
    print("\n😴  En attente de 'Salut Nash'…")

    while True:
        try:
            text = listen_once()
            if not text:
                continue

            # ── MODE IDLE ──
            if state == STATE_IDLE:
                if any(w in text for w in WAKE_WORDS):
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

                if any(w in text for w in STOP_WORDS):
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


if __name__ == "__main__":
    main()
