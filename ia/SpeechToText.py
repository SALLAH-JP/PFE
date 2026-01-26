import vosk
import sounddevice as sd
import json
import sys
import queue
import requests

# Configuration
MODEL_PATH = "vosk-model-small-fr-0.22"
SAMPLE_RATE = 16000

# File d'attente pour les données audio
q = queue.Queue()

def audio_callback(indata, frames, time, status):
    """Callback appelé pour chaque bloc audio"""
    if status:
        print(status, file=sys.stderr)
    q.put(bytes(indata))


def ask_ollama(prompt, model="llama3.2:1b"):
    url = "http://localhost:11434/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }

    r = requests.post(url, json=payload)
    r.raise_for_status()

    return r.json()["response"]


# Charger le modèle
model = vosk.Model(MODEL_PATH)
recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)
print("Modèle chargé !")

# Démarrer l'enregistrement
print("Parlez maintenant... (Ctrl+C pour arrêter)\n")

try:
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=8000,
        dtype='int16',
        channels=1,
        device=0,
        callback=audio_callback
    ):
        while True:
            data = q.get()
            
            if recognizer.AcceptWaveform(data):
                # Phrase complète
                result = json.loads(recognizer.Result())
                text = result.get('text', '')
                if text:
                    print(f"\n{text}")
                    response = ask_ollama(text)
                    print(f"Ollama: {response}\n")

                    
except KeyboardInterrupt:
    print("\n\nArrêt")
except Exception as e:
    print(f"Erreur: {e}")