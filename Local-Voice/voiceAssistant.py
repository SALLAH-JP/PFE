#!/usr/bin/env python3
"""
MARC Robot — voiceAssistant.py
Pipeline vocal : Wake word → Ollama (JSON strict) → HTTP → server.py
"""

import json
import time
import requests
import subprocess
import tempfile
import os
import speech_recognition as sr
from gtts import gTTS

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_MODEL  = "mistral-large-3:675b-cloud"
SERVER_URL    = "http://localhost:5000"

WAKE_WORDS    = ["salut marc", "salut marque", "salut mac"]
STOP_WORDS    = ["merci", "merci marc"]

SYSTEM_PROMPT = """Tu es MARC, un robot assistant vocal dans un laboratoire de robotique.
Réponds UNIQUEMENT en JSON valide, sans texte avant ou après, sans balises markdown.

Format si c'est une conversation normale :
{"type": "chat", "response": "Ta réponse courte ici, max 2 phrases."}

Format si c'est une commande de déplacement ou d'action :
{"type": "commande", "response": "Confirmation courte.", "action": "<action>", ...}

Actions disponibles et leurs paramètres optionnels :
- moveTo       → ajouter obligatoirement "destination": "Imprimante3D" | "Nao" | "brasRobotique"
- moveForward  → ajouter optionnellement "temps": <secondes>
- moveBackward → ajouter optionnellement "temps": <secondes>
- turnLeft     → ajouter optionnellement "temps": <secondes>
- turnRight    → ajouter optionnellement "temps": <secondes>
- changeEyes   → ajouter optionnellement "style": 1 | 2
- turn         → aucun paramètre supplémentaire
- shutdown     → aucun paramètre supplémentaire

Exemples :
Utilisateur : "va chez Nao"
{"type": "commande", "response": "Je me dirige vers Nao.", "action": "moveTo", "destination": "Nao"}

Utilisateur : "avance pendant 3 secondes"
{"type": "commande", "response": "J'avance pendant 3 secondes.", "action": "moveForward", "temps": 3}

Utilisateur : "change tes yeux en style 2"
{"type": "commande", "response": "Je change mes yeux.", "action": "changeEyes", "style": 2}

Utilisateur : "mets-toi en veille"
{"type": "commande", "response": "Bonne nuit.", "action": "shutdown"}

Utilisateur : "comment tu vas ?"
{"type": "chat", "response": "Je vais très bien, merci !"}
"""

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

def ask_ollama(user_text: str) -> dict | None:
    """
    Envoie le texte à Ollama et retourne le JSON parsé.
    Retourne None en cas d'erreur.
    """
    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

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
#  TTS — gTTS + mpg123
# ─────────────────────────────────────────────
def speak(text: str) -> None:
    print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        gTTS(text=text, lang="fr").save(tmp)
        subprocess.run(["mpg123", "-q", tmp], check=False)
        os.unlink(tmp)
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
            timeout=10
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
