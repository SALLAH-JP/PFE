# MARC — Mascareignes Assistant and Robot Compagnon

> Robot mobile autonome guide pour le laboratoire de robotique de l'Université des Mascareignes.

MARC est un projet de fin d'études développé en troisième année d'Informatique Appliquée à l'Université des Mascareignes (Maurice), en partenariat avec l'Université de Limoges. Le robot navigue de façon autonome entre sept stations dédiées aux équipements robotiques du laboratoire (NAO, Pepper, Vector, Baxter, Franka Panda, imprimante 3D Zortrax M300 Plus), interagit en langage naturel grâce à un Grand Modèle de Langage, et expose une interface web temps réel pour la supervision et le contrôle manuel.

---

## Aperçu

- **Navigation autonome** par suivi de ligne (3 capteurs IR) entre 7 stations
- **Pipeline vocal** Speech-to-Text → LLM cloud (Mistral via Ollama) → Text-to-Speech
- **Sortie JSON structurée** : le LLM génère directement des commandes exécutables
- **Interface web HTTPS** : parcours SVG temps réel, journal d'événements, Push-To-Talk
- **Matrice LED RGB 64×32** pour l'expression visuelle (6 GIF animés)
- **Tentative auto-équilibrante PID** documentée et analysée (mode finalement abandonné pour la navigation, voir le rapport)

---

## Architecture

Le système est distribué en trois couches :

```
┌─────────────────────────────────────────────┐
│  Couche matérielle                          │
│  Arduino Mega 2560 — 16 MHz, PID 100 Hz     │
│  Moteurs NEMA 23 · TB6600 · IR · BNO085     │
└──────────────────┬──────────────────────────┘
                   │ UART 115 200 bauds
┌──────────────────┴──────────────────────────┐
│  Couche applicative                         │
│  Raspberry Pi 4 Model B — 1 Go RAM          │
│  Flask HTTPS · pipeline vocal · LED matrix  │
└──────────────────┬──────────────────────────┘
                   │ HTTPS — API Ollama
┌──────────────────┴──────────────────────────┐
│  Intelligence distante                      │
│  Mistral Large via Ollama Cloud             │
│  Sortie JSON structurée                     │
└─────────────────────────────────────────────┘
```

---

## Matériel

| Composant            | Référence                                    |
| -------------------- | -------------------------------------------- |
| Microcontrôleur      | Arduino Mega 2560                            |
| Ordinateur de bord   | Raspberry Pi 4 Model B (1 Go RAM)            |
| Moteurs              | Pas-à-pas NEMA 23 ×2                         |
| Drivers moteurs      | TB6600 ×2                                    |
| Centrale inertielle  | BNO085 (SPI)                                 |
| Capteurs ligne       | 3 capteurs IR numériques                     |
| Affichage            | Matrice LED RGB 64×32                        |
| Télécommande         | Récepteur IR (réglage PID, contrôle manuel)  |

La modélisation 3D complète a été réalisée sur **Onshape** et le robot a été imprimé en 3D au laboratoire de l'UDM.

---

## Structure du dépôt

```
.
├── arduino/              # Code embarqué Arduino Mega
│   ├── marc.ino          # Boucle principale, PID, suivi de ligne
│   └── lib/              # Bibliothèques tierces
├── raspberry/            # Couche applicative Python
│   ├── server.py         # Serveur Flask HTTPS
│   ├── voiceAssistant.py # Pipeline vocal (STT → LLM → TTS)
│   ├── gif_viewer.py     # Pilotage matrice LED
│   ├── SplitEyes.py      # Préparation des GIF d'expression
│   └── modelfile.txt     # Prompt système LLM + base de connaissances
├── web/                  # Interface web
│   ├── index.html        # Page unique HTML/CSS/JS
│   ├── app.js            # Parcours SVG, Push-To-Talk, journal
│   └── style.css
├── cad/                  # Liens vers les modèles Onshape
└── docs/
    └── Rapport_PFE_MARC.pdf
```

---

## Installation

### Prérequis

- Raspberry Pi 4 (Raspberry Pi OS 64 bits)
- Arduino Mega 2560 + IDE Arduino
- Python 3.9 ou supérieur
- Compte Ollama avec accès à l'API cloud
- Réseau Wi-Fi local

### Côté Arduino

1. Ouvrir `arduino/marc.ino` dans l'IDE Arduino.
2. Installer les bibliothèques : `FastAccelStepper`, `IRremote`, `SparkFun BNO08x`.
3. Vérifier les broches (STEP, DIR, ENABLE, capteurs IR) dans l'en-tête du fichier.
4. Compiler et téléverser sur l'Arduino Mega.

### Côté Raspberry Pi

```bash
# Cloner le dépôt
git clone https://github.com/<utilisateur>/MARC.git
cd MARC/raspberry

# Environnement Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Bibliothèque matrice LED (compilation native)
git clone https://github.com/hzeller/rpi-rgb-led-matrix
cd rpi-rgb-led-matrix && make build-python PYTHON=$(which python3) && cd ..

# Certificat SSL auto-signé pour HTTPS
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Variable d'environnement Ollama
export OLLAMA_API_KEY="<votre_clé>"
export OLLAMA_HOST="https://ollama.com"
```

### Lancement

```bash
# Terminal 1 — serveur web
sudo python3 server.py

# Terminal 2 — assistant vocal
python3 voiceAssistant.py

# Terminal 3 — matrice LED
sudo python3 gif_viewer.py
```

L'interface web est ensuite accessible sur `https://<ip-du-pi>:5000` depuis n'importe quel appareil du réseau local.

---

## Utilisation

### Commande vocale

1. Dire le mot de réveil : *« Salut Marc »*.
2. Énoncer la commande, par exemple : *« Va voir Pepper »*, *« Présente-moi NAO »*, *« Reviens à la base »*.
3. MARC interprète la requête, confirme oralement et exécute.

### Interface web

- Cliquer sur une station du parcours SVG pour y envoyer le robot
- Activer ou désactiver le mode guide
- Push-To-Talk pour enregistrer une commande vocale depuis le navigateur
- Consulter le journal des événements en temps réel

---

## Documentation

Le rapport complet de Projet de Fin d'Études détaille l'analyse des besoins, les choix d'architecture, l'analyse du conflit PID-équilibre vs suivi de ligne, les tests LLM réalisés sur Raspberry Pi 4, ainsi que les résultats expérimentaux. Il est disponible dans `docs/Rapport_PFE_MARC.pdf`.

---

## Auteur

**SALLAH Assiongbon Théodore Jean-Paul**
Étudiant en 3ème année — Informatique Appliquée
Université des Mascareignes · Université de Limoges
Année universitaire 2024 / 2025

Encadrant pédagogique : M. Khadimoullah Ramoth

---

## Remerciements

Ce projet doit beaucoup à plusieurs contributions extérieures :

- Le projet [Local-Voice](https://github.com/m15-ai/Local-Voice) de M15.ai, dont l'architecture a inspiré le pipeline vocal
- La plateforme [Ollama](https://ollama.com) et le modèle Mistral Large
- La bibliothèque [FastAccelStepper](https://github.com/gin66/FastAccelStepper) de J. Kerschbaumer
- La bibliothèque [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) de H. Zeller
- Les fournisseurs locaux Advanced Electronique et Transcom (Maurice)

---

## Licence

Ce projet est distribué sous licence MIT — voir le fichier `LICENSE`.

Le code des bibliothèques tierces utilisées reste sous leurs licences respectives.
