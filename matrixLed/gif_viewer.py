#!/usr/bin/env python3

import threading
from rgbmatrix import RGBMatrix, RGBMatrixOptions
from PIL import Image
import time
import sys

# Stop event global — arrête le thread en cours avant d'en lancer un nouveau
_current_stop_event = None


def gifViewer(gif_path, matrix=None):
    global _current_stop_event

    # Arrête le GIF précédent
    if _current_stop_event is not None:
        _current_stop_event.set()
        time.sleep(0.1)

    if matrix is None:
        options = RGBMatrixOptions()
        options.rows = 32
        options.cols = 64
        options.led_rgb_sequence = 'RBG'
        options.brightness = 75
        options.disable_hardware_pulsing = True
        options.hardware_mapping = 'regular'
        matrix = RGBMatrix(options=options)

    matrix.Clear()

    gif = Image.open(gif_path)
    frames = []
    for frame_index in range(gif.n_frames):
        gif.seek(frame_index)
        frame = gif.copy()
        frame.thumbnail((matrix.width, matrix.height), Image.LANCZOS)
        frames.append(frame.convert("RGB"))
    gif.close()

    stop_event = threading.Event()
    _current_stop_event = stop_event

    def afficher():
        canvas = matrix.CreateFrameCanvas()
        # Joue le GIF une seule fois
        for frame in frames:
            if stop_event.is_set():
                return
            canvas.SetImage(frame)
            matrix.SwapOnVSync(canvas, framerate_fraction=10)
        print(f"✅ {gif_path} affiché")
        # Garde la dernière frame — attend le stop
        while not stop_event.is_set():
            time.sleep(0.1)

    thread = threading.Thread(target=afficher, daemon=True)
    thread.start()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Require a gif argument")
    gifViewer(sys.argv[1])
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
