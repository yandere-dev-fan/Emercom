/**
 * WalkieAPI — встраиваемая рация без UI.
 *
 * Подключить: <script src="/walkie_api.js"></script>
 * Если сервер на другом домене: <script>window.WALKIE_SERVER = "https://your-server.com"</script>
 *
 * Использование:
 *
 *   // Подписка на события
 *   WalkieAPI.on(event => console.log(event.type, event));
 *
 *   // Создать канал
 *   const { code } = await WalkieAPI.createChannel();
 *
 *   // Войти в канал
 *   await WalkieAPI.joinChannel("ABC123");
 *
 *   // PTT
 *   WalkieAPI.startTalking();
 *   WalkieAPI.stopTalking();
 *
 *   // Выйти
 *   WalkieAPI.leave();
 *
 *   // Текущее состояние
 *   WalkieAPI.getState(); // { connected, channelCode, userId, isTalking, channelBusy, busyBy }
 *
 * События (event.type):
 *   connected        — подключились { code, userId, userCount }
 *   disconnected     — отключились  { code, reason }
 *   peer_joined      — вошёл юзер   { peerId, userCount }
 *   peer_left        — вышел юзер   { peerId, userCount }
 *   channel_busy     — кто-то говорит { userId }
 *   channel_free     — эфир свободен  { userId }
 *   channel_blocked  — эфир занят, PTT отклонён
 *   talking_start    — ты начал говорить (после задержки PTT)
 *   talking_stop     — ты перестал говорить
 *   error            — ошибка { message }
 */

