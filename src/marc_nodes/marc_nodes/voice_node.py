#!/usr/bin/env python3
"""
voice_node.py — Assistant vocal de MARC en nœud ROS2.

Porté depuis assistantVocale/voiceAssistant.py. Le pipeline vocal est
INCHANGÉ :
    micro → Google STT → Ollama (JSON strict) → edge-tts (voix FR)

SEULE DIFFÉRENCE avec l'original :
  Avant : la commande finale était envoyée au serveur par HTTPS POST
          (requests.post(SERVER_URL + "/vocal_command", ...)).
  Maintenant : elle est PUBLIÉE sur le topic ROS2 /vocal_command
          (std_msgs/String contenant le JSON), que webbridge_node écoute.

Le reste (wake word, états idle/active, retries Ollama, TTS) est conservé.
"""

import os
import sys
import json
import time
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# ─────────────────────────────────────────────
#  On réutilise directement les fonctions de voiceAssistant.py
#  (STT, LLM, TTS) sans les réécrire : import depuis assistantVocale/.
#  Layout attendu :
#     MARC/
#       ├── src/marc_nodes/marc_nodes/voice_node.py   <- CE FICHIER
#       └── assistantVocale/voiceAssistant.py
# ─────────────────────────────────────────────
ROOT = "/home/pi/MARC"
sys.path.append(os.path.join(ROOT, "assistantVocale"))

from voiceAssistant import (   # noqa: E402
    listen_once,
    ask_ollama,
    speak,
    calibrate_mic,
    conversation_history,
    WAKE_WORDS,
    STOP_WORDS,
)

STATE_IDLE   = "idle"
STATE_ACTIVE = "active"


class VoiceNode(Node):
    def __init__(self):
        super().__init__("voice_node")

        # Publisher : le JSON de commande part sur /vocal_command
        self.cmd_pub = self.create_publisher(String, "/vocal_command", 10)

        self.get_logger().info("voice_node démarré — pipeline vocal en thread")

        # Le pipeline vocal est bloquant (écoute micro) : on le met dans
        # un thread pour ne pas figer le spin ROS2.
        self._running = True
        self._thread = threading.Thread(target=self._voice_loop, daemon=True)
        self._thread.start()

    # ─────────────────────────────────────────
    #  Remplace send_command_to_server() : publie au lieu de POST
    # ─────────────────────────────────────────
    def publish_command(self, payload: dict):
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self.cmd_pub.publish(msg)
        self.get_logger().info(f"Commande publiée : {msg.data}")

    # ─────────────────────────────────────────
    #  Boucle vocale (reprise de main() dans voiceAssistant.py)
    # ─────────────────────────────────────────
    def _voice_loop(self):
        calibrate_mic()
        state = STATE_IDLE
        self.get_logger().info("En attente de 'Salut Marc'…")

        while self._running and rclpy.ok():
            try:
                text = listen_once()
                if not text:
                    continue

                # ── IDLE : attente du wake word ──
                if state == STATE_IDLE:
                    if any(w in text for w in WAKE_WORDS):
                        self.get_logger().info(f"Wake word : '{text}'")
                        state = STATE_ACTIVE
                        conversation_history.clear()
                        speak("Oui, je vous écoute.")

                # ── ACTIVE : on traite la commande ──
                elif state == STATE_ACTIVE:
                    self.get_logger().info(f"Vous : {text}")

                    if any(w in text for w in STOP_WORDS):
                        speak("De rien, à bientôt !")
                        state = STATE_IDLE
                        conversation_history.clear()
                        self.publish_command({
                            "type": "commande", "action": "shutdown",
                            "response": "Mise en veille.",
                        })
                        continue

                    result = ask_ollama(text)
                    if result is None:
                        speak("Je n'arrive pas à me connecter.")
                        continue

                    response_text = result.get("response", "")

                    if result.get("type") == "commande":
                        # Publie la commande sur le topic (au lieu du POST HTTPS)
                        self.publish_command(result)
                        if response_text:
                            speak(response_text)
                        if result.get("action") == "shutdown":
                            state = STATE_IDLE
                            conversation_history.clear()

                    elif result.get("type") == "chat":
                        if response_text:
                            speak(response_text)
                    else:
                        if response_text:
                            speak(response_text)

            except Exception as e:
                self.get_logger().error(f"Erreur boucle vocale : {e}")
                time.sleep(1)

    def destroy_node(self):
        self._running = False
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = VoiceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
