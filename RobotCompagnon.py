#!/usr/bin/env python3
"""
RobotCompagnon.py
Orchestrateur principal du robot Nash
"""

import time
import sys
import os

# ── Imports modules du projet ──
sys.path.append(os.path.join(os.path.dirname(__file__), "Local-Voice"))
sys.path.append(os.path.join(os.path.dirname(__file__), "matrixLed"))

from voiceAssistant import listen_once, ask_ollama, speak, recognizer
import speech_recognition as sr

try:
    from gif_viewer import gifViewer
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    MATRIX_AVAILABLE = True
except ImportError:
    print("⚠️  rgbmatrix non disponible (mode PC)")
    MATRIX_AVAILABLE = False

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
WAKE_WORDS = ["salut nash", "salut nache", "salut nasch"]
STOP_WORDS = ["merci", "merci nash"]

# Chemins GIFs — adapte selon tes fichiers
GIF_DIR    = os.path.join(os.path.dirname(__file__), "matrixLed")
GIF_IDLE   = os.path.join(GIF_DIR, "style1", "blink.gif")
GIF_LISTEN = os.path.join(GIF_DIR, "style1", "neutral.gif")
GIF_THINK  = os.path.join(GIF_DIR, "style1", "blink.gif")
GIF_SPEAK  = os.path.join(GIF_DIR, "style1", "blink.gif")

# ─────────────────────────────────────────────
#  ÉTATS
# ─────────────────────────────────────────────
STATE_IDLE   = "idle"
STATE_ACTIVE = "active"

# ─────────────────────────────────────────────
#  INITIALISATION MATRIX
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
    if not MATRIX_AVAILABLE or matrix is None:
        return
    if not os.path.exists(gif_path):
        print(f"⚠️  GIF introuvable : {gif_path}")
        return
    gifViewer(gif_path, matrix)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Nash  —  Google STT → qwen2.5:1.5b → pyttsx3   ║")
    print("║  Wake word : 'Salut Nash'                         ║")
    print("║  Stop      : 'Merci'                              ║")
    print("║  Ctrl+C pour quitter                              ║")
    print("╚══════════════════════════════════════════════════╝\n")


    state = STATE_IDLE
    show_gif(GIF_IDLE)
    print("😴  En attente de 'Salut Nash'…")

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
                    show_gif(GIF_LISTEN)
                    speak("Oui, je vous écoute.")
                    print("💬  Mode discussion — dites 'Merci' pour terminer\n")
                else:
                    print(f"\r😴  (ignoré : '{text}')", end="", flush=True)

            # ── MODE ACTIF ──
            elif state == STATE_ACTIVE:
                print(f"\n👤  Vous : {text}")
                show_gif(GIF_LISTEN)

                if any(w in text for w in STOP_WORDS):
                    speak("De rien, à bientôt !")
                    state = STATE_IDLE
                    show_gif(GIF_IDLE)
                    print("\n😴  En attente de 'Salut Nash'…")
                    continue

                show_gif(GIF_THINK)
                response = ask_ollama(text)
                if response:
                    show_gif(GIF_SPEAK)
                    speak(response)
                    show_gif(GIF_LISTEN)

        except KeyboardInterrupt:
            print("\n\n👋  Arrêt du robot.")
            if matrix:
                matrix.Clear()
            break
        except Exception as e:
            print(f"\n⚠️  Erreur : {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
