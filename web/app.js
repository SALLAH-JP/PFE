// MARC — app.js
// Pilotage de l'interface web : envoie les ordres via /command (LLM)
// et /nav_stop ; affiche en temps réel la pose et le statut via SSE.

let currentStation = null;
let targetStation  = null;


// ══════════════════════════════════════════
//  SÉLECTION D'UNE STATION
// ══════════════════════════════════════════
async function selectStation(stationKey) {
  if (stationKey === currentStation) {
    addLog(`MARC est déjà à ${stationKey}`, 'info');
    return;
  }
  targetStation = stationKey;
  updateStationStyles();
  addLog(`Destination demandée → ${stationKey.toUpperCase()}`, 'cmd');

  // On envoie au serveur, qui passe par le LLM pour générer la commande
  try {
    const res  = await fetch('/command', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ destination: stationKey }),
    });
    const data = await res.json();
    if (data.ai_reply) document.getElementById('aiReply').textContent = data.ai_reply;
  } catch {
    addLog('Erreur envoi commande', 'err');
  }
}


// ══════════════════════════════════════════
//  STOP D'URGENCE
// ══════════════════════════════════════════
async function stopNavigation() {
  addLog('STOP demandé', 'err');
  targetStation = null;
  updateStationStyles();
  try {
    await fetch('/nav_stop', { method: 'POST' });
  } catch {
    addLog('Erreur envoi STOP', 'err');
  }
}


// ══════════════════════════════════════════
//  RÉINITIALISATION DU CAP (TARE YAW)
// ══════════════════════════════════════════
async function resetYaw() {
  addLog('Réinitialisation du cap demandée', 'cmd');
  try {
    await fetch('/imu_tare', { method: 'POST' });
  } catch {
    addLog('Erreur envoi tare cap', 'err');
  }
}


// ══════════════════════════════════════════
//  LOCALISATION (SCAN PAR ROTATION)
// ══════════════════════════════════════════
async function localizeRobot() {
  addLog('Localisation demandée', 'cmd');
  try {
    await fetch('/localize', { method: 'POST' });
  } catch {
    addLog('Erreur envoi localisation', 'err');
  }
}


// ══════════════════════════════════════════
//  STYLES STATIONS (highlight current / target)
// ══════════════════════════════════════════
const STATION_KEYS = ['nao', 'vector', 'pepper', 'imp3d', 'baxter', 'bras'];

function updateStationStyles() {
  STATION_KEYS.forEach(key => {
    const card  = document.getElementById('card-' + key);
    const badge = document.getElementById('badge-' + key);
    if (!card) return;
    card.classList.remove('is-current', 'is-target');
    if (key === currentStation) {
      card.classList.add('is-current');
      if (badge) badge.textContent = 'ICI';
    } else if (key === targetStation) {
      card.classList.add('is-target');
      if (badge) badge.textContent = 'CIBLE';
    } else {
      if (badge) badge.textContent = '—';
    }
  });
}


// ══════════════════════════════════════════
//  APPLIQUER L'ÉTAT REÇU DU SERVEUR
// ══════════════════════════════════════════
function applyRobotState(state) {
  if (state.current !== undefined) currentStation = state.current;
  if (state.target  !== undefined) targetStation  = state.target;
  if (state.pose)                  applyPose(state.pose);
  updateStationStyles();
}

function applyPose(pose) {
  const px = document.getElementById('poseX');
  const py = document.getElementById('poseY');
  const ph = document.getElementById('poseHeading');
  const ps = document.getElementById('poseSource');
  if (pose.x !== null && pose.x !== undefined) {
    px.textContent = Number(pose.x).toFixed(2);
    py.textContent = Number(pose.y).toFixed(2);
    ph.textContent = Number(pose.heading).toFixed(0);
    ps.textContent = pose.source || '—';
  } else {
    px.textContent = '—';
    py.textContent = '—';
    ph.textContent = '—';
    ps.textContent = 'aucune';
  }
}


