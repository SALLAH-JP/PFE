import vosk
import sounddevice as sd
import json
import sys
import queue
import requests
import threading

llm_lock = threading.Lock()
listening_enabled = True


# Configuration
MODEL_PATH = "vosk-model-small-fr-0.22"
SAMPLE_RATE = 48000

# File d'attente pour les données audio
q = queue.Queue()

def audio_callback(indata, frames, time, status):
    """Callback appelé pour chaque bloc audio"""

    if not listening_enabled:
	return

    if status:
        print(status, file=sys.stderr)
    q.put(bytes(indata))


def ask_ollama(prompt, model="qwen2.5:0.5b"):
    url = "http://localhost:11434/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }

    with llm_lock:
    	r = requests.post(url, json=payload, timeout=120)
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

                    listening_enabled = False
                    response = ask_ollama(text)
                    listening_enabled = True

                    print(f"Ollama: {response}\n")



except KeyboardInterrupt:
    print("\n\nArrêt")
except Exception as e:
    print(f"Erreur: {e}")
