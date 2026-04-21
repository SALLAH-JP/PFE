const STATIONS = {
  base:   { cx: 290, cy: 190 },
  nao:    { cx: 52,  cy: 190 },
  vector: { cx: 183, cy: 46  },
  pepper: { cx: 397, cy: 46  },
  imp3d:  { cx: 528, cy: 190 },
  baxter: { cx: 397, cy: 334 },
  bras:   { cx: 183, cy: 334 },
};

let currentStation  = 'base';
let targetStation   = null;
let guideModeActive = false;

// ── Mode guide ──
async function toggleGuideMode() {
  guideModeActive = !guideModeActive;
  updateGuideButton();
  try {
    const res  = await fetch('/line_following', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ enabled: guideModeActive }),
    });
    const data = await res.json();
    guideModeActive = data.line_following;
    updateGuideButton();
    addLog(`Mode guide : ${guideModeActive ? 'ON' : 'OFF'}`, guideModeActive ? 'cmd' : 'info');
  } catch {
    addLog('Erreur toggle mode guide', 'err');
  }
}

function updateGuideButton() {
  const btn   = document.getElementById('guideToggle');
  const label = document.getElementById('guideLabel');
  label.textContent = `Mode guide ${guideModeActive ? 'ON' : 'OFF'}`;
  btn.classList.toggle('guide-on', guideModeActive);
}

// ── Déplacer MARC ──
function moveMarc(stationId) {
  const s = STATIONS[stationId];
  if (!s) return;
  document.getElementById('marc-dot').setAttribute('cx', s.cx);
  document.getElementById('marc-dot').setAttribute('cy', s.cy);
  document.getElementById('marc-glow').setAttribute('cx', s.cx);
  document.getElementById('marc-glow').setAttribute('cy', s.cy);
  document.getElementById('marc-text').setAttribute('x', s.cx);
  document.getElementById('marc-text').setAttribute('y', s.cy + 4);
}

// ── Styles stations ──
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

// ── Sélection destination ──
function selectStation(id) {
  if (id === currentStation) {
    addLog(`MARC est déjà à ${id}`, 'info');
    return;
  }
  targetStation = id;
  updateStationStyles();
  addLog(`Destination → ${id.toUpperCase()}`, 'cmd');
  sendDestination(id);
}

// ── Envoi destination ──
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

// ── Simulation locale ──
function simulateMove(dest) {
  addLog(`MARC se déplace vers ${dest.toUpperCase()}...`, 'info');
  setTimeout(() => {
    currentStation = dest;
    targetStation  = null;
    moveMarc(dest);
    updateStationStyles();
    addLog(`MARC est arrivé à ${dest.toUpperCase()}`, 'info');
    document.getElementById('aiReply').textContent = `Je suis arrivé à ${dest}.`;
  }, 1500);
}

// ── Appliquer état serveur ──
function applyRobotState(state) {
  if (state.current) {
    currentStation = state.current;
    moveMarc(state.current);
  }
  if (state.target !== undefined) targetStation = state.target;
  if (state.line_following !== undefined) {
    guideModeActive = state.line_following;
    updateGuideButton();
  }
  updateStationStyles();
}

// ═══════════════════════════════════════════
//  PUSH-TO-TALK
// ═══════════════════════════════════════════
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
    addLog('Erreur serveur — fallback Web Speech', 'err');
    fallbackWebSpeech();
  }
  setMicStatus('Maintenir pour parler', '');
}

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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_text: transcript }),
      });
      const data = await res.json();
      if (data.ai_reply)    document.getElementById('aiReply').textContent = data.ai_reply;
      if (data.robot_state) applyRobotState(data.robot_state);
    } catch { document.getElementById('aiReply').textContent = "Commande non reconnue."; }
  };
  rec.onerror = (e) => addLog('Fallback erreur : ' + e.error, 'err');
  rec.start();
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

fetch('/status')
  .then(r => r.json())
  .then(d => {
    if (d.robot_state) applyRobotState(d.robot_state);
    addLog('Connecté au serveur', 'info');
  })
  .catch(() => addLog('Mode démo — serveur non joignable', 'err'));

moveMarc(currentStation);
updateStationStyles();