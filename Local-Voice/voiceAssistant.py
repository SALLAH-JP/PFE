#!/usr/bin/env python3
"""
MARC Robot — voiceAssistant.py
Pipeline vocal : Wake word → Ollama (JSON strict) → HTTP → server.py
"""

import urllib3
import json
import time
import requests
import subprocess
import tempfile
import os
import speech_recognition as sr
from gtts import gTTS
from pathlib import Path



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_MODEL  = "mistral-large-3:675b-cloud"
SERVER_URL    = "https://localhost:5000"

WAKE_WORDS    = ["salut marc", "salut marque", "salut mac"]
STOP_WORDS    = ["merci", "merci marc"]

BASE_DIR = Path(__file__).parent
with open(BASE_DIR / "modelfile.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# ─────────────────────────────────────────────
#  ÉTATS
# ─────────────────────────────────────────────
STATE_IDLE   = "idle"
STATE_ACTIVE = "active"

# ─────────────────────────────────────────────
#  INITIALISATION STT
# ─────────────────────────────────────────────
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = 0.5

def calibrate_mic():
    try:
        with sr.Microphone(device_index=1) as source:
            print("🎙️  Calibration micro...")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅  Calibration terminée")
    except Exception as e:
        print(f"⚠️  Calibration échouée : {e}")


# ─────────────────────────────────────────────
#  STT — Google
# ─────────────────────────────────────────────
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
#  LLM — Ollama (JSON strict)
# ─────────────────────────────────────────────
conversation_history: list[dict] = []

def ask_ollama(user_text: str, extra_context: str = "") -> dict | None:
    """
    Envoie le texte à Ollama et retourne le JSON parsé.
    Retourne None en cas d'erreur.
    """

    system = SYSTEM_PROMPT
    if extra_context:
        system = SYSTEM_PROMPT + "\n\n---\n\n" + extra_context

    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system}] + conversation_history

    full_response = ""
    try:
        with requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": messages, "keep_alive": -1, "stream": True},
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
                if chunk.get("done"):
                    break

        conversation_history.append({"role": "assistant", "content": full_response})

        # Nettoyage des balises markdown si le modèle en ajoute quand même
        clean = full_response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()

        parsed = json.loads(clean)
        print(f"🤖  MARC JSON : {json.dumps(parsed, ensure_ascii=False)}")
        return parsed

    except json.JSONDecodeError as e:
        print(f"❌  JSON invalide reçu d'Ollama : {e}\nRéponse brute : {full_response}")
        return {"type": "chat", "response": "Désolé, je n'ai pas pu traiter ça correctement."}
    except requests.exceptions.ConnectionError:
        print("❌  Ollama inaccessible")
        return None
    except Exception as e:
        print(f"❌  Erreur Ollama : {e}")
        return None


# ─────────────────────────────────────────────
#  TTS — gTTS + mpg123 or PIPER
# ─────────────────────────────────────────────
PIPER_EXE   = BASE_DIR / "piper" / "piper"          # ou "piper.exe" sur Windows
PIPER_MODEL = BASE_DIR / "piper" / "fr_FR-siwis-medium.onnx"
def speak(text: str) -> None:
    print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        gTTS(text=text, lang="fr").save(tmp)
        subprocess.run(["mpg123", "-q", "--scale", "65536", tmp], check=False)
        os.unlink(tmp)
    except Exception as e:
        print(f"⚠️  TTS erreur : {e}")



def speak2(text: str) -> None:
    print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
    try:
        piper = subprocess.Popen(
            ["piper", "--model", PIPER_MODEL, "--output-raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        aplay = subprocess.Popen(
            ["aplay", "-r", "22050", "-f", "S16_LE", "-c", "1", "-q"],
            stdin=piper.stdout
        )
        piper.stdin.write(text.encode())
        piper.stdin.close()
        aplay.wait()
    except Exception as e:
        print(f"⚠️  TTS erreur : {e}")


# ─────────────────────────────────────────────
#  ENVOI COMMANDE AU SERVEUR
# ─────────────────────────────────────────────
def send_command_to_server(payload: dict) -> bool:
    """
    Envoie le payload JSON au serveur Flask /vocal_command.
    Retourne True si succès.
    """
    try:
        resp = requests.post(
            f"{SERVER_URL}/vocal_command",
            json=payload,
            timeout=10,
            verify=False  # certificat auto-signé
        )

        resp.raise_for_status()
        print(f"✅  Serveur : {resp.json()}")
        return True
    except Exception as e:
        print(f"❌  Erreur envoi serveur : {e}")
        return False


# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
def main():
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  MARC  —  Google STT → Ollama JSON → Flask        ║")
    print("║  Wake word : 'Salut Marc'                         ║")
    print("║  Stop      : 'Merci'                              ║")
    print("║  Ctrl+C pour quitter                              ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Vérification Ollama
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"✅  Ollama — modèles disponibles : {models}")
        if not any(OLLAMA_MODEL in m for m in models):
            print(f"⚠️  Modèle '{OLLAMA_MODEL}' manquant — lancez : ollama pull {OLLAMA_MODEL}")
    except Exception:
        print("❌  Ollama inaccessible — lancez : ollama serve")
        return

    calibrate_mic()

    state = STATE_IDLE
    print("\n😴  En attente de 'Salut Marc'…\n")

    while True:
        try:
            text = listen_once()
            if not text:
                continue

            # ── MODE IDLE ──
            if state == STATE_IDLE:
                if any(w in text for w in WAKE_WORDS):
                    print(f"🟢  Wake word détecté : '{text}'")
                    state = STATE_ACTIVE
                    conversation_history.clear()
                    speak("Oui, je vous écoute.")
                    print("💬  Mode actif — dites 'Merci' pour terminer\n")
                else:
                    print(f"\r😴  (ignoré : '{text}')", end="", flush=True)

            # ── MODE ACTIF ──
            elif state == STATE_ACTIVE:
                print(f"\n👤  Vous : {text}")

                # Mot de stop → retour en veille
                if any(w in text for w in STOP_WORDS):
                    speak("De rien, à bientôt !")
                    state = STATE_IDLE
                    conversation_history.clear()
                    # Notifier le serveur du shutdown propre
                    send_command_to_server({"type": "commande", "action": "shutdown", "response": "Mise en veille."})
                    print("\n😴  En attente de 'Salut Marc'…\n")
                    continue

                result = ask_ollama(text)
                if result is None:
                    speak("Je n'arrive pas à me connecter.")
                    continue

                response_text = result.get("response", "")

                if result.get("type") == "commande":
                    # Envoyer la commande au serveur
                    sent = send_command_to_server(result)
                    if not sent:
                        speak("Je n'ai pas pu exécuter la commande.")
                    else:
                        if response_text:
                            speak(response_text)
                    # Shutdown → retour en veille
                    if result.get("action") == "shutdown":
                        state = STATE_IDLE
                        conversation_history.clear()
                        print("\n😴  En attente de 'Salut Marc'…\n")

                elif result.get("type") == "chat":
                    if response_text:
                        speak(response_text)

                else:
                    print(f"⚠️  Type inconnu : {result.get('type')}")
                    if response_text:
                        speak(response_text)

        except KeyboardInterrupt:
            print("\n\n👋  Arrêt.")
            break
        except Exception as e:
            print(f"\n⚠️  Erreur inattendue : {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
