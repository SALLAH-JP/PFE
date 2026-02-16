import ollama
import subprocess
import tempfile
import os
import time
import signal
import json
import wave
import pyaudio
from vosk import Model, KaldiRecognizer
import sys
import numpy as np
from collections import deque

MODEL_PATH = "/home/sallah/PFE/Local-Voice/voices/gilles-low.onnx"
CONFIG_PATH = "/home/sallah/PFE/Local-Voice/voices/gilles-low.onnx.json"
OUTPUT_WAV = "/home/sallah/PFE/Local-Voice/test.wav"
THINKING_SOUND = "/home/sallah/PFE/Local-Voice/process.wav"
VOSK_MODEL_PATH = "/home/sallah/PFE/Local-Voice/vosk-model"  
WAKE_WORDS = ["hey assistant", "hello assistant", "okay assistant", "assistant"]
BEEP_SOUND = "/home/sallah/PFE/Local-Voice/beep.wav"  

vosk_model = None
recognizer = None
is_listening = False
wake_word_detected = False

BASE_DIR = "/"

def create_beep_sound():
    if os.path.exists(BEEP_SOUND):
        return
   
    print("Creating beep sound file...")
    try:
        import math
        import struct
       
        SAMPLE_RATE = 44100
        DURATION = 0.3  
        FREQUENCY = 880  

        with wave.open(BEEP_SOUND, 'w') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  
            wav_file.setframerate(SAMPLE_RATE)

            for i in range(int(SAMPLE_RATE * DURATION)):
                fade_factor = 1.0
                if i < SAMPLE_RATE * 0.1:  
                    fade_factor = i / (SAMPLE_RATE * 0.1)
                elif i > SAMPLE_RATE * (DURATION - 0.1):  
                    fade_factor = (SAMPLE_RATE * DURATION - i) / (SAMPLE_RATE * 0.1)
               
                sample = fade_factor * 0.5 * math.sin(2 * math.pi * FREQUENCY * i / SAMPLE_RATE)
                sample_int = int(sample * 32767)
                wav_file.writeframes(struct.pack('<h', sample_int))
       
        print(f"Beep sound created at {BEEP_SOUND}")
    except Exception as e:
        print(f"Could not create beep sound: {e}")

def initialize_vosk():
    global vosk_model, recognizer
   
    if not os.path.exists(VOSK_MODEL_PATH):
        print(f"Error: Vosk model not found at {VOSK_MODEL_PATH}")
        print("Please download a Vosk model from https://alphacephei.com/vosk/models")
        print("Example: vosk-model-small-en-us-0.15")
        sys.exit(1)
   
    vosk_model = Model(VOSK_MODEL_PATH)
    recognizer = KaldiRecognizer(vosk_model, 16000)
    print("Vosk model loaded successfully")
    print(f"Listening for wake words: {', '.join(WAKE_WORDS)}")

