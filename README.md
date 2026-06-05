<div align="center">

# 🤖 MARC

### *Mascareignes Assistant and Robot Compagnon*

**Robot mobile autonome, guide vocal du laboratoire de robotique de l'Université des Mascareignes.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![ROS 2](https://img.shields.io/badge/ROS%202-Jazzy-22314E?logo=ros&logoColor=white)](https://docs.ros.org/)
[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-4-C51A4A?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.org/)
[![Arduino](https://img.shields.io/badge/Arduino-Mega%202560-00979D?logo=arduino&logoColor=white)](https://www.arduino.cc/)
[![OpenCV](https://img.shields.io/badge/OpenCV-ArUco-5C3EE8?logo=opencv&logoColor=white)](https://opencv.org/)
[![Ollama](https://img.shields.io/badge/LLM-Ministral%20via%20Ollama-7B68EE)](https://ollama.com/)

[**🎥 Voir les vidéos**](#-vidéos) · [**🚀 Installation**](#-installation) · [**📖 Documentation**](#-documentation)

</div>

---

## 📺 Vidéos

### Partie 1 — Construction *(disponible)*

> 🎬 **[▶️ MARC — Construction d'un robot compagnon (Partie 1)](https://www.youtube.com/watch?v=X_8uBchsi5A)**

[![MARC — Construction](https://img.youtube.com/vi/X_8uBchsi5A/maxresdefault.jpg)](https://www.youtube.com/watch?v=X_8uBchsi5A)

*La construction du robot, de la mécanique à l'électronique.*

### Partie 2 — Démonstration *(à venir)*

> 🎬 Navigation autonome, interaction vocale et expressions de MARC en action dans le laboratoire. *(lien à ajouter)*

---

## 📋 Sommaire

- [À propos](#-à-propos)
- [Aperçu](#-aperçu)
- [Architecture matérielle](#%EF%B8%8F-architecture-mat%C3%A9rielle)
- [Matériel](#-matériel)
- [Conception 3D](#-conception-3d)
- [Architecture logicielle (ROS 2)](#-architecture-logicielle-ros-2)
- [Navigation et localisation](#-navigation-et-localisation)
- [Structure du dépôt](#-structure-du-dépôt)
- [Installation](#-installation)
- [Utilisation](#-utilisation)
- [Documentation](#-documentation)
- [Auteur](#-auteur)

---

## 🎯 À propos

MARC est un **projet de fin d'études** développé en 3ème année d'Informatique Appliquée à l'**Université des Mascareignes** (Maurice), en partenariat avec l'**Université de Limoges**.

L'objectif : créer un robot guide capable de **se déplacer de manière autonome** entre les stations du laboratoire de robotique, de **dialoguer en langage naturel** grâce à un Grand Modèle de Langage, et d'**exprimer des émotions** via une matrice LED. Le tout supervisé depuis une interface web temps réel.

Le robot est entièrement conçu à partir de zéro : **modélisation 3D sur Onshape**, **impression 3D**, **électronique custom**, **firmware embarqué** et **stack logiciel ROS 2 complet**.

---

## ✨ Aperçu

- 🧭 **Navigation autonome par vision** : localisation absolue par marqueurs **ArUco**, fusionnée avec l'**odométrie** (cap IMU + distance parcourue)
- 🗺️ **Planification de chemin** : trajets calculés par **Dijkstra** sur un graphe de points de passage sûrs, qui contourne les obstacles fixes connus
- 🎙️ **Pipeline vocal complet** : Speech-to-Text → LLM cloud (Ministral via Ollama) → Text-to-Speech français
- 🧠 **Sortie JSON structurée** : le LLM génère directement des commandes exécutables
- 🌐 **Interface web HTTPS temps réel** : envoi de destinations, journal d'événements, Push-To-Talk, mises à jour SSE
- 👀 **Matrice LED RGB 64×32** avec plusieurs expressions GIF (neutre, clignement, suspicieux, triste, amour, disparition)
- 🧩 **Architecture ROS 2 modulaire** : 8 nœuds indépendants, lancés en une commande
- 📡 **Perception de proximité** : 3 capteurs ultrasoniques HC-SR04 remontés en continu
- ⚖️ **Tentative auto-équilibrante PID** documentée et analysée *(mode finalement écarté pour la navigation au sol — voir le rapport)*

---

## 🏗️ Architecture matérielle

Le système est distribué en trois couches :

```
┌─────────────────────────────────────────────┐
│  Couche temps réel — Arduino Mega 2560      │
│  Moteurs NEMA 23 · TB6600 · BNO085 (SPI)    │
│  3× HC-SR04 · capteurs IR · télécommande IR │
│  Envoie : Y:yaw · D:distance · U:c:g:d      │
│  Reçoit : C:move:turn · M:mode              │
└──────────────────┬──────────────────────────┘
                   │ UART 115 200 bauds
                   │ (/dev/ttyACM0)
┌──────────────────┴──────────────────────────┐
│  Couche applicative — Raspberry Pi 4        │
│  ROS 2 : 8 nœuds (vision, localisation,     │
│  navigation, mission, voix, LED, web, série)│
│  Caméra IMX219 (ArUco) · Flask HTTPS + SSE  │
└──────────────────┬──────────────────────────┘
                   │ daemon Ollama local → cloud
┌──────────────────┴──────────────────────────┐
│  Intelligence distante                      │
│  ministral-3:14b-cloud via Ollama           │
│  Sortie JSON structurée                     │
└─────────────────────────────────────────────┘
```

---

## 🔧 Matériel

| Composant | Référence / détail |
| --- | --- |
| Microcontrôleur | Arduino Mega 2560 |
| Ordinateur de bord | Raspberry Pi 4 Model B (1 Go RAM) |
| Moteurs | Pas-à-pas NEMA 23 ×2 (Ø roue 20 cm, 200 pas/tr, 1/4 micro-pas) |
| Drivers moteurs | TB6600 ×2 — STEP/DIR/ENA : G (7/5/3), D (8/6/4) |
| Centrale inertielle | BNO085 — SPI, CS 47, INT A4, RST A5 |
| Caméra | IMX219 (Arducam) — détection ArUco |
| Capteurs proximité | 3× HC-SR04 — centre (22/23), gauche (24/25), droite (26/27), portée 80 cm |
| Capteurs ligne | 3 capteurs IR — pins 49 (G), 40 (C), 48 (D) *(mode hérité)* |
| Affichage | Matrice LED RGB 64×32 (HUB75) |
| Télécommande | Récepteur IR (pin 46) — réglage PID, pilotage manuel |
| Alimentation | Double batterie Li-ion 12 V 3S indépendantes (moteurs / Pi) |

> 💡 La modélisation 3D complète a été réalisée sur **Onshape** et le robot a été imprimé en 3D au laboratoire de l'UDM.

---

## 🧱 Conception 3D

L'intégralité de la mécanique de MARC a été **modélisée sur Onshape** puis **imprimée en 3D** au laboratoire de l'UDM. Le robot se décompose en **3 sous-ensembles** : la coque externe esthétique, la structure interne porteuse, et la tête (matrice LED).

### 🔗 Visualisation interactive

👉 **[Voir le modèle 3D complet sur Onshape](https://cad.onshape.com/documents/bc8d4e2fc1fa99c54b0ae445/w/f22cc56c4e97ab29b572a8dc/e/e2a230c46f8ff9770799be93?renderMode=0&uiState=6a234b71ce6fba4c867a8832)**

### 📦 Fichiers 3D disponibles

Tous les fichiers source sont dans [`assets/3d-models/`](assets/3d-models/), aux formats **STL** (impression directe) et **STEP** (édition CAO).

> 💡 Astuce : cliquez sur n'importe quel fichier `.stl` directement sur GitHub pour le **visualiser en 3D** dans votre navigateur.

#### 🏛️ Coque externe

Habillage esthétique du robot, imprimé en plusieurs morceaux pour respecter les contraintes du plateau d'impression.

| Pièce | STL | STEP |
|-------|-----|------|
| Colonne V3 | [STL](assets/3d-models/ColonneV3.stl) | [STEP](assets/3d-models/ColonneV3.step) |
| Corps Bas — Avant | [STL](assets/3d-models/CorpsBasAvant.stl) | [STEP](assets/3d-models/CorpsBasAvant.step) |
| Corps Bas — Arrière | [STL](assets/3d-models/CorpsBasArriere.stl) | [STEP](assets/3d-models/CorpsBasArriere.step) |
| Corps Haut — Avant | [STL](assets/3d-models/CorpsHautAvant.stl) | [STEP](assets/3d-models/CorpsHautAvant.step) |
| Corps Haut — Arrière | [STL](assets/3d-models/CorpsHautArriere.stl) | [STEP](assets/3d-models/CorpsHautArriere.step) |
| Étage 0 (base) | [STL](assets/3d-models/Etage0.stl) | [STEP](assets/3d-models/Etage0.step) |
| Porte d'accès | [STL](assets/3d-models/Porte.stl) | [STEP](assets/3d-models/Porte.step) |
| Support capteurs IR | [STL](assets/3d-models/SupportTrackingSensor.stl) | [STEP](assets/3d-models/SupportTrackingSensor.step) |

#### 🦴 Structure interne

Squelette porteur qui supporte l'électronique, les moteurs et les batteries.

| Pièce | STL | STEP |
|-------|-----|------|
| Colonne V1 | [STL](assets/3d-models/ColonneV1.stl) | [STEP](assets/3d-models/ColonneV1.step) |
| Coque interne | [STL](assets/3d-models/Coque.stl) | [STEP](assets/3d-models/Coque.step) |
| Étage 1 | [STL](assets/3d-models/Etage1.stl) | [STEP](assets/3d-models/Etage1.step) |
| Étage 2 | [STL](assets/3d-models/Etage2.stl) | [STEP](assets/3d-models/Etage2.step) |
| Étage 3 | [STL](assets/3d-models/Etage3.stl) | [STEP](assets/3d-models/Etage3.step) |
| Bague de réduction | [STL](assets/3d-models/BagueDeReduction.stl) | [STEP](assets/3d-models/BagueDeReduction.step) |
| Roue | [STL](assets/3d-models/Roue.stl) | [STEP](assets/3d-models/Roue.step) |
| Part 8 | [STL](assets/3d-models/Part8.stl) | [STEP](assets/3d-models/Part8.step) |

#### 🤖 Tête

Module supérieur portant la matrice LED 64×32 (le « visage » de MARC).

| Pièce | STL | STEP |
|-------|-----|------|
| Tête — partie basse | [STL](assets/3d-models/TeteBas.stl) | [STEP](assets/3d-models/TeteBas.step) |
| Tête — partie haute | [STL](assets/3d-models/Tete-Haut.stl) | [STEP](assets/3d-models/TeteHaut.step) |
| Cadre matrice LED | [STL](assets/3d-models/tete/Matrice-Led.stl) | [STEP](assets/3d-models/MatriceLed.step) |

---

## 🧩 Architecture logicielle (ROS 2)

Le logiciel embarqué sur le Raspberry Pi est un **package ROS 2** (`marc_nodes`) composé de **8 nœuds** qui communiquent par topics. Chaque responsabilité est isolée dans son propre nœud, ce qui permet de lancer, tester et déboguer chaque brique séparément.

```
                        ┌──────────────┐
     /aruco/detections  │ camera_node  │  caméra IMX219 + détection ArUco
            ┌───────────└──────────────┘
            ▼
   ┌──────────────────┐   /pose    ┌────────────────┐  /nav_goal   ┌────────────────┐
   │ localization_node│──────────▶ │  mission_node  │ ───────────▶ │ navigation_node│
   │ ArUco + odométrie│            │ Dijkstra/graphe│ ◀─────────── │  go-to-goal    │
   └──────────────────┘            └────────────────┘ /goal_status └───────┬────────┘
      ▲          ▲                        ▲                                 │ /cmd_motor
/imu_yaw│ /distance│             /mission_station                           ▼
      │          │                 /nav_status              ┌────────────────────────┐
   ┌──┴──────────┴───┐                 ▲   │                │     firmware_node      │
   │  firmware_node  │ ─────────────────┘  │                │ pont série /dev/ttyACM0│
   │  pont série     │ ◀───────────────────┼────────────────└────────────────────────┘
   └─────────────────┘                     │
            ▲                       ┌───────┴─────────┐  /vocal_command  ┌────────────┐
     /led_expression                │  webbridge_node │ ◀─────────────── │ voice_node │
   ┌─────────────────┐              │  Flask + web +  │                  │ STT→LLM→TTS│
   │     led_node    │ ◀─────────── │  LLM + SSE      │                  └────────────┘
   │ matrice RGB 64×32│             └─────────────────┘
   └─────────────────┘
```

**`firmware_node`** — pont entre ROS 2 et l'Arduino sur le port série. Souscrit à `/cmd_motor` (`Int32MultiArray [move, turn]`) qu'il traduit en trames `C:move:turn`, et publie le cap `/imu_yaw` et la distance `/distance` lus depuis l'Arduino.

**`camera_node`** — capture le flux de la caméra IMX219, détecte les marqueurs ArUco et publie les détections (`/aruco/detections`).

**`localization_node`** — calcule la pose (x, y, cap) du robot par **fusion** : position absolue par recalage ArUco quand un marqueur connu est visible, intégration **odométrique** (cap IMU + distance) sinon. Publie `/pose`.

**`navigation_node`** — contrôleur **go-to-goal pur** : amène MARC à un point (x, y) par correction de cap puis avance, avec orientation finale optionnelle. Gère aussi un **scan de localisation** par rotation au démarrage à froid, jusqu'à acquérir une première pose.

**`mission_node`** — le « cerveau de trajet » : à partir d'une station nommée ou d'un point libre, il planifie un **chemin par waypoints** (Dijkstra sur le graphe de points sûrs de `maps.py`) et enchaîne les waypoints un par un vers `navigation_node`.

**`voice_node`** — pipeline vocal : wake word (« salut marc ») → Google STT → `ministral-3:14b-cloud` (Ollama local, JSON strict) → synthèse vocale française (`edge-tts`). Publie la commande finale sur `/vocal_command`.

**`webbridge_node`** — serveur Flask HTTPS qui héberge l'interface web, interprète les commandes texte via le LLM, diffuse les événements en temps réel (SSE) et relaie les destinations vers `mission_node`.

**`led_node`** — unique propriétaire de la matrice LED. Tout nœud qui veut afficher une expression publie sur `/led_expression` (`love`, `neutral`, `suspicious`, `sad`, `blink`, `disappear`, `idle:<exp>`, `style:<n>`), ce qui évite les conflits d'accès GPIO.

---

## 🧭 Navigation et localisation

### Repère salle

L'origine est une intersection de carreaux choisie comme (0, 0). Convention : **0° = -Y**, **+90° = +X**, sens trigonométrique.

### Marqueurs ArUco de localisation

Les marqueurs fixes (mur, plafond) servent à recaler la pose absolue. Leurs positions réelles sont centralisées dans [`maps.py`](src/marc_nodes/marc_nodes/maps.py) :

| ID ArUco | Position (x, y) en m |
| --- | --- |
| 0 | (-2.07, 0.00) |
| 1 | (-2.08, 1.07) |
| 2 | (2.17, 1.05) |
| 3 | (-0.66, 2.45) |
| 4 | (2.17, -0.33) |
| 5 | (0.96, -3.39) |

### Stations

| Station | Équipement | Position (x, y) | Cap final |
| --- | --- | --- | --- |
| `nao` | Robot humanoïde NAO | (-1.6, 0) | 90° |
| `vector` | Robot mobile Vector | (-1.5, 4.9) | 30° |
| `pepper` | Robot humanoïde Pepper | *à mesurer* | — |
| `imp3d` | Imprimante 3D | *à mesurer* | — |
| `baxter` | Robot industriel Baxter | (1.8, 1.8) | 90° |
| `bras` | Bras Franka Panda | (1.8, -0.6) | -90° |

> ⚠️ Les positions marquées *à mesurer* valent `None` dans `maps.py`. Tant qu'une station n'a pas de coordonnées, `mission_node` refuse le trajet plutôt que d'inventer une trajectoire.

### Évitement des obstacles

L'évitement des **obstacles fixes** est assuré **en amont** par la planification : `mission_node` calcule un chemin qui ne passe que par des segments sûrs du graphe de waypoints. Les 3 capteurs **HC-SR04** fournissent en plus une perception de proximité (distances centre/gauche/droite remontées à 10 Hz).

---

## 📁 Structure du dépôt

```
MARC/
├── src/marc_nodes/                # Package ROS 2 (cœur du robot)
│   ├── launch/marc.launch.py      # Lance les 8 nœuds en une commande
│   ├── marc_nodes/
│   │   ├── firmware_node.py       # Pont série Arduino
│   │   ├── camera_node.py         # Caméra IMX219 + détection ArUco
│   │   ├── localization_node.py   # Fusion ArUco + odométrie → /pose
│   │   ├── navigation_node.py     # Contrôleur go-to-goal
│   │   ├── mission_node.py        # Planification de trajet (Dijkstra)
│   │   ├── voice_node.py          # Pipeline vocal STT→LLM→TTS
│   │   ├── webbridge_node.py      # Flask HTTPS + interface web + LLM + SSE
│   │   ├── led_node.py            # Matrice LED RGB
│   │   └── maps.py                # Carte : marqueurs, stations, graphe waypoints
│   ├── package.xml / setup.py     # Métadonnées et points d'entrée ROS 2
│
├── controlMoteur/                 # Firmware Arduino Mega
│   ├── controlMoteur.ino          # Boucle principale (série, PID, capteurs)
│   ├── fonctions.ino              # Moteurs, ultrasons, suivi de ligne, télécommande
│   └── header.h                   # Pins, constantes, objets globaux
│
├── vision/                        # Briques vision autonomes (hors ROS)
│   ├── calibrate_camera.py        # Calibration de la caméra (échiquier)
│   ├── camera_calibration.npz     # Matrice intrinsèque + distorsion
│   ├── camera_service.py          # Service caméra HTTPS
│   ├── localization.py            # Localisation ArUco + odométrie (prototype)
│   ├── navigation_goto.py         # Go-to-goal (prototype)
│   └── navigation_service.py      # Service navigation
│
├── assistantVocale/               # Pipeline vocal autonome (importé par voice_node)
│   ├── voiceAssistant.py          # STT (Google) → Ollama → edge-tts
│   ├── modelfile.txt              # Prompt système / contrainte JSON du LLM
│   └── requirements.txt
│
├── matrixLed/                     # Pilotage de la matrice LED RGB 64×32
│   ├── eye_manager.py             # Animations + clignement thread-safe
│   ├── gif_viewer.py              # Lecteur GIF (test standalone)
│   ├── SplitEyes.py               # Génération des GIFs depuis une vidéo
│   └── style1/ style2/ style3/    # Sets d'expressions (GIF)
│
├── web/                           # Interface web (servie par webbridge_node)
│   ├── server.py                  # Ancien serveur Flask monolithique (hérité)
│   ├── index.html / style.css / app.js
│   └── cert.pem / key.pem         # Certificat SSL auto-signé (à générer)
│
├── assets/3d-models/              # Fichiers STL + STEP (Onshape)
├── LICENSE
└── README.md
```

---

## 🚀 Installation

### Prérequis

- Raspberry Pi 4 (Raspberry Pi OS 64 bits recommandé)
- **ROS 2 Jazzy** installé
- Arduino Mega 2560 + IDE Arduino
- Python 3.9 ou supérieur
- Daemon [Ollama](https://ollama.com) installé et connecté (`ollama signin`) — le modèle cloud est relayé par le serveur local, aucune clé API à gérer
- `ffmpeg`, `mpg123`, `sox`, `alsa-utils`, `portaudio19-dev`

### Côté Arduino

1. Ouvrir `controlMoteur/controlMoteur.ino` dans l'IDE Arduino.
2. Installer via le gestionnaire de bibliothèques :
   - [`FastAccelStepper`](https://github.com/gin66/FastAccelStepper)
   - [`IRremote`](https://github.com/Arduino-IRremote/Arduino-IRremote)
   - [`NewPing`](https://bitbucket.org/teckel12/arduino-new-ping)
   - [`SparkFun BNO08x Arduino Library`](https://github.com/sparkfun/SparkFun_BNO08x_Arduino_Library)
   - `PID_v1` (Brett Beauregard)
3. Vérifier le brochage dans `header.h`.
4. Compiler et téléverser sur l'Arduino Mega.

### Côté Raspberry Pi

```bash
# Cloner le dépôt dans un workspace ROS 2
git clone https://github.com/SALLAH-JP/MARC.git
cd MARC

# Dépendances Python du pipeline vocal et de la vision
pip install -r assistantVocale/requirements.txt
pip install opencv-contrib-python pyserial speechrecognition edge-tts pydub pillow flask

# Dépendances système
sudo apt install ffmpeg mpg123 sox alsa-utils portaudio19-dev

# Bibliothèque matrice LED (compilation native)
git clone https://github.com/hzeller/rpi-rgb-led-matrix
cd rpi-rgb-led-matrix && make build-python PYTHON=$(which python3) && cd ..

# Accès GPIO sans sudo pour la matrice LED
sudo setcap 'cap_sys_rawio+ep' $(readlink -f $(which python3))

# Certificat SSL auto-signé (HTTPS requis pour le micro côté navigateur)
cd web
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
cd ..

# Authentification Ollama (le modèle cloud est relayé par le daemon local,
# aucune clé API à exporter)
ollama signin

# Compilation du package ROS 2
source /opt/ros/jazzy/setup.bash
colcon build --packages-select marc_nodes
source install/setup.bash
```

---

## 🎮 Utilisation

### Lancement complet

Tout le système démarre en une seule commande :

```bash
ros2 launch marc_nodes marc.launch.py
```

Arguments optionnels :

| Argument | Défaut | Effet |
| --- | --- | --- |
| `port:=/dev/ttyACM1` | `/dev/ttyACM0` | Port série de l'Arduino |
| `style:=1` | `2` | Style de départ des yeux LED |
| `use_voice:=false` | `true` | Désactive le pipeline vocal |
| `use_led:=false` | `true` | Désactive la matrice LED |
| `use_vision:=false` | `true` | Désactive la chaîne vision (caméra + localisation + navigation) |

Exemple — sans voix ni vision, pour tester les moteurs seuls :

```bash
ros2 launch marc_nodes marc.launch.py use_voice:=false use_vision:=false
```

### Commande vocale

1. Dire le mot de réveil.
2. Énoncer la commande, par exemple :
   - *« Va voir Pepper »* → navigation autonome jusqu'à la station
   - *« Présente-moi NAO »* → MARC s'y rend et donne une présentation
   - *« Reviens à la base »* → retour à l'origine
3. MARC interprète la requête, confirme oralement et exécute.

### Interface web

Accessible sur `https://<ip-du-pi>:5000` depuis n'importe quel appareil du réseau local (accepter l'avertissement de certificat auto-signé) :

- Envoyer le robot vers une station
- Push-To-Talk pour enregistrer une commande vocale depuis le navigateur
- Suivre le journal des événements en temps réel (SSE)

### Envoi manuel d'une destination (debug ROS 2)

```bash
# Envoyer MARC à une station
ros2 topic pub --once /mission_station std_msgs/String "{data: 'baxter'}"

# Envoyer vers un point brut (x, y)
ros2 topic pub --once /mission_point geometry_msgs/Point "{x: 1.0, y: 2.0, z: 0.0}"

# Suivre la pose estimée
ros2 topic echo /pose
```

---

## 📖 Documentation

Le rapport complet de Projet de Fin d'Études détaille l'analyse des besoins, les choix d'architecture, le passage du suivi de ligne à la navigation par vision, l'analyse du conflit PID-équilibre vs suivi de ligne, les tests de modèles LLM sur Raspberry Pi 4, la modélisation 3D sur Onshape et les résultats expérimentaux.

📄 Le rapport complet (PDF) est disponible sur demande.

---

## 👤 Auteur

**SALLAH Assiongbon Théodore Jean-Paul**
Étudiant en 3ème année — Informatique Appliquée
🎓 Université des Mascareignes · Université de Limoges

**Encadrant pédagogique :** M. Khadimoullah Ramoth

[![GitHub](https://img.shields.io/badge/GitHub-SALLAH--JP-181717?logo=github)](https://github.com/SALLAH-JP)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-jeanpaul--sallah-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/jeanpaul-sallah/)

---

## 🙏 Remerciements

Ce projet doit beaucoup à plusieurs contributions extérieures :

- Le projet [Local-Voice](https://github.com/m15-ai/Local-Voice) de M15.ai, dont l'architecture a inspiré le pipeline vocal
- La plateforme [Ollama](https://ollama.com) et le modèle Ministral
- La bibliothèque [FastAccelStepper](https://github.com/gin66/FastAccelStepper) de J. Kerschbaumer
- La bibliothèque [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) de H. Zeller
- [NewPing](https://bitbucket.org/teckel12/arduino-new-ping), [edge-tts](https://github.com/rany2/edge-tts), [Flask](https://flask.palletsprojects.com), [SpeechRecognition](https://github.com/Uberi/speech_recognition), [OpenCV](https://opencv.org)
- Les fournisseurs locaux Advanced Electronique et Transcom (Maurice)

---

## 📜 Licence

Ce projet est distribué sous licence **MIT** — voir le fichier [`LICENSE`](LICENSE).

Le code des bibliothèques tierces utilisées reste sous leurs licences respectives.

---

<div align="center">

*Si ce projet vous a plu, n'hésitez pas à laisser une ⭐ !*

</div>
