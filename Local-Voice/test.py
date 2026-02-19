import ollama
import json



def resample_audio(data, orig_rate=48000, target_rate=16000):
    # Convert byte string to numpy array
    audio_np = np.frombuffer(data, dtype=np.int16)
    # Resample using soxr
    resampled_np = soxr.resample(audio_np, orig_rate, target_rate)
    # Convert back to bytes
    return resampled_np.astype(np.int16).tobytes()



while True :
    response = ollama.chat(model='robot-assistant', messages=input("Prompt : "))
    response = response['response']

    print(response)

    response = json.loads(response[response.find("{"):response.rfind("}")+1])

    print(response)