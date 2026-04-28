#!/usr/bin/env python3
"""
eye_manager.py — Gestionnaire des yeux de MARC
Tourne en arrière-plan, cligne régulièrement, et peut jouer
des animations selon les événements.

Utilisation :
    from eye_manager import EyeManager
    eyes = EyeManager(matrix, gif_base_dir="/home/pi/PFE/matrixLed", style=2)
    eyes.start()

    eyes.play("love")           # joue une fois puis retourne à idle
    eyes.set_idle("neutral")    # changer l'animation idle
    eyes.set_style(1)           # changer le style complet (recharge les GIFs)
"""

import os
import time
import random
import threading
from PIL import Image


# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────

BLINK_INTERVAL_MIN = 3.0   # secondes entre deux clignements
BLINK_INTERVAL_MAX = 7.0

# Nom → fichier GIF (commun à tous les styles)
ANIMATIONS = {
    "neutral":    "neutral.gif",
    "blink":      "blink.gif",
    "suspicious": "suspicious.gif",
    "disappear":  "disappear.gif",
    "cry":        "cry.gif",
    "love":       "love.gif",
}

DEFAULT_IDLE = "neutral"


# ─────────────────────────────────────────────
#  CLASSE PRINCIPALE
# ─────────────────────────────────────────────

class EyeManager:
    def __init__(self, matrix, gif_base_dir: str, style: int = 2):
        """
        matrix       : instance RGBMatrix
        gif_base_dir : dossier parent contenant style1/, style2/, ...
        style        : numéro de style à charger au démarrage
        """
        self.matrix       = matrix
        self.gif_base_dir = gif_base_dir
        self._style       = style
        self._gif_dir     = os.path.join(gif_base_dir, f"style{style}")
        self._idle        = DEFAULT_IDLE
        self._event       = threading.Event()
        self._lock        = threading.Lock()
        self._pending     = None
        self._running     = False
        self._thread      = None
        self._cache: dict[str, list] = {}

        self._preload_all()

    # ─────────────────────────────────────────
    #  API PUBLIQUE
    # ─────────────────────────────────────────

    def start(self):
        """Démarre le thread de gestion des yeux."""
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("👀 EyeManager démarré")

    def stop(self):
        """Arrête proprement le thread."""
        self._running = False
        self._event.set()

    def play(self, animation: str):
        """
        Joue une animation une fois, puis retourne à l'idle.
        Thread-safe.
        """
        if animation not in self._cache:
            print(f"⚠️  Animation inconnue ou non chargée : {animation}")
            return
        with self._lock:
            self._pending = animation
        self._event.set()

    def set_idle(self, animation: str):
        """Change l'animation idle de base."""
        if animation not in self._cache:
            print(f"⚠️  Animation inconnue : {animation}")
            return
        self._idle = animation
        print(f"👀 Idle → {animation}")

    def set_style(self, style: int):
        """
        Change le style des yeux (recharge tous les GIFs du nouveau dossier).
        Appelé depuis execute_action('changeEyes').
        """
        new_dir = os.path.join(self.gif_base_dir, f"style{style}")
        if not os.path.exists(new_dir):
            print(f"⚠️  Style introuvable : {new_dir}")
            return

        print(f"👀 Changement style → style{style}")
        self._style   = style
        self._gif_dir = new_dir

        # Recharge les GIFs du nouveau style
        self._cache = {}
        self._preload_all()

        # Repart sur l'idle du nouveau style
        self._idle = DEFAULT_IDLE
        self.play(self._idle)
        print(f"👀 Style{style} chargé ✓")

    # ─────────────────────────────────────────
    #  BOUCLE PRINCIPALE
    # ─────────────────────────────────────────

    def _loop(self):
        while self._running:
            # Joue l'idle une fois
            self._play_gif(self._idle)

            # Attend le prochain événement ou le timer de clignement
            wait = random.uniform(BLINK_INTERVAL_MIN, BLINK_INTERVAL_MAX)
            triggered = self._event.wait(timeout=wait)
            self._event.clear()

            if not self._running:
                break

            with self._lock:
                pending = self._pending
                self._pending = None

            if pending:
                print(f"👀 Animation : {pending}")
                self._play_gif(pending)
            else:
                # Timer écoulé → clignement automatique
                self._play_gif("blink")

    # ─────────────────────────────────────────
    #  LECTURE D'UN GIF
    # ─────────────────────────────────────────

    def _play_gif(self, name: str):
        frames = self._cache.get(name)
        if not frames:
            return

        canvas = self.matrix.CreateFrameCanvas()

        for frame_img, duration_ms in frames:
            if not self._running:
                return
            if self._event.is_set():   # événement urgent → on coupe
                return
            canvas.SetImage(frame_img)
            self.matrix.SwapOnVSync(canvas, framerate_fraction=10)
            time.sleep(duration_ms / 1000.0)

    # ─────────────────────────────────────────
    #  PRÉCHARGEMENT DES GIFs
    # ─────────────────────────────────────────

    def _preload_all(self):
        for name, filename in ANIMATIONS.items():
            path = os.path.join(self._gif_dir, filename)
            if not os.path.exists(path):
                print(f"⚠️  GIF introuvable : {path}")
                continue
            try:
                gif    = Image.open(path)
                frames = []
                for i in range(gif.n_frames):
                    gif.seek(i)
                    frame = gif.copy()
                    frame.thumbnail(
                        (self.matrix.width, self.matrix.height),
                        Image.LANCZOS
                    )
                    duration = gif.info.get("duration", 100)
                    frames.append((frame.convert("RGB"), duration))
                gif.close()
                self._cache[name] = frames
                print(f"✅ GIF chargé : {name} ({len(frames)} frames)")
            except Exception as e:
                print(f"❌ Erreur chargement {name} : {e}")


# ─────────────────────────────────────────────
#  TEST STANDALONE
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        from rgbmatrix import RGBMatrix, RGBMatrixOptions
        options = RGBMatrixOptions()
        options.rows = 32
        options.cols = 64
        options.led_rgb_sequence = "RBG"
        options.brightness = 75
        options.disable_hardware_pulsing = True
        options.hardware_mapping = "regular"
        matrix = RGBMatrix(options=options)

        BASE = os.path.join(os.path.dirname(__file__), "..", "matrixLed")
        eyes = EyeManager(matrix, BASE, style=2)
        eyes.start()

        time.sleep(5)
        eyes.play("love")
        time.sleep(4)
        eyes.play("suspicious")
        time.sleep(4)
        print("Changement style 1...")
        eyes.set_style(1)
        time.sleep(5)

        eyes.stop()
    except ImportError:
        print("rgbmatrix non disponible — test impossible sur PC")
