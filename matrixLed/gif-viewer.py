#!/usr/bin/env python

import threading
from rgbmatrix import RGBMatrix, RGBMatrixOptions
from PIL import Image

def gifViewer(gif_path, matrix=None):
    """
    Affiche un GIF une seule fois (non-bloquant)
    
    Args:
        gif_path: Chemin vers le fichier GIF
        matrix: Instance RGBMatrix (si None, en crée une nouvelle)
    """
    
    # Créer la matrice si nécessaire
    if matrix is None:
        # Configuration for the matrix
        options = RGBMatrixOptions()
        options.rows = 32
        options.cols = 64
        #options.led_slowdown_gpio = 3
        options.led_rgb_sequence = 'RBG'
        options.brightness = 75
        options.disable_hardware_pulsing = True
        options.hardware_mapping = 'regular'  # If you have an Adafruit HAT: 'adafruit-hat'

        matrix = RGBMatrix(options = options)
    
    # Effacer l'affichage
    matrix.Clear()
    
    # Charger et prétraiter le GIF
    gif = Image.open(gif_path)
    num_frames = gif.n_frames
    
    frames = []
    for frame_index in range(num_frames):
        gif.seek(frame_index)
        frame = gif.copy()
        frame.thumbnail((matrix.width, matrix.height), Image.LANCZOS)
        frames.append(frame.convert("RGB"))
    
    gif.close()
    
    # Fonction pour afficher une seule fois
    def afficher():
        canvas = matrix.CreateFrameCanvas()
        
        for frame in frames:
            canvas.SetImage(frame)
            matrix.SwapOnVSync(canvas, framerate_fraction=10)
        
        # Garder la dernière frame affichée
        print(f"✅ {gif_path} affiché")
    
    # Lancer dans un thread
    thread = threading.Thread(target=afficher, daemon=True)
    thread.start()
