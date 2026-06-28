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

Version procédurale (sans classes) :
  - speak(text, wait=False) : seule API TTS (file FIFO + mpg123 persistant).
  - ask_ollama_stream(...)  : streaming JSON avec extraction au vol de "response",
                              état local (sans classe ResponseStreamer).
  - Le markdown (**, _, `…) est nettoyé avant d'être prononcé.
"""

import os
import re
import json
import time
import queue
import asyncio
import threading
import subprocess
import tempfile
import urllib3
import requests
import edge_tts
import speech_recognition as sr
from gtts import gTTS
from pathlib import Path


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

TTS_VOICE = "fr-FR-HenriNeural"


# ─────────────────────────────────────────────
#  ÉTATS (utilisés par main() et voice_node)
# ─────────────────────────────────────────────
STATE_IDLE   = "idle"
STATE_ACTIVE = "active"


# ═════════════════════════════════════════════
#  TTS EN FLUX — procédural, mpg123 persistant
#  Un seul process mpg123 dont on garde stdin ouvert : tous les chunks
#  MP3 successifs s'enchaînent → pas de coupure entre les phrases.
# ═════════════════════════════════════════════

# File des textes à prononcer (FIFO)
_tts_queue: "queue.Queue[str]" = queue.Queue()

# Process mpg123 unique
_mpg123_proc: subprocess.Popen | None = None
_mpg123_lock = threading.Lock()

# Markdown courant à virer avant TTS (sinon edge-tts lit les astérisques)
_MD_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"),               r"\1"),  # **gras**
    (re.compile(r"__(.+?)__"),                   r"\1"),  # __gras__
    (re.compile(r"\*(.+?)\*"),                   r"\1"),  # *italique*
    (re.compile(r"(?<!\w)_(.+?)_(?!\w)"),        r"\1"),  # _italique_
    (re.compile(r"`(.+?)`"),                     r"\1"),  # `code`
    (re.compile(r"~~(.+?)~~"),                   r"\1"),  # ~~barré~~
    (re.compile(r"^#+\s*", re.M),                ""),     # # titres
]


def _strip_markdown(text: str) -> str:
    if not text:
        return ""
    for pat, repl in _MD_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _get_mpg123() -> subprocess.Popen:
    """Renvoie le mpg123 unique. Le (re)lance s'il est mort."""
    global _mpg123_proc
    with _mpg123_lock:
        if _mpg123_proc is None or _mpg123_proc.poll() is not None:
            _mpg123_proc = subprocess.Popen(
                ["mpg123", "-q", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return _mpg123_proc


async def _synthesize_to_mpg123(text: str) -> None:
    """edge-tts en flux → écrit les chunks MP3 dans le stdin du mpg123 unique."""
    proc = _get_mpg123()
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    async for chunk in communicate.stream():
        if chunk.get("type") != "audio":
            continue
        data = chunk.get("data")
        if not data:
            continue
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            # mpg123 est mort → on le relance et on retente une fois
            with _mpg123_lock:
                global _mpg123_proc
                _mpg123_proc = None
            proc = _get_mpg123()
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except Exception:
                return


def _tts_worker() -> None:
    """Consomme la file en série, une phrase à la fois."""
    while True:
        text = _tts_queue.get()
        try:
            asyncio.run(_synthesize_to_mpg123(text))
        except Exception as e:
            print(f"⚠️  TTS erreur : {e}")
        finally:
            _tts_queue.task_done()


# Démarre le worker une fois pour toutes, à l'import du module
threading.Thread(target=_tts_worker, daemon=True).start()


def speak(text: str, wait: bool = False) -> None:
    """
    Enfile un texte à prononcer. Le markdown est nettoyé automatiquement.
    Si wait=True, bloque jusqu'à la fin de la prononciation (utile avant
    de réécouter au micro pour éviter d'entendre la voix de MARC).
    """
    text = _strip_markdown((text or "").strip())
    if text:
        _tts_queue.put(text)
    if wait:
        _tts_queue.join()


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
#  Historique de conversation partagé
# ─────────────────────────────────────────────
conversation_history: list[dict] = []


# ═════════════════════════════════════════════
#  LLM — Ollama (JSON strict, version bloquante)
# ═════════════════════════════════════════════
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
                json={"model": OLLAMA_MODEL, "messages": messages,
                      "keep_alive": -1, "stream": True},
                stream=True,
                timeout=60,
            ) as resp:
                # 5xx transitoire → retry avec backoff
                if 500 <= resp.status_code < 600:
                    if attempt < MAX_RETRIES:
                        wait = 1.5 ** attempt
                        print(f"⚠️  Ollama {resp.status_code} (tentative {attempt}/{MAX_RETRIES}) → retry dans {wait:.1f}s")
                        time.sleep(wait)
                        continue
                    print(f"❌  Ollama {resp.status_code} après {MAX_RETRIES} tentatives")
                    return {"type": "chat",
                            "response": "Désolé, mon cerveau est temporairement indisponible. Réessayez dans un instant."}

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

            # Nettoyage des balises markdown si le modèle ajoute des fences
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

    return {"type": "chat",
            "response": "Désolé, mon cerveau est temporairement indisponible."}


# ═════════════════════════════════════════════
#  LLM — Ollama EN FLUX (parle pendant la génération)
#  Procédural : l'état du parseur est local à la fonction.
# ═════════════════════════════════════════════

# Repère le début de la valeur de la clé "response" dans le JSON streamé
_RESPONSE_OPEN = re.compile(r'"response"\s*:\s*"')

# Décode les séquences d'échappement JSON les plus courantes
_ESCAPE_MAP = {"n": "\n", "t": "\t", "r": "", '"': '"', "\\": "\\", "/": "/"}


def ask_ollama_stream(user_text: str, on_sentence, extra_context: str = "") -> dict:
    """
    Streaming Ollama : on extrait au fil de l'eau le contenu de la clé
    "response" et on appelle on_sentence(phrase) pour chaque phrase complète.
    Le markdown est nettoyé avant l'appel.

    Retourne le dict JSON parsé en fin de flux (type / response / action /
    paramètres) avec une clé "_spoke" indiquant si au moins une phrase a
    déjà été émise (pour éviter de la répéter chez l'appelant).
    """
    system = SYSTEM_PROMPT
    if extra_context:
        system = SYSTEM_PROMPT + "\n\n---\n\n" + extra_context

    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system}] + conversation_history

    MAX_RETRIES = 3

    for attempt in range(1, MAX_RETRIES + 1):
        # ── État local du parseur (ex-ResponseStreamer, mis à plat) ──
        buf = ""           # JSON brut accumulé (pour le parse final)
        start = None       # index du début de la valeur "response"
        pos = None         # curseur dans la valeur
        pending = ""       # phrase en cours, pas encore émise
        decoded = ""       # response complète décodée (filet de sécurité)
        escape = False
        closed = False
        spoke = False

        def flush() -> None:
            """Émet la phrase courante via on_sentence après nettoyage markdown."""
            nonlocal pending, spoke
            s = _strip_markdown(pending.strip())
            pending = ""
            if s:
                spoke = True
                on_sentence(s)

        def consume(token: str) -> None:
            """Accumule un token et fait avancer le parseur sur la valeur "response"."""
            nonlocal buf, start, pos, pending, decoded, escape, closed
            buf += token
            if closed:
                return
            if start is None:
                m = _RESPONSE_OPEN.search(buf)
                if not m:
                    return
                start = m.end()
                pos = start
            n = len(buf)
            while pos < n:
                ch = buf[pos]
                pos += 1
                if escape:
                    u = _ESCAPE_MAP.get(ch, ch)
                    pending += u
                    decoded += u
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':                     # guillemet fermant → fin de "response"
                    flush()
                    closed = True
                    return
                pending += ch
                decoded += ch
                # Frontière de phrase (en évitant les décimales du genre 1.5)
                if ch in "!?…\n":
                    flush()
                elif ch == "." and not pending[-2:-1].isdigit():
                    flush()

        full_response = ""
        try:
            with requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "messages": messages,
                      "keep_alive": -1, "stream": True},
                stream=True,
                timeout=60,
            ) as resp:
                # 5xx transitoire → retry tant qu'aucun token n'a été lu
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
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    full_response += token
                    if token:
                        consume(token)
                    if chunk.get("done"):
                        break

            # Flux terminé sans guillemet fermant → on émet le reliquat
            if not closed:
                flush()

            # ── Parse final du JSON complet ──
            conversation_history.append({"role": "assistant", "content": full_response})

            clean = full_response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()

            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError:
                # Filet de sécurité : on garde au moins ce qu'on a décodé en "response"
                parsed = {"type": "chat",
                          "response": decoded.strip() or full_response.strip()}

            parsed["_spoke"] = spoke
            print(f"🤖  MARC JSON : {json.dumps(parsed, ensure_ascii=False)}")
            return parsed

        except requests.exceptions.ConnectionError:
            print("❌  Ollama inaccessible")
            return {"type": "chat",
                    "response": "Désolé, je n'ai pas pu traiter ça correctement.",
                    "_spoke": spoke}
        except Exception as e:
            print(f"❌  Erreur Ollama : {e}")
            return {"type": "chat",
                    "response": "Désolé, je n'ai pas pu traiter ça correctement.",
                    "_spoke": spoke}

    return {"type": "chat",
            "response": "Désolé, mon cerveau est temporairement indisponible.",
            "_spoke": False}


# ─────────────────────────────────────────────
#  ENVOI COMMANDE AU SERVEUR
# ─────────────────────────────────────────────
def send_command_to_server(payload: dict) -> bool:
    """Envoie le payload JSON au serveur Flask /vocal_command."""
    try:
        resp = requests.post(
            f"{SERVER_URL}/vocal_command",
            json=payload,
            timeout=10,
            verify=False,  # certificat auto-signé
        )
        resp.raise_for_status()
        print(f"✅  Serveur : {resp.json()}")
        return True
    except Exception as e:
        print(f"❌  Erreur envoi serveur : {e}")
        return False


# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE (exécution autonome du fichier)
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
                    speak("Oui, je vous écoute.", wait=True)
                    print("💬  Mode actif — dites 'Merci' pour terminer\n")
                else:
                    print(f"\r😴  (ignoré : '{text}')", end="", flush=True)

            # ── MODE ACTIF ──
            elif state == STATE_ACTIVE:
                print(f"\n👤  Vous : {text}")

                # Mot de stop → retour en veille
                if any(w in text for w in STOP_WORDS):
                    speak("De rien, à bientôt !", wait=True)
                    state = STATE_IDLE
                    conversation_history.clear()
                    send_command_to_server({"type": "commande", "action": "shutdown",
                                            "response": "Mise en veille."})
                    print("\n😴  En attente de 'Salut Marc'…\n")
                    continue

                # LLM EN FLUX : MARC parle déjà pendant la génération
                result = ask_ollama_stream(text, speak)
                spoke = result.pop("_spoke", False)
                response_text = result.get("response", "")

                if result.get("type") == "commande":
                    sent = send_command_to_server(result)
                    if not sent:
                        speak("Je n'ai pas pu exécuter la commande.")
                    elif not spoke and response_text:
                        speak(response_text)
                    if result.get("action") == "shutdown":
                        state = STATE_IDLE
                        conversation_history.clear()
                        print("\n😴  En attente de 'Salut Marc'…\n")

                elif result.get("type") == "chat":
                    if not spoke and response_text:
                        speak(response_text)

                else:
                    print(f"⚠️  Type inconnu : {result.get('type')}")
                    if not spoke and response_text:
                        speak(response_text)

                # Attendre la fin de la parole avant de réécouter
                # (évite que le micro capte la voix de MARC)
                speak("", wait=True)  # ne dit rien mais attend la file

        except KeyboardInterrupt:
            print("\n\n👋  Arrêt.")
            break
        except Exception as e:
            print(f"\n⚠️  Erreur inattendue : {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
