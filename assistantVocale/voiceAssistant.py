#!/usr/bin/env python3
"""
MARC Robot — voiceAssistant.py
Pipeline vocal EN FLUX (streaming) :
    Wake word → Ollama (JSON, streamé token par token)
              → extraction au vol de la clé "response"
              → TTS edge-tts streamé phrase par phrase (lecture pendant la génération)
              → HTTP → server.py (exécution de la commande)

MARC commence à parler AVANT que le LLM ait fini de générer, et la synthèse
vocale démarre avant la fin de chaque phrase : la latence perçue chute fortement.
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

import re
import queue
import asyncio
import threading
import edge_tts



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_MODEL  = "ministral-3:14b-cloud"
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

def ask_ollama2(user_text: str, extra_context: str = "") -> dict | None:
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
        return {"type": "chat", "response": "Désolé, je n'ai pas pu traiter ça correctement."}
    except Exception as e:
        print(f"❌  Erreur Ollama : {e}")
        return {"type": "chat", "response": "Désolé, je n'ai pas pu traiter ça correctement."}



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

    MAX_RETRIES = 3
    full_response = ""

    for attempt in range(1, MAX_RETRIES + 1):
        full_response = ""
        try:
            with requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "messages": messages, "keep_alive": -1, "stream": True},
                stream=True,
                timeout=60
            ) as resp:
                # 5xx = erreur serveur transitoire → retry avec backoff
                if 500 <= resp.status_code < 600:
                    if attempt < MAX_RETRIES:
                        wait = 1.5 ** attempt
                        print(f"⚠️  Ollama {resp.status_code} (tentative {attempt}/{MAX_RETRIES}) → retry dans {wait:.1f}s")
                        time.sleep(wait)
                        continue
                    print(f"❌  Ollama {resp.status_code} après {MAX_RETRIES} tentatives")
                    return {"type": "chat", "response": "Désolé, mon cerveau est temporairement indisponible. Réessayez dans un instant."}

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
            return {"type": "chat", "response": "Désolé, je n'ai pas pu traiter ça correctement."}
        except Exception as e:
            print(f"❌  Erreur Ollama : {e}")
            return {"type": "chat", "response": "Désolé, je n'ai pas pu traiter ça correctement."}

    return {"type": "chat", "response": "Désolé, je n'ai pas pu traiter ça correctement."}



# ─────────────────────────────────────────────
#  TTS — gTTS + mpg123 or PIPER
# ─────────────────────────────────────────────

import edge_tts
import asyncio

def speak(text: str) -> None:
    print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
    try:
        asyncio.run(edge_tts.Communicate(text, voice="fr-FR-HenriNeural").save("/tmp/marc_tts.mp3"))
        subprocess.run(["mpg123", "-q", "/tmp/marc_tts.mp3"])
        os.unlink("/tmp/marc_tts.mp3")
    except Exception as e:
        print(f"⚠️  TTS erreur : {e}")


def speak1(text: str) -> None:
    print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        gTTS(text=text, lang="fr").save(tmp)
        subprocess.run(["mpg123", "-q", "--scale", "65536", tmp], check=False)
        os.unlink(tmp)
    except Exception as e:
        print(f"⚠️  TTS erreur : {e}")



PIPER_EXE   = BASE_DIR / "piper" / "piper"
PIPER_MODEL = BASE_DIR / "piper" / "fr_FR-siwis-low.onnx"
PIPER_DATA  = BASE_DIR / "piper" / "espeak-ng-data"

import wave
import io
import pyaudio

CHUNK = 1024
RATE  = 16000

def speak2(text: str) -> None:
    print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
    try:
        # 1. Génère le PCM avec piper
        piper_proc = subprocess.Popen(
            [
                str(PIPER_EXE),
                "--model",        str(PIPER_MODEL),
                "--output-raw"
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        tts_pcm, _ = piper_proc.communicate(input=text.encode())

        # 2. Convertit PCM raw → WAV via sox
        sox_proc = subprocess.Popen(
            ["sox", "-t", "raw", "-r", "16000", "-c", "1", "-b", "16",
             "-e", "signed-integer", "-", "-t", "wav", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        wav_bytes, _ = sox_proc.communicate(input=tts_pcm)

        # 3. Lecture via pyaudio
        wf     = wave.open(io.BytesIO(wav_bytes), "rb")
        pa     = pyaudio.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(),
            rate=wf.getframerate(),
            output=True
        )
        data = wf.readframes(CHUNK)
        while data:
            stream.write(data)
            data = wf.readframes(CHUNK)

        stream.stop_stream()
        stream.close()
        pa.terminate()
        wf.close()

    except Exception as e:
        print(f"⚠️  TTS erreur : {e}")


# ═════════════════════════════════════════════
#  TTS EN FLUX  (edge-tts → mpg123, phrase par phrase)
# ═════════════════════════════════════════════
TTS_VOICE = "fr-FR-HenriNeural"


class TTSPlayer:
    """
    Lecteur vocal en flux.

    On enfile des phrases avec .say(texte) ; un thread worker les synthétise
    et les joue dans l'ordre (FIFO), une à la fois. Chaque phrase est elle-même
    streamée : edge-tts produit des morceaux MP3 écrits directement dans
    l'entrée standard de mpg123, donc la lecture commence avant la fin de la
    synthèse. Combiné au LLM streamé, MARC parle pendant qu'il « réfléchit ».
    """

    def __init__(self, voice: str = TTS_VOICE):
        self.voice = voice
        self._q: "queue.Queue[str]" = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._q.put(text)

    def wait(self) -> None:
        """Bloque jusqu'à ce que toutes les phrases en file soient jouées."""
        self._q.join()

    def _run(self) -> None:
        while True:
            text = self._q.get()
            try:
                asyncio.run(self._speak_stream(text))
            except Exception as e:
                print(f"⚠️  TTS erreur : {e}")
            finally:
                self._q.task_done()

    async def _speak_stream(self, text: str) -> None:
        print(f"🔊  {text[:80]}{'…' if len(text) > 80 else ''}")
        player = subprocess.Popen(
            ["mpg123", "-q", "-"],          # « - » = lit le flux MP3 sur stdin
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            communicate = edge_tts.Communicate(text, voice=self.voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio" and chunk.get("data"):
                    player.stdin.write(chunk["data"])
            if player.stdin:
                player.stdin.close()
        finally:
            player.wait()


# ═════════════════════════════════════════════
#  EXTRACTION EN FLUX DU CHAMP "response" DU JSON
# ═════════════════════════════════════════════
class ResponseStreamer:
    """
    Extrait à la volée le texte de la clé "response" d'un flux JSON généré
    token par token par le LLM, et émet chaque phrase complète via le callback
    `on_sentence`. C'est ce qui permet de faire parler MARC AVANT la fin de la
    génération, sans casser le parse JSON final (type / action / paramètres).
    """

    _OPEN = re.compile(r'"response"\s*:\s*"')

    def __init__(self, on_sentence):
        self._on_sentence = on_sentence
        self.buf = ""           # flux JSON brut accumulé (pour le parse final)
        self._start = None      # index du début de la valeur "response"
        self._pos = None        # prochain index à analyser
        self._pending = ""      # phrase en cours (pas encore émise)
        self._decoded = ""      # texte "response" décodé complet (fallback)
        self._escape = False
        self._closed = False
        self.spoke = False      # True dès qu'au moins une phrase a été émise

    def feed(self, token: str) -> None:
        self.buf += token
        if self._start is None:
            m = self._OPEN.search(self.buf)
            if not m:
                return
            self._start = m.end()
            self._pos = self._start
        self._scan()

    def _scan(self) -> None:
        if self._closed:
            return
        n = len(self.buf)
        while self._pos < n:
            ch = self.buf[self._pos]
            self._pos += 1
            if self._escape:
                u = self._unescape(ch)
                self._pending += u
                self._decoded += u
                self._escape = False
                continue
            if ch == "\\":
                self._escape = True
                continue
            if ch == '"':                       # guillemet fermant → fin de "response"
                self._flush()
                self._closed = True
                return
            self._pending += ch
            self._decoded += ch
            # Frontière de phrase : ponctuation forte, en évitant les décimales
            if ch in "!?…\n":
                self._flush()
            elif ch == "." and not self._pending[-2:-1].isdigit():
                self._flush()

    @staticmethod
    def _unescape(ch: str) -> str:
        return {"n": "\n", "t": "\t", "r": "", '"': '"', "\\": "\\", "/": "/"}.get(ch, ch)

    def _flush(self) -> None:
        s = self._pending.strip()
        self._pending = ""
        if s:
            self.spoke = True
            self._on_sentence(s)

    def finalize(self) -> None:
        """Flux tronqué (pas de guillemet fermant reçu) → émet le reliquat."""
        if not self._closed:
            self._flush()

    @property
    def text(self) -> str:
        return self._decoded.strip()


# ═════════════════════════════════════════════
#  LLM — Ollama EN FLUX (parle pendant la génération)
# ═════════════════════════════════════════════
def ask_ollama_stream(user_text: str, on_sentence, extra_context: str = "") -> dict:
    """
    Version STREAMING de ask_ollama().

    Dès qu'Ollama envoie des tokens, on extrait au vol le contenu de la clé
    "response" et on appelle `on_sentence(phrase)` pour chaque phrase terminée.
    Le consommateur décide quoi en faire : le faire dire par MARC
    (TTSPlayer.say), le pousser au navigateur via SSE, ou les deux.

    Retourne le dict JSON complet (type / response / action / paramètres),
    avec une clé interne "_spoke" indiquant si la réponse a déjà été émise
    (pour éviter de la répéter chez l'appelant).
    """
    system = SYSTEM_PROMPT
    if extra_context:
        system = SYSTEM_PROMPT + "\n\n---\n\n" + extra_context

    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system}] + conversation_history

    MAX_RETRIES = 3

    for attempt in range(1, MAX_RETRIES + 1):
        full_response = ""
        streamer = ResponseStreamer(on_sentence=on_sentence)
        try:
            with requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "messages": messages, "keep_alive": -1, "stream": True},
                stream=True,
                timeout=60,
            ) as resp:
                # 5xx transitoire → retry tant qu'aucun token n'a encore été lu
                if 500 <= resp.status_code < 600:
                    if attempt < MAX_RETRIES:
                        wait = 1.5 ** attempt
                        print(f"⚠️  Ollama {resp.status_code} (tentative {attempt}/{MAX_RETRIES}) → retry dans {wait:.1f}s")
                        time.sleep(wait)
                        continue
                    print(f"❌  Ollama {resp.status_code} après {MAX_RETRIES} tentatives")
                    return {"type": "chat",
                            "response": "Désolé, mon cerveau est temporairement indisponible. Réessayez dans un instant.",
                            "_spoke": False}

                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_response += token
                        streamer.feed(token)        # ← peut déclencher tts.say(...)
                    if chunk.get("done"):
                        break

            streamer.finalize()                     # émet l'éventuelle dernière phrase
            conversation_history.append({"role": "assistant", "content": full_response})

            # Parse JSON complet → type / action / paramètres
            clean = full_response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()

            parsed = json.loads(clean)
            parsed["_spoke"] = streamer.spoke
            shown = {k: v for k, v in parsed.items() if k != "_spoke"}
            print(f"🤖  MARC JSON : {json.dumps(shown, ensure_ascii=False)}")
            return parsed

        except json.JSONDecodeError as e:
            print(f"❌  JSON invalide reçu d'Ollama : {e}\nRéponse brute : {full_response}")
            # On a peut-être quand même lu/parlé du texte exploitable
            fallback = streamer.text or "Désolé, je n'ai pas pu traiter ça correctement."
            if not streamer.spoke:
                on_sentence(fallback)
            return {"type": "chat", "response": fallback, "_spoke": True}
        except requests.exceptions.ConnectionError:
            print("❌  Ollama inaccessible")
            return {"type": "chat",
                    "response": "Désolé, je n'ai pas pu traiter ça correctement.",
                    "_spoke": False}
        except Exception as e:
            print(f"❌  Erreur Ollama : {e}")
            return {"type": "chat",
                    "response": "Désolé, je n'ai pas pu traiter ça correctement.",
                    "_spoke": False}

    return {"type": "chat",
            "response": "Désolé, je n'ai pas pu traiter ça correctement.",
            "_spoke": False}


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

    tts = TTSPlayer()          # lecteur vocal en flux (thread worker FIFO)

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
                    tts.say("Oui, je vous écoute.")
                    tts.wait()        # finir de parler avant de réécouter
                    print("💬  Mode actif — dites 'Merci' pour terminer\n")
                else:
                    print(f"\r😴  (ignoré : '{text}')", end="", flush=True)

            # ── MODE ACTIF ──
            elif state == STATE_ACTIVE:
                print(f"\n👤  Vous : {text}")

                # Mot de stop → retour en veille
                if any(w in text for w in STOP_WORDS):
                    tts.say("De rien, à bientôt !")
                    tts.wait()
                    state = STATE_IDLE
                    conversation_history.clear()
                    # Notifier le serveur du shutdown propre
                    send_command_to_server({"type": "commande", "action": "shutdown", "response": "Mise en veille."})
                    print("\n😴  En attente de 'Salut Marc'…\n")
                    continue

                # LLM EN FLUX : MARC parle déjà pendant la génération
                result = ask_ollama_stream(text, tts.say)
                spoke = result.pop("_spoke", False)     # réponse déjà dite ?
                response_text = result.get("response", "")

                if result.get("type") == "commande":
                    # La confirmation a (en général) déjà été dite en flux ;
                    # on envoie ensuite la commande au serveur.
                    sent = send_command_to_server(result)
                    if not sent:
                        tts.say("Je n'ai pas pu exécuter la commande.")
                    elif not spoke and response_text:
                        tts.say(response_text)
                    # Shutdown → retour en veille
                    if result.get("action") == "shutdown":
                        state = STATE_IDLE
                        conversation_history.clear()
                        print("\n😴  En attente de 'Salut Marc'…\n")

                elif result.get("type") == "chat":
                    if not spoke and response_text:
                        tts.say(response_text)

                else:
                    print(f"⚠️  Type inconnu : {result.get('type')}")
                    if not spoke and response_text:
                        tts.say(response_text)

                # Attendre la fin de la parole avant de réécouter
                # (évite que le micro capte la voix de MARC)
                tts.wait()

        except KeyboardInterrupt:
            print("\n\n👋  Arrêt.")
            break
        except Exception as e:
            print(f"\n⚠️  Erreur inattendue : {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
