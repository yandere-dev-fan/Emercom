/**
 * voice_ptt.js — интеграция рации в игровую сессию.
 * Зависит от WalkieAPI (walkie_api.js), который должен быть загружен раньше.
 */

(function () {
    'use strict';

    const root = document.getElementById('session-runtime');
    if (!root) return;

    const sessionCode = root.dataset.sessionCode;
    if (!sessionCode) return;

    // ─── UI-элементы ──────────────────────────────────────────────────────────
    const pttBtn      = document.getElementById('ptt-btn');
    const statusLine  = document.getElementById('ptt-status');
    const channelInfo = document.getElementById('ptt-channel-info');

    function setStatus(text, cls) {
        if (!statusLine) return;
        statusLine.textContent = text;
        statusLine.className = 'ptt-status ' + (cls || '');
    }

    function setChannelInfo(text) {
        if (channelInfo) channelInfo.textContent = text;
    }

    function setPttActive(active) {
        if (!pttBtn) return;
        pttBtn.classList.toggle('ptt-active', active);
        pttBtn.textContent = active ? '🔴 Говорю…' : '🎙 PTT';
    }

    // ─── Инициализация ────────────────────────────────────────────────────────
    async function init() {
        if (typeof window.WalkieAPI === 'undefined') {
            setStatus('WalkieAPI не загружен', 'error');
            return;
        }

        setStatus('Подключение к каналу…');

        // 1. Получить/создать walkie-канал для этой сессии
        let code;
        try {
            const resp = await fetch(`/api/sessions/${sessionCode}/walkie/channel`, {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            code = data.code;
        } catch (err) {
            setStatus(`Ошибка получения канала: ${err.message}`, 'error');
            return;
        }

        setChannelInfo(`Канал: ${code}`);
        setStatus('Вход в канал…');

        // 2. Подписаться на события WalkieAPI
        WalkieAPI.on((event) => {
            switch (event.type) {
                case 'connected':
                    setStatus('Канал готов. Удерживайте PTT для передачи.');
                    if (pttBtn) pttBtn.disabled = false;
                    break;
                case 'disconnected':
                    setStatus('Отключён от канала', 'warn');
                    if (pttBtn) pttBtn.disabled = true;
                    setPttActive(false);
                    break;
                case 'talking_start':
                    setStatus('🔴 Передача…');
                    setPttActive(true);
                    break;
                case 'talking_stop':
                    setStatus('Канал свободен.');
                    setPttActive(false);
                    break;
                case 'channel_busy':
                    setStatus(`📻 Говорит участник ${event.userId}`);
                    break;
                case 'channel_free':
                    setStatus('Канал свободен.');
                    break;
                case 'channel_blocked':
                    setStatus('⚠ Канал занят, подождите.', 'warn');
                    break;
                case 'peer_joined':
                    setStatus(`Подключился участник (всего: ${event.userCount})`);
                    break;
                case 'peer_left':
                    setStatus(`Участник отключился (всего: ${event.userCount})`);
                    break;
                case 'error':
                    setStatus(`Ошибка рации: ${event.message}`, 'error');
                    break;
            }
        });

        // 3. Войти в канал
        try {
            await WalkieAPI.joinChannel(code);
        } catch (err) {
            setStatus(`Не удалось войти в канал: ${err.message}`, 'error');
        }
    }

    // ─── PTT-кнопка ───────────────────────────────────────────────────────────
    if (pttBtn) {
        pttBtn.disabled = true;

        // Мышь
        pttBtn.addEventListener('mousedown', () => WalkieAPI?.startTalking());
        pttBtn.addEventListener('mouseup',   () => WalkieAPI?.stopTalking());
        pttBtn.addEventListener('mouseleave',() => WalkieAPI?.stopTalking());

        // Тач (мобильные)
        pttBtn.addEventListener('touchstart', (e) => { e.preventDefault(); WalkieAPI?.startTalking(); });
        pttBtn.addEventListener('touchend',   (e) => { e.preventDefault(); WalkieAPI?.stopTalking();  });
    }

    // ─── Горячая клавиша Space ────────────────────────────────────────────────
    let spaceHeld = false;
    document.addEventListener('keydown', (e) => {
        if (e.code === 'Space' && e.target === document.body && !spaceHeld) {
            e.preventDefault();
            spaceHeld = true;
            WalkieAPI?.startTalking();
        }
    });
    document.addEventListener('keyup', (e) => {
        if (e.code === 'Space' && spaceHeld) {
            e.preventDefault();
            spaceHeld = false;
            WalkieAPI?.stopTalking();
        }
    });

    // ─── Старт ────────────────────────────────────────────────────────────────
    init().catch((err) => setStatus(`Ошибка инициализации: ${err.message}`, 'error'));
})();