// ══════════════════════════════════════════
//  PUSH-TO-TALK
// ══════════════════════════════════════════
let mediaRecorder = null;
let audioChunks   = [];
let mediaStream   = null;

const micBtn    = document.getElementById('micBtn');
const micStatus = document.getElementById('micStatus');

async function startRecording() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    const mimeType = MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')
      ? 'audio/ogg;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
    mediaRecorder = new MediaRecorder(mediaStream, { mimeType });
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = () => sendAudioToServer(mimeType);
    mediaRecorder.start();
    micBtn.classList.add('recording');
    setMicStatus('🔴 En écoute…', 'listening');
    addLog('Enregistrement démarré', 'info');
  } catch (err) {
    addLog('Microphone refusé : ' + err.message, 'err');
    setMicStatus('Maintenir pour parler', '');
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.requestData();
    mediaRecorder.stop();
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  micBtn.classList.remove('recording');
  setMicStatus('Traitement…', 'processing');
}

async function sendAudioToServer(mimeType) {
  if (audioChunks.length === 0) { setMicStatus('Maintenir pour parler', ''); return; }
  const blob     = new Blob(audioChunks, { type: mimeType });
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');
  addLog('Envoi audio...', 'info');
  try {
    const res  = await fetch('/transcribe', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.transcript) {
      document.getElementById('userText').textContent = data.transcript;
      addLog(`Transcription : "${data.transcript}"`, 'info');
    }
    if (data.ai_reply) {
      document.getElementById('aiReply').textContent = data.ai_reply;
      addLog(`MARC : ${data.ai_reply.slice(0, 80)}`, 'info');
    }
    if (data.robot_state) applyRobotState(data.robot_state);
  } catch {
    addLog('Erreur transcription serveur', 'err');
  }
  setMicStatus('Maintenir pour parler', '');
}

micBtn.addEventListener('mousedown',  (e) => { e.preventDefault(); startRecording(); });
micBtn.addEventListener('mouseup',    (e) => { e.preventDefault(); stopRecording(); });
micBtn.addEventListener('mouseleave', ()  => { if (mediaRecorder && mediaRecorder.state === 'recording') stopRecording(); });
micBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); }, { passive: false });
micBtn.addEventListener('touchend',   (e) => { e.preventDefault(); stopRecording(); },  { passive: false });

function setMicStatus(text, cls) {
  micStatus.textContent = text;
  micStatus.className   = 'mic-status' + (cls ? ' ' + cls : '');
}


// ══════════════════════════════════════════
//  JOURNAL
// ══════════════════════════════════════════
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


// ══════════════════════════════════════════
//  CONNEXION TEMPS RÉEL (SSE)
// ══════════════════════════════════════════
let eventSource  = null;
let sseConnected = false;

function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/events');

  eventSource.addEventListener('open', () => {
    if (!sseConnected) {
      sseConnected = true;
      addLog('Connexion temps réel établie', 'info');
      setStatusDot(true);
    }
  });

  // État complet du robot (current, target, mode, pose)
  eventSource.addEventListener('state', (e) => {
    try { applyRobotState(JSON.parse(e.data)); } catch {}
  });

  // Pose temps réel (envoi indépendant haute fréquence depuis localization)
  eventSource.addEventListener('pose', (e) => {
    try { applyPose(JSON.parse(e.data)); } catch {}
  });

  // MARC vient de prononcer une phrase
  eventSource.addEventListener('speech', (e) => {
    try {
      const { text } = JSON.parse(e.data);
      if (text) {
        document.getElementById('aiReply').textContent = text;
        addLog(`MARC : ${text.slice(0, 80)}`, 'info');
      }
    } catch {}
  });

  // Journal
  eventSource.addEventListener('log', (e) => {
    try {
      const { message, level } = JSON.parse(e.data);
      addLog(message, level || 'info');
    } catch {}
  });

  eventSource.addEventListener('error', () => {
    if (sseConnected) {
      sseConnected = false;
      addLog('Connexion temps réel perdue — reconnexion…', 'err');
      setStatusDot(false);
    }
  });
}