(function () {
  'use strict';

  const SERVER_BASE = window.WALKIE_SERVER || '';
  const ICE_SERVERS = [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' },
  ];
  const PTT_DELAY_MS = 150;

  // ─── State ─────────────────────────────────────────────────────────────────
  let ws = null, myId = null, channelCode = null;
  let localStream = null, rawStream = null, audioCtx = null, micGain = null;
  let isTalking = false, channelBusy = false, busyById = null;
  const peers = {}, listeners = new Set();

  // ─── Events ────────────────────────────────────────────────────────────────
  function emit(type, data) {
    const e = Object.assign({ type }, data);
    listeners.forEach(fn => { try { fn(e); } catch(_) {} });
    window._walkieLastEvent = e;
  }

  // ─── Audio ─────────────────────────────────────────────────────────────────
  function makeDistortionCurve(amount) {
    const n = 256, c = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const x = (i * 2) / n - 1;
      c[i] = ((Math.PI + amount) * x) / (Math.PI + amount * Math.abs(x));
    }
    return c;
  }

  // TX: EQ + дисторшн + компрессор — только для исходящего голоса
  function buildTxChain(src, ctx, out) {
    const hp = ctx.createBiquadFilter(); hp.type='highpass'; hp.frequency.value=300; hp.Q.value=0.8;
    const lp = ctx.createBiquadFilter(); lp.type='lowpass';  lp.frequency.value=3400; lp.Q.value=0.8;
    const ws = ctx.createWaveShaper(); ws.curve=makeDistortionCurve(18); ws.oversample='4x';
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value=-24; comp.knee.value=10; comp.ratio.value=6;
    comp.attack.value=0.003; comp.release.value=0.1;
    const g = ctx.createGain(); g.gain.value=0.9;
    src.connect(hp); hp.connect(lp); lp.connect(ws); ws.connect(comp); comp.connect(g); g.connect(out);
  }

  // RX: только EQ + компрессор, БЕЗ дисторшна
  // Дисторшн на тишине создаёт постоянный писк — поэтому здесь его нет
  function buildRxChain(src, ctx, out) {
    const hp = ctx.createBiquadFilter(); hp.type='highpass'; hp.frequency.value=300; hp.Q.value=0.7;
    const lp = ctx.createBiquadFilter(); lp.type='lowpass';  lp.frequency.value=3400; lp.Q.value=0.7;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value=-20; comp.knee.value=8; comp.ratio.value=4;
    comp.attack.value=0.005; comp.release.value=0.15;
    const g = ctx.createGain(); g.gain.value=1.0;
    src.connect(hp); hp.connect(lp); lp.connect(comp); comp.connect(g); g.connect(out);
  }

  function playNoiseBurst(vol, dur) {
    if (!audioCtx) return;
    const ctx = audioCtx;
    const buf = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * dur), ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource(); src.buffer = buf;
    const bp = ctx.createBiquadFilter(); bp.type='bandpass'; bp.frequency.value=1400; bp.Q.value=0.4;
    const g = ctx.createGain(); const now = ctx.currentTime;
    g.gain.setValueAtTime(vol, now);
    g.gain.exponentialRampToValueAtTime(0.001, now + dur);
    src.connect(bp); bp.connect(g); g.connect(ctx.destination);
    src.start(); src.stop(now + dur);
  }

  function playClick(isStart) {
    if (!audioCtx) return;
    const ctx = audioCtx;
    const osc = ctx.createOscillator(); osc.type='square';
    osc.frequency.value = isStart ? 1100 : 750;
    const g = ctx.createGain(); const now = ctx.currentTime;
    g.gain.setValueAtTime(0.4, now);
    g.gain.exponentialRampToValueAtTime(0.001, now + 0.045);
    osc.connect(g); g.connect(ctx.destination);
    osc.start(); osc.stop(now + 0.05);
  }

  async function acquireMic() {
    if (localStream) return localStream;
    rawStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      video: false,
    });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') await audioCtx.resume();
    micGain = audioCtx.createGain(); micGain.gain.value = 0;
    const dest = audioCtx.createMediaStreamDestination();
    audioCtx.createMediaStreamSource(rawStream).connect(micGain);
    buildTxChain(micGain, audioCtx, dest);
    localStream = dest.stream;
    return localStream;
  }

  // ─── WebRTC ────────────────────────────────────────────────────────────────
  function createPc(peerId) {
    if (peers[peerId]) return peers[peerId];
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    peers[peerId] = pc;
    if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
    pc.ontrack = (e) => {
      if (!audioCtx) return;
      if (audioCtx.state === 'suspended') audioCtx.resume();
      buildRxChain(audioCtx.createMediaStreamSource(e.streams[0]), audioCtx, audioCtx.destination);
    };
    pc.onicecandidate = (e) => { if (e.candidate) wsSend({ type:'ice', target:peerId, candidate:e.candidate }); };
    pc.onconnectionstatechange = () => {
      if (['failed','disconnected','closed'].includes(pc.connectionState)) closePeer(peerId);
    };
    return pc;
  }

  function closePeer(id) { if (peers[id]) { peers[id].close(); delete peers[id]; } }

  async function makeOffer(peerId) {
    const pc = createPc(peerId);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    wsSend({ type:'offer', target:peerId, sdp:pc.localDescription });
  }

  async function handleOffer(from, sdp) {
    const pc = createPc(from);
    await pc.setRemoteDescription(new RTCSessionDescription(sdp));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    wsSend({ type:'answer', target:from, sdp:pc.localDescription });
  }

  async function handleAnswer(from, sdp) {
    const pc = peers[from]; if (!pc) return;
    if (pc.signalingState === 'have-local-offer')
      await pc.setRemoteDescription(new RTCSessionDescription(sdp));
  }

  async function handleIce(from, candidate) {
    const pc = peers[from]; if (!pc) return;
    try { await pc.addIceCandidate(new RTCIceCandidate(candidate)); } catch(_) {}
  }

  // ─── WebSocket ─────────────────────────────────────────────────────────────
  function wsSend(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function connectWS(code) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const base = SERVER_BASE
      ? SERVER_BASE.replace(/^http/, proto).replace(/\/+$/, '')
      : proto + '://' + location.host;
    ws = new WebSocket(`${base}/ws/${code}`);
    ws.onmessage = async (e) => {
      const m = JSON.parse(e.data);
      switch (m.type) {
        case 'welcome':
          myId = m.user_id; channelBusy = m.busy; busyById = m.busy_by;
          emit('connected', { code, userId: myId, userCount: m.user_count });
          break;
        case 'peer_joined':
          emit('peer_joined', { peerId: m.peer_id, userCount: m.user_count });
          await makeOffer(m.peer_id);
          break;
        case 'peer_left':
          closePeer(m.peer_id);
          emit('peer_left', { peerId: m.peer_id, userCount: m.user_count });
          break;
        case 'offer':  await handleOffer(m.from, m.sdp); break;
        case 'answer': await handleAnswer(m.from, m.sdp); break;
        case 'ice':    await handleIce(m.from, m.candidate); break;
        case 'channel_busy':
          channelBusy = true; busyById = m.user_id;
          if (m.user_id !== myId) { playClick(true); playNoiseBurst(0.2, 0.2); emit('channel_busy', { userId: m.user_id }); }
          break;
        case 'channel_free':
          channelBusy = false; busyById = null;
          if (!isTalking) { if (m.user_id !== myId) { playClick(false); playNoiseBurst(0.1, 0.1); } emit('channel_free', { userId: m.user_id }); }
          break;
        case 'channel_blocked': emit('channel_blocked', {}); break;
      }
    };
    ws.onclose = (e) => emit('disconnected', { code: e.code, reason: e.reason });
    ws.onerror = () => emit('error', { message: 'WebSocket error' });
  }

  // ─── PTT ───────────────────────────────────────────────────────────────────
  function startTalking() {
    if (isTalking) return;
    if (channelBusy && busyById !== myId) { emit('channel_blocked', {}); return; }
    isTalking = true;
    playClick(true); playNoiseBurst(0.3, 0.28);
    setTimeout(() => {
      if (!isTalking) return;
      if (micGain) micGain.gain.setTargetAtTime(1, audioCtx.currentTime, 0.02);
      wsSend({ type: 'ptt_start' });
      emit('talking_start', {});
    }, PTT_DELAY_MS);
  }

  function stopTalking() {
    if (!isTalking) return;
    isTalking = false;
    if (micGain) micGain.gain.setTargetAtTime(0, audioCtx.currentTime, 0.01);
    wsSend({ type: 'ptt_stop' });
    playClick(false); playNoiseBurst(0.15, 0.12);
    emit('talking_stop', {});
  }

  function cleanup() {
    stopTalking();
    if (ws) { ws.close(); ws = null; }
    Object.keys(peers).forEach(closePeer);
    if (rawStream) { rawStream.getTracks().forEach(t => t.stop()); rawStream = null; }
    if (audioCtx) { audioCtx.close(); audioCtx = null; }
    localStream = null; micGain = null; myId = null; channelCode = null;
    channelBusy = false; busyById = null; isTalking = false;
  }

  // ─── Public API ────────────────────────────────────────────────────────────
  window.WalkieAPI = {
    /** Подписка на события. Возвращает функцию отписки. */
    on(fn) { listeners.add(fn); return () => listeners.delete(fn); },

    /** Создать канал → Promise<{ code, expiresInSeconds }> */
    async createChannel() {
      await acquireMic();
      const res = await fetch(`${SERVER_BASE}/create`, { method: 'POST' });
      if (!res.ok) throw new Error('Server error');
      const { code: c } = await res.json();
      channelCode = c;
      connectWS(c);
      return { code: c, expiresInSeconds: 600 };
    },

    /** Войти в канал по коду → Promise<void> */
    async joinChannel(code) {
      code = String(code).toUpperCase().trim();
      await acquireMic();
      const res = await fetch(`${SERVER_BASE}/check/${code}`);
      const { ok, reason } = await res.json();
      if (!ok) throw new Error(reason || 'channel_not_found');
      channelCode = code;
      connectWS(code);
    },

    /** PTT нажата */
    startTalking,

    /** PTT отпущена */
    stopTalking,

    /** Покинуть канал */
    leave() { cleanup(); },

    /** Текущее состояние */
    getState() {
      return {
        connected:   !!(ws && ws.readyState === WebSocket.OPEN),
        channelCode, userId: myId, isTalking, channelBusy, busyBy: busyById,
      };
    },

    /** Для poll-loop (Godot и т.п.): забрать последнее событие как JSON-строку */
    pollEvent() {
      const e = window._walkieLastEvent || null;
      window._walkieLastEvent = null;
      return e ? JSON.stringify(e) : '';
    },
  };

  console.log('[WalkieAPI] ready');
})();
