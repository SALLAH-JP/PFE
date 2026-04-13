// ===================================================
//  MARC Robot — app.js
//  Parcours robotique avec sélection de station
//  Commande vocale via MediaRecorder → serveur Whisper
// ===================================================

const STATIONS = {
  nao:    { cx: 52,  cy: 190 },
  imp3d:  { cx: 183, cy: 46  },
  pepper: { cx: 397, cy: 46  },
  robot3: { cx: 528, cy: 190 },
  robot4: { cx: 397, cy: 334 },
  robot5: { cx: 290, cy: 338 },
  robot6: { cx: 183, cy: 334 },
};

let currentStation = 'nao';
let targetStation  = null;

// ── Déplacer Nash sur le SVG ──
function moveNash(stationId) {
  const s = STATIONS[stationId];
  if (!s) return;
  document.getElementById('nash-dot').setAttribute('cx', s.cx);
  document.getElementById('nash-dot').setAttribute('cy', s.cy);
  document.getElementById('nash-glow').setAttribute('cx', s.cx);
  document.getElementById('nash-glow').setAttribute('cy', s.cy);
  document.getElementById('nash-text').setAttribute('x', s.cx);
  document.getElementById('nash-text').setAttribute('y', s.cy + 4);
}

// ── Mettre à jour les styles des stations ──
function updateStationStyles() {
  Object.keys(STATIONS).forEach(id => {
    const svgGroup = document.getElementById('st-' + id);
    const cardEl   = document.getElementById('card-' + id);
    const badgeEl  = document.getElementById('badge-' + id);
    if (!svgGroup || !cardEl) return;
    svgGroup.classList.remove('is-current', 'is-target');
    cardEl.classList.remove('is-current', 'is-target');
    if (id === currentStation) {
      svgGroup.classList.add('is-current');
      cardEl.classList.add('is-current');
      if (badgeEl) badgeEl.textContent = 'ICI';
    } else if (id === targetStation) {
      svgGroup.classList.add('is-target');
      cardEl.classList.add('is-target');
      if (badgeEl) badgeEl.textContent = 'CIBLE';
    } else {
      if (badgeEl) badgeEl.textContent = '—';
    }
  });
}

// ── Sélectionner une destination (clic carte/SVG) ──
function selectStation(id) {
  if (id === currentStation) {
    addLog(`Nash est déjà à ${id}`, 'info');
    return;
  }
  targetStation = id;
  updateStationStyles();
  addLog(`Destination → ${id.toUpperCase()}`, 'cmd');
  sendDestination(id);
}

// ── Envoyer destination au serveur ──
async function sendDestination(destination) {
  try {
    const res  = await fetch('/command', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ destination }),
    });
    const data = await res.json();
    if (data.robot_state) applyRobotState(data.robot_state);
    if (data.ai_reply)    document.getElementById('aiReply').textContent = data.ai_reply;
    addLog('Serveur ✓', 'info');
  } catch {
    addLog('[démo] simulation locale', 'info');
    simulateMove(destination);
  }
}

// ── Simulation locale (sans serveur) ──
function simulateMove(dest) {
  addLog(`Nash se déplace vers ${dest.toUpperCase()}...`, 'info');
  setTimeout(() => {
    currentStation = dest;
    targetStation  = null;
    moveNash(dest);
    updateStationStyles();
    addLog(`Nash est arrivé à ${dest.toUpperCase()}`, 'info');
    document.getElementById('aiReply').textContent = `Je suis arrivé à ${dest}.`;
  }, 1500);
}

// ── Appliquer l'état reçu du serveur ──
function applyRobotState(state) {
  if (state.current) {
    currentStation = state.current;
    moveNash(state.current);
  }
  if (state.target !== undefined) targetStation = state.target;
  updateStationStyles();
}

// ═══════════════════════════════════════════
//  PUSH-TO-TALK — MediaRecorder → /transcribe
// ═══════════════════════════════════════════
let mediaRecorder  = null;
let audioChunks    = [];
let mediaStream    = null;

const micBtn    = document.getElementById('micBtn');
const micStatus = document.getElementById('micStatus');

// ── Démarrer l'enregistrement ──
async function startRecording() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];

    // Choisir le meilleur format supporté
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : 'audio/ogg';

    mediaRecorder = new MediaRecorder(mediaStream, { mimeType });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = () => sendAudioToServer(mimeType);

    mediaRecorder.start(100); // chunk toutes les 100ms
    micBtn.classList.add('recording');
    setMicStatus('🔴 En écoute…', 'listening');
    addLog('Enregistrement démarré', 'info');

  } catch (err) {
    addLog('Microphone refusé : ' + err.message, 'err');
    setMicStatus('Maintenir pour parler', '');
  }
}

