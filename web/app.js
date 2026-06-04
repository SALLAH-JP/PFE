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
    // L'affichage progressif et le journal de la réponse de MARC sont gérés
    // en flux via SSE ('speech'). Filet de sécurité d'affichage seulement.
    if (data.ai_reply) document.getElementById('aiReply').textContent = data.ai_reply;
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
let speechBuffer = '';   // accumule les phrases de MARC reçues en flux (SSE)

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

  // Début d'un nouvel énoncé de MARC (flux) → on réinitialise l'affichage
  eventSource.addEventListener('speech_start', () => {
    speechBuffer = '';
    document.getElementById('aiReply').textContent = '';
  });

  // MARC prononce une phrase. En flux : { text, partial:true } pour chaque
  // phrase, puis { text:<complet>, done:true } à la fin. Mono-bloc : { text }.
  eventSource.addEventListener('speech', (e) => {
    try {
      const d = JSON.parse(e.data);
      if (!d.text && !d.done) return;

      if (d.partial) {
        // Phrase intermédiaire : on l'ajoute au fur et à mesure
        speechBuffer += (speechBuffer ? ' ' : '') + d.text;
        document.getElementById('aiReply').textContent = speechBuffer;
      } else {
        // Énoncé complet (fin de flux ou message mono-bloc) :
        // filet de sécurité d'affichage + une seule entrée de journal
        const full = d.text || speechBuffer;
        document.getElementById('aiReply').textContent = full;
        if (full) addLog(`MARC : ${full.slice(0, 80)}`, 'info');
        speechBuffer = '';
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

let cameraOnline    = null;   // null = inconnu, pour forcer le 1er affichage
let healthFailCount = 0;      // /health doit échouer plusieurs fois d'affilée
                              //   avant qu'on déclare OFFLINE (anti-blip)

// Met à jour UNIQUEMENT la pastille et le placeholder. Ne touche JAMAIS à feed.src.
function setCameraStatus(online) {
  if (online === cameraOnline) return;
  cameraOnline = online;

  const statusEl = document.getElementById('cameraStatus');
  const feed     = document.getElementById('cameraFeed');

  if (online) {
    if (statusEl) { statusEl.textContent = '● LIVE'; statusEl.classList.remove('offline'); }
    if (feed) feed.classList.remove('cam-error');
    addLog('Caméra en ligne', 'info');
  } else {
    if (statusEl) { statusEl.textContent = '● OFFLINE'; statusEl.classList.add('offline'); }
    if (feed) feed.classList.add('cam-error');
    addLog('Caméra hors ligne', 'err');
  }
}

function reconnectFeed() {
  const feed = document.getElementById('cameraFeed');
  if (feed) feed.src = `${CAMERA_HOST}/video?t=${Date.now()}`;
}

function initCamera() {
  const feed = document.getElementById('cameraFeed');
  if (feed) {
    // Le flux lui-même est le seul signal qui PEUT relancer le src.
    feed.addEventListener('load',  () => setCameraStatus(true));
    feed.addEventListener('error', () => {
      setCameraStatus(false);
      // Vraie panne du flux : on retente une fois après 2 s
      setTimeout(() => { if (cameraOnline === false) reconnectFeed(); }, 2000);
    });
    reconnectFeed();
  }
  pollCamera();
}

// Heartbeat : /health met à jour la pastille seulement après plusieurs
// échecs consécutifs, et ne touche JAMAIS feed.src (qui aurait pour effet
// de couper un flux MJPEG vivant à chaque blip réseau).
async function pollCamera() {
  let healthOk = false;
  try {
    const h = await fetch(`${CAMERA_HOST}/health`, { cache: 'no-store' });
    healthOk = h.ok;
  } catch { /* ignoré */ }

  if (healthOk) {
    const wasOffline = (cameraOnline === false);
    healthFailCount = 0;
    setCameraStatus(true);
    // Le serveur vient de revenir après une vraie panne → on relance le flux
    // (qui est probablement figé sur sa dernière trame).
    if (wasOffline) reconnectFeed();
  } else {
    healthFailCount++;
    if (healthFailCount >= 3) setCameraStatus(false);
  }

  // Détections ArUco (badges)
  const detEl = document.getElementById('cameraDetections');
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
