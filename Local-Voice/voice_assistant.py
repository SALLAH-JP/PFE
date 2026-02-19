#!/usr/bin/env python3
"""
Voice Assistant: Real-Time Voice Chat

This app runs on a Raspberry Pi (or Linux desktop) and creates a low-latency, full-duplex voice interaction
with an AI character. It uses local speech recognition
(Vosk), local text-to-speech synthesis (Piper), and a locally hosted large language model via Ollama.

Key Features:
- Wake-free, continuous voice recognition with real-time transcription
- LLM-driven responses streamed from a selected local model (e.g., LLaMA, Qwen, Gemma)
- Audio response synthesis with a gruff custom voice using ONNX-based Piper models
- Optional noise mixing and filtering via SoX
- System volume control via ALSA
- Modular and responsive design suitable for low-latency, character-driven agents

Ideal for embedded voice AI demos, cosplay companions, or standalone AI characters.

Copyright: M15.ai
License: MIT
"""

import os
import json
import queue
import threading
import time
import wave
import io
import re
import subprocess
from vosk import Model, KaldiRecognizer
import ollama
import pyaudio
import requests
from pydub import AudioSegment
import soxr
import numpy as np

# ------------------- TIMING UTILITY -------------------
class Timer:
    def __init__(self, label):
        self.label = label
        self.enabled = True
    def __enter__(self):
        self.start = time.time()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.enabled:
            elapsed_ms = (time.time() - self.start) * 1000
            print(f"[Timing] {self.label}: {elapsed_ms:.0f} ms")
    def disable(self):
        self.enabled = False

# ------------------- FUNCTIONS -------------------

def resample_audio(data, orig_rate=48000, target_rate=16000):
    # Convert byte string to numpy array
    audio_np = np.frombuffer(data, dtype=np.int16)
    # Resample using soxr
    resampled_np = soxr.resample(audio_np, orig_rate, target_rate)
    # Convert back to bytes
    return resampled_np.astype(np.int16).tobytes()

# ------------------- PATHS -------------------

CONFIG_PATH = os.path.expanduser("va_config.json")
BASE_DIR = os.path.dirname(__file__)
MODEL_PATH = os.path.join(BASE_DIR, 'vosk-model')
CHAT_URL = 'http://localhost:11434/api/chat'

# ------------------- CONFIG FILE LOADING -------------------

DEFAULT_CONFIG = {
    "volume": 9,
    "mic_name": "Plantronics",
    "audio_output_device": "Plantronics",
    "model_name": "robot-assistant",
    "voice": "en_US-kathleen-low.onnx",
    "enable_audio_processing": False,
    "history_length": 4,
    "system_prompt": "You are a helpful assistant."
}

def load_config():
    # Load config from system file or fall back to defaults
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                user_config = json.load(f)
            return {**DEFAULT_CONFIG, **user_config}  # merge with defaults
        except Exception as e:
            print(f"[Warning] Failed to load system config: {e}")

    print("[Debug] Using default config.")

    return DEFAULT_CONFIG

config = load_config()

# Apply loaded config values
VOLUME = config["volume"]
MIC_NAME = config["mic_name"]
AUDIO_OUTPUT_DEVICE = config["audio_output_device"] 
MODEL_NAME = config["model_name"]
VOICE_MODEL = os.path.join("voices", config["voice"])
ENABLE_AUDIO_PROCESSING = config["enable_audio_processing"]
HISTORY_LENGTH = config["history_length"]


# Setup messages with system prompt
messages = [{"role": "system", "content": config["system_prompt"]}]

RATE = 48000
CHUNK = 1024
CHANNELS = 1
mic_enabled = True

# SOUND EFFECTS
NOISE_LEVEL = '0.04'
BANDPASS_HIGHPASS = '300'
BANDPASS_LOWPASS = '800'

# ------------------- VOICE MODEL -------------------

VOICE_MODELS_DIR = os.path.join(BASE_DIR, 'voices')
if not os.path.isdir(VOICE_MODELS_DIR):
    os.makedirs(VOICE_MODELS_DIR)

VOICE_MODEL = os.path.join(VOICE_MODELS_DIR, config["voice"])

print('[Debug] Available Piper voices:')
for f in os.listdir(VOICE_MODELS_DIR):
    if f.endswith('.onnx'):
        print('  ', f)
print(f'[Debug] Using VOICE_MODEL: {VOICE_MODEL}')
print(f"[Debug] Config loaded: model={MODEL_NAME}, voice={config['voice']}, vol={VOLUME}, mic={MIC_NAME}")

# ------------------- CONVERSATION STATE -------------------

audio_queue = queue.Queue()

# Audio callback form Shure
def audio_callback(in_data, frame_count, time_info, status):
    global mic_enabled
    if not mic_enabled:
        return (None, pyaudio.paContinue)
    resampled_data = resample_audio(in_data, orig_rate=48000, target_rate=16000)
    audio_queue.put(resampled_data)
    return (None, pyaudio.paContinue)

# ------------------- STREAM SETUP -------------------

def start_stream():
    pa = pyaudio.PyAudio()

    stream = pa.open(
        rate=RATE,
        format=pyaudio.paInt16,
        channels=CHANNELS,
        input=True,
        frames_per_buffer=CHUNK,
        stream_callback=audio_callback
    )
    stream.start_stream()
    print(f'[Debug] Stream @ {RATE}Hz')
    return pa, stream

# ------------------- QUERY OLLAMA CHAT ENDPOINT -------------------

def query_ollama():


    with Timer("Inference"):  # measure inference latency
        resp = ollama.generate(
            model=MODEL_NAME,
            prompt=json.dumps(messages[-HISTORY_LENGTH:]),
            keep_alive=-1
        )

    response = resp['response']
    print(f'[Debug] Ollama status: {response}')

    response = json.loads(response[response.find("{"):response.rfind("}")+1])

    return response['reponse']

# ------------------- TTS & DEGRADATION -------------------

import tempfile

def play_response(text):
    import io
    import tempfile

    # Mute the mic during playback to avoid feedback loop
    global mic_enabled
    mic_enabled = False  # 🔇 mute mic

    # clean the response
    clean = re.sub(r"[\*]+", '', text)                # remove asterisks
    clean = re.sub(r"\(.*?\)", '', clean)             # remove (stage directions)
    clean = re.sub(r"<.*?>", '', clean)               # remove HTML-style tags
    clean = clean.replace('\n', ' ').strip()          # normalize newlines
    clean = re.sub(r'\s+', ' ', clean)                # collapse whitespace
    clean = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+', '', clean)  # remove emojis

    piper_path = os.path.join(BASE_DIR, 'bin', 'piper', 'piper')

    # 1. Generate Piper raw PCM
    with Timer("Piper inference"):
        piper_proc = subprocess.Popen(
            [piper_path, '--model', VOICE_MODEL, '--output_raw'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        tts_pcm, _ = piper_proc.communicate(input=clean.encode())

    # No FX: just convert raw PCM to WAV
    pcm_to_wav = subprocess.Popen(
        ['sox', '-t', 'raw', '-r', '16000', '-c', str(CHANNELS), '-b', '16',
         '-e', 'signed-integer', '-', '-t', 'wav', '-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )
    tts_wav_16k, _ = pcm_to_wav.communicate(input=tts_pcm)

    resample_proc = subprocess.Popen(
        ['sox', '-t', 'wav', '-', '-r', '48000', '-t', 'wav', '-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )
    final_bytes, _ = resample_proc.communicate(input=tts_wav_16k)

    # 7. Playback
    with Timer("Playback"):
        try:
            wf = wave.open(io.BytesIO(final_bytes), 'rb')


            pa = pyaudio.PyAudio()
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

        except wave.Error as e:
            print(f"[Error] Could not open final WAV: {e}")
        
        finally:
            mic_enabled = True      # 🔊 unmute mic
            time.sleep(0.3)         # optional: small cooldown


# ------------------- PROCESSING LOOP -------------------

def processing_loop():
    model = Model(MODEL_PATH)
    #rec = KaldiRecognizer(model, RATE)
    rec = KaldiRecognizer(model, 16000)
    MAX_DEBUG_LEN = 200  # optional: limit length of debug output
    LOW_EFFORT_UTTERANCES = {"huh", "uh", "um", "erm", "hmm", "he's", "but"}

    while True:
        data = audio_queue.get()

        if rec.AcceptWaveform(data):
            start = time.time()
            r = json.loads(rec.Result())
            elapsed_ms = int((time.time() - start) * 1000)

            user = r.get('text', '').strip()
            if user:
                print(f"[Timing] STT parse: {elapsed_ms} ms")
                print("User:", user)

                if user.lower().strip(".,!? ") in LOW_EFFORT_UTTERANCES:
                    print("[Debug] Ignored low-effort utterance.")
                    rec = KaldiRecognizer(model, 16000)
                    continue  # Skip LLM response + TTS for accidental noise

                messages.append({"role": "user", "content": user})
                # Generate assistant response
                resp_text = query_ollama()
                if resp_text:
                    # Clean debug print (remove newlines and carriage returns)
                    clean_debug_text = resp_text.replace('\n', ' ').replace('\r', ' ')
                    if len(clean_debug_text) > MAX_DEBUG_LEN:
                        clean_debug_text = clean_debug_text[:MAX_DEBUG_LEN] + '...'

                    print('Assistant:', clean_debug_text)
                    messages.append({"role": "assistant", "content": clean_debug_text})

                    # TTS generation + playback
                    #play_response(resp_text)
                else:
                    print('[Debug] Empty response, skipping TTS.')

                # Reset recognizer after each full interaction
                rec = KaldiRecognizer(model, 16000)

# ------------------- MAIN -------------------

if __name__ == '__main__':
    pa, stream = start_stream()
    t = threading.Thread(target=processing_loop, daemon=True)
    t.start()
    try:
        while stream.is_active():
            time.sleep(0.1)
    except KeyboardInterrupt:
        stream.stop_stream(); stream.close(); pa.terminate()