function setStatusDot(online) {
  const dot   = document.querySelector('.status-dot');
  const label = document.querySelector('.status-label');
  if (!dot || !label) return;
  if (online) {
    dot.style.background = 'var(--success)';
    label.textContent    = 'TEMPS RÉEL';
  } else {
    dot.style.background = 'var(--danger)';
    label.textContent    = 'HORS LIGNE';
  }
}

connectSSE();
updateStationStyles();


// ══════════════════════════════════════════
//  CAMÉRA (service dédié sur port 5001)
// ══════════════════════════════════════════
const CAMERA_HOST = window.CAMERA_HOST || `https://${window.location.hostname}:5001`;

let cameraOnline = null;   // null = inconnu, pour forcer le 1er affichage

// Met à jour la pastille + le placeholder, et reconnecte le flux au retour en ligne.
function setCameraStatus(online) {
  if (online === cameraOnline) return;   // pas de changement → rien à faire
  const wasOffline = (cameraOnline !== true);
  cameraOnline = online;

  const statusEl = document.getElementById('cameraStatus');
  const feed     = document.getElementById('cameraFeed');

  if (online) {
    if (statusEl) { statusEl.textContent = '● LIVE'; statusEl.classList.remove('offline'); }
    if (feed) feed.classList.remove('cam-error');
    // Le flux MJPEG ne se reconnecte pas seul après une coupure :
    // on relance la source quand on repasse hors ligne → en ligne.
    if (wasOffline && feed) feed.src = `${CAMERA_HOST}/video?t=${Date.now()}`;
    addLog('Caméra en ligne', 'info');
  } else {
    if (statusEl) { statusEl.textContent = '● OFFLINE'; statusEl.classList.add('offline'); }
    if (feed) feed.classList.add('cam-error');
    addLog('Caméra hors ligne', 'err');
  }
}

function initCamera() {
  const feed = document.getElementById('cameraFeed');
  if (feed) {
    // Le flux lui-même est le signal le plus direct de l'état caméra.
    feed.addEventListener('load',  () => setCameraStatus(true));
    feed.addEventListener('error', () => setCameraStatus(false));
    feed.src = `${CAMERA_HOST}/video?t=${Date.now()}`;
  }
  pollCamera();        // heartbeat + détections
}

// Heartbeat caméra : un MJPEG figé ne déclenche pas 'error' côté <img>,
// donc on sonde /health en parallèle pour détecter une coupure en cours de flux.
async function pollCamera() {
  const detEl = document.getElementById('cameraDetections');

  // 1. État (heartbeat) via /health
  try {
    const h = await fetch(`${CAMERA_HOST}/health`, { cache: 'no-store' });
    setCameraStatus(h.ok);
  } catch {
    setCameraStatus(false);
  }

  // 2. Détections ArUco (badges)
  try {
    const res  = await fetch(`${CAMERA_HOST}/detections`, { cache: 'no-store' });
    const data = await res.json();
    const ids  = Object.keys(data);
    if (ids.length === 0) {
      detEl.innerHTML = '<span class="cam-det-empty">Aucun marqueur détecté</span>';
    } else {
      detEl.innerHTML = ids.map(id => {
        const d = data[id];
        const info = (d.distance != null)
          ? `${d.distance}m ${d.angle >= 0 ? '+' : ''}${d.angle}°`
          : 'détecté';
        return `<span class="cam-det-badge">ID ${id} · ${info}</span>`;
      }).join('');
    }
  } catch {
    detEl.innerHTML = '<span class="cam-det-empty">Aucun marqueur détecté</span>';
  }

  setTimeout(pollCamera, 1000);
}

initCamera();
