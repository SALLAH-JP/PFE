#!/usr/bin/env python3
"""
Pipeline vocal : STT (SpeechRecognition + Google) → LLM (Ollama REST) → TTS (pyttsx3)
Dépendances : pip install SpeechRecognition pyttsx3 pyaudio requests
"""

import sys
import time
import json
import requests
import speech_recognition as sr
import pyttsx3
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "matrixLed"))

from gif_viewer import gifViewer

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL   = "http://11.0.0.44:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:1.5b"

SYSTEM_PROMPT = (
    "Tu es un assistant vocal concis et sympathique. "
    "Réponds toujours en moins de 3 phrases."
)

# ─────────────────────────────────────────────
#  INITIALISATION
# ─────────────────────────────────────────────
recognizer = sr.Recognizer()

tts_engine = pyttsx3.init()
tts_engine.setProperty("rate", 170)
tts_engine.setProperty("volume", 1.0)

for voice in tts_engine.getProperty("voices"):
    if "fr" in voice.id.lower() or "french" in voice.name.lower():
        tts_engine.setProperty("voice", voice.id)
        break

conversation_history: list[dict] = []


# ─────────────────────────────────────────────
#  1. STT
# ─────────────────────────────────────────────
def listen_and_transcribe() -> str | None:
    print("\n🎤  Parlez…")
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.2)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
        
        wav_data = audio.get_wav_data()
        audio_wav = sr.AudioData(wav_data, audio.sample_rate, audio.sample_width)

        text = recognizer.recognize_google(audio_wav, language="fr-FR")
        print(f"📝  Transcription : {text}")
        return text.strip()

    except sr.WaitTimeoutError:
        print("   (aucune parole détectée)")
        return None
    except sr.UnknownValueError:
        print("   (audio incompréhensible)")
        return None
    except sr.RequestError as e:
        print(f"❌  Erreur STT : {e}")
        return None


# ─────────────────────────────────────────────
#  2. LLM
# ─────────────────────────────────────────────
def ask_ollama(user_text: str) -> str:
    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

    print("🤖  Ollama : ", end="", flush=True)
    full_response = ""

    try:
        with requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "messages": messages, "stream": True}, stream=True, timeout=60) as resp:
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
        print(f"\n❌  Ollama inaccessible — lancez : ollama serve")
        return ""


# ─────────────────────────────────────────────
#  3. TTS
# ─────────────────────────────────────────────
def speak(text: str) -> None:
    print(f"🔊  {text[:60]}{'…' if len(text) > 60 else ''}")
    tts_engine.say(text)
    tts_engine.runAndWait()


# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
while True:
    try:
        user_text = listen_and_transcribe()
        if not user_text:
            continue

        if any(w in user_text.lower() for w in ["quitter", "exit", "au revoir", "stop"]):
            speak("Au revoir !")
            break

        response = ask_ollama(user_text)
        if response:
            speak(response)

    except KeyboardInterrupt:
        print("\n👋  Au revoir !")
        break