def start_thinking_sound():
    proc = subprocess.Popen(
        ["aplay", "-q", THINKING_SOUND],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return proc

def stop_thinking_sound(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=0.3)
        except:
            proc.kill()

def play_beep():
    try:
        if os.path.exists(BEEP_SOUND):
            subprocess.run(
                ["aplay", "-q", BEEP_SOUND],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        else:
            subprocess.run(
                ["play", "-q", "-n", "synth", "0.3", "sine", "880"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except FileNotFoundError:
        try:
            subprocess.run(
                ["speaker-test", "-t", "sine", "-f", "880", "-l", "1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.3
            )
        except:
            print("\a", end='', flush=True)
    except Exception as e:
        print(f"Could not play beep: {e}")
        print("\a", end='', flush=True)  

def speak_with_piper(text):
    thinking_proc = None
   
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = tmp.name

        thinking_proc = start_thinking_sound()

        piper_process = subprocess.Popen([
            os.path.join(BASE_DIR, 'bin', 'piper', 'piper'),
            "-m", MODEL_PATH,
            "-c", CONFIG_PATH,
            "-i", tmp_path,
            "-f", OUTPUT_WAV
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        while piper_process.poll() is None:
            if thinking_proc.poll() is not None:
                thinking_proc = start_thinking_sound()
            time.sleep(0.1)

        piper_process.wait()
       
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass

        if thinking_proc:
            stop_thinking_sound(thinking_proc)

    time.sleep(0.2)

    subprocess.run(["aplay", OUTPUT_WAV])

def check_wake_word(text):
    text_lower = text.lower().strip()
    for wake_word in WAKE_WORDS:
        if wake_word in text_lower:
            return True
    return False

def listen_for_command(timeout_seconds=10):
    global is_listening
   
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=8000)
    stream.start_stream()
   
    print("\nListening for command...")
   
    start_time = time.time()
    silence_start = None
    speech_detected = False
    final_text = ""

    recognizer.Reset()
   
    while is_listening:
        if time.time() - start_time > timeout_seconds:
            print("Timeout - no command detected")
            break
       
        data = stream.read(4000, exception_on_overflow=False)
       
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            if result.get("text", "").strip():
                final_text = result["text"]
                speech_detected = True
                print(f"Command: {final_text}")
                break
        else:
            partial_result = json.loads(recognizer.PartialResult())
            partial_text = partial_result.get("partial", "")
            if partial_text:
                silence_start = None
                if not speech_detected:
                    speech_detected = True
                    print(f"Command: {partial_text}", end='\r')
            elif speech_detected and silence_start is None:
                silence_start = time.time()
            elif silence_start and time.time() - silence_start > 1.5:
                result = json.loads(recognizer.FinalResult())
                final_text = result.get("text", "")
                if final_text:
                    print(f"Command: {final_text}")
                break
   
    stream.stop_stream()
    stream.close()
    p.terminate()
   
    return final_text.strip()

def continuous_listen_for_wake_word():
    global is_listening, wake_word_detected
   
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=8000)
    stream.start_stream()
   
    print("\nAlways listening for wake word...")
    print(f"Say one of: {', '.join(WAKE_WORDS)}")
   
    recognizer.Reset()
   
    while is_listening:
        try:
            data = stream.read(4000, exception_on_overflow=False)
           
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "").strip()
               
                if text and check_wake_word(text):
                    print(f"\nWake word detected: '{text}'")
                    play_beep()
                    wake_word_detected = True
                    recognizer.Reset()  
                    break

            partial_result = json.loads(recognizer.PartialResult())
            partial_text = partial_result.get("partial", "").lower()
           
            if partial_text and any(wake_word in partial_text for wake_word in WAKE_WORDS):
                time.sleep(0.1)
                result = json.loads(recognizer.FinalResult())
                final_text = result.get("text", "").strip()
               
                if final_text and check_wake_word(final_text):
                    print(f"\nWake word detected: '{final_text}'")
                    play_beep()
                    wake_word_detected = True
                    recognizer.Reset()  
                    break
           
        except Exception as e:
            print(f"Audio error: {e}")
            time.sleep(0.1)
   
    stream.stop_stream()
    stream.close()
    p.terminate()

def process_with_llm(user_input):
    thinking_proc = start_thinking_sound()
   
    response_text = ""
    try:
        stream = ollama.chat(
            model="qwen2.5:0.5b",
            messages=[{
                "role": "user",
                "content": user_input + ". Respond in under 50 words."
            }],
            stream=True
        )

        print("Processing... ", end="", flush=True)
        for chunk in stream:
            if "message" in chunk:
                content = chunk["message"]["content"]
                response_text += content
        print("Done!")
       
    except Exception as e:
        print(f"Error with LLM: {e}")
        response_text = "I encountered an error processing your request."
   
    finally:
        stop_thinking_sound(thinking_proc)
   
    return response_text.strip()

def main():
    global is_listening, wake_word_detected
   
    print("=" * 50)
    print("Voice Assistant with Wake Word")
    print("=" * 50)
    print(f"\nWake words: {', '.join(WAKE_WORDS)}")
    print("\nThe assistant is always listening...")
    print("Say a wake word followed by your command")
    print("Examples: 'hey assistant what time is it'")
    print("          'hello assistant tell me a joke'")
    print("\nPress Ctrl+C to exit")
    print("-" * 50)

    create_beep_sound()
    initialize_vosk()
   
    is_listening = True
   
    try:
        while is_listening:
            wake_word_detected = False
            continuous_listen_for_wake_word()
           
            if not is_listening:
                break
           
            if wake_word_detected:
                time.sleep(0.3)
               
                command = listen_for_command(timeout_seconds=10)
               
                if not command:
                    print("No command detected. Going back to sleep.\n")
                    continue

                if command.lower() in ["exit", "quit", "stop", "goodbye"]:
                    print("\nGoodbye!")
                    is_listening = False
                    break
               
                print(f"\nProcessing: {command}")

                response = process_with_llm(command)
               
                if response:
                    print(f"\nResponse: {response}")

                    time.sleep(0.1)
                    speak_with_piper(response)
               
                print("\n" + "-" * 50)
                print("Back to listening for wake word...\n")
               
            time.sleep(0.1)  
           
    except KeyboardInterrupt:
        print("\nInterrupted by user. Goodbye!")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        is_listening = False

if __name__ == "__main__":
    def signal_handler(sig, frame):
        print("\nShutting down...")
        sys.exit(0)
   
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
   
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye!")
    except Exception as e:
        print(f"\nFatal error: {e}")