// ── Arrêter l'enregistrement ──
function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  micBtn.classList.remove('recording');
  setMicStatus('Traitement…', 'processing');
}

// ── Envoyer l'audio au serveur ──
async function sendAudioToServer(mimeType) {
  if (audioChunks.length === 0) {
    setMicStatus('Maintenir pour parler', '');
    return;
  }

  const blob     = new Blob(audioChunks, { type: mimeType });
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');

  addLog('Envoi audio au serveur...', 'info');

  try {
    const res  = await fetch('/transcribe', {
      method: 'POST',
      body:   formData,
    });
    const data = await res.json();

    // Afficher la transcription
    if (data.transcript) {
      document.getElementById('userText').textContent = data.transcript;
      addLog(`Transcription : "${data.transcript}"`, 'info');
    }

    // Afficher la réponse Nash
    if (data.ai_reply) {
      document.getElementById('aiReply').textContent = data.ai_reply;
      addLog(`Nash : ${data.ai_reply.slice(0, 80)}`, 'info');
    }

    // Déplacer le robot si destination détectée
    if (data.destination) {
      addLog(`Destination détectée → ${data.destination.toUpperCase()}`, 'cmd');
    }
    if (data.robot_state) applyRobotState(data.robot_state);

  } catch (err) {
    addLog('Erreur serveur — fallback Web Speech', 'err');
    // Fallback : Web Speech API si serveur indisponible
    fallbackWebSpeech();
  }

  setMicStatus('Maintenir pour parler', '');
}

// ── Fallback Web Speech API (si serveur indisponible) ──
function fallbackWebSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { addLog('Web Speech API non supportée', 'err'); return; }

  const rec = new SR();
  rec.continuous = false; rec.interimResults = false; rec.lang = 'fr-FR';

  rec.onresult = async (event) => {
    const transcript = event.results[0][0].transcript.trim();
    document.getElementById('userText').textContent = transcript;
    addLog(`[fallback] Voix : "${transcript}"`, 'info');

    try {
      const res  = await fetch('/send_text', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ user_text: transcript }),
      });
      const data = await res.json();
      if (data.ai_reply)    document.getElementById('aiReply').textContent = data.ai_reply;
      if (data.destination) addLog(`Destination → ${data.destination}`, 'cmd');
      if (data.robot_state) applyRobotState(data.robot_state);
    } catch {
      document.getElementById('aiReply').textContent = "Commande non reconnue.";
    }
  };

  rec.onerror = (e) => addLog('Fallback erreur : ' + e.error, 'err');
  rec.start();
}

// ── Événements bouton micro ──
micBtn.addEventListener('mousedown', (e) => {
  e.preventDefault();
  startRecording();
});

micBtn.addEventListener('mouseup', (e) => {
  e.preventDefault();
  stopRecording();
});

micBtn.addEventListener('mouseleave', () => {
  if (mediaRecorder && mediaRecorder.state === 'recording') stopRecording();
});

micBtn.addEventListener('touchstart', (e) => {
  e.preventDefault();
  startRecording();
}, { passive: false });

micBtn.addEventListener('touchend', (e) => {
  e.preventDefault();
  stopRecording();
}, { passive: false });

// ── UTILS ──
function setMicStatus(text, cls) {
  micStatus.textContent = text;
  micStatus.className   = 'mic-status' + (cls ? ' ' + cls : '');
}

function addLog(msg, type = '') {
  const container = document.getElementById('logEntries');
  const t = new Date().toTimeString().slice(0, 8);
  const d = document.createElement('div');
  d.className = 'log-entry';
  d.innerHTML = `<span class="log-time">${t}</span><span class="log-msg ${type}">${msg}</span>`;
  container.appendChild(d);
  container.scrollTop = container.scrollHeight;
}

document.getElementById('logClear').addEventListener('click', () => {
  document.getElementById('logEntries').innerHTML = '';
});

// ── SYNC ÉTAT INITIAL ──
fetch('/status')
  .then(r => r.json())
  .then(d => {
    if (d.robot_state) applyRobotState(d.robot_state);
    addLog('Connecté au serveur', 'info');
  })
  .catch(() => addLog('Mode démo — serveur non joignable', 'err'));

// ── INIT ──
moveNash(currentStation);
updateStationStyles();
