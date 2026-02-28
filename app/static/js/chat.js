import { onEvent } from "./ws.js";

const chatThreadSelect = document.getElementById("chat-thread");
const chatMessages = document.getElementById("chat-messages");
const chatInput = document.getElementById("chat-body");
const chatSendBtn = document.getElementById("chat-send-btn");

let currentThread = "system";
let sessionCode = "";
let csrfToken = "";
let role = "";
let messagesByThread = {
    "instructor_dispatcher": [],
    "dispatcher_rtp": [],
    "system": []
};

// Available threads by role
const threadsByRole = {
    "instructor": ["instructor_dispatcher", "dispatcher_rtp", "system"],
    "admin": ["instructor_dispatcher", "dispatcher_rtp", "system"],
    "dispatcher": ["instructor_dispatcher", "dispatcher_rtp", "system"],
    "rtp": ["dispatcher_rtp", "system"],
    "observer": ["system"],
    "waiting": ["system"]
};

const threadLabels = {
    "instructor_dispatcher": "Создатель ↔ Диспетчер",
    "dispatcher_rtp": "Диспетчер ↔ РТП",
    "system": "Система"
};

function formatTime(isoString) {
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderMessage(msg) {
    const el = document.createElement("div");
    el.className = "chat-msg pill subtle";
    el.style.display = "flex";
    el.style.flexDirection = "column";
    el.style.alignItems = "flex-start";
    el.innerHTML = `
        <span style="font-size:0.8em; color:var(--text-muted); margin-bottom: 2px;">
            ${msg.participant_id ? msg.participant_id.substring(0, 8) : 'Система'} [${formatTime(msg.created_at)}]
        </span>
        <span style="word-break: break-word;">${msg.body}</span>
    `;
    return el;
}

function renderChat() {
    chatMessages.innerHTML = "";
    if (messagesByThread[currentThread]) {
        messagesByThread[currentThread].forEach(msg => {
            chatMessages.appendChild(renderMessage(msg));
        });
    }
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function loadThread(threadKey) {
    try {
        const res = await fetch(`/api/sessions/${sessionCode}/chat/messages?thread_key=${threadKey}`);
        if (res.ok) {
            const data = await res.json();
            messagesByThread[threadKey] = data.items;
            if (currentThread === threadKey) {
                renderChat();
            }
        }
    } catch (e) {
        console.error("Failed to load chat thread", e);
    }
}

async function sendMessage() {
    const body = chatInput.value.trim();
    if (!body || !currentThread) return;

    chatInput.disabled = true;
    chatSendBtn.disabled = true;

    try {
        const res = await fetch(`/api/sessions/${sessionCode}/chat/messages`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken
            },
            body: JSON.stringify({
                thread_key: currentThread,
                body: body
            })
        });
        if (res.ok) {
            chatInput.value = "";
        }
    } catch (e) {
        console.error("Chat send error", e);
    } finally {
        chatInput.disabled = false;
        chatSendBtn.disabled = false;
        chatInput.focus();
    }
}

export function initChat(sessCode, csrf, userRole) {
    sessionCode = sessCode;
    csrfToken = csrf;
    role = userRole;

    const available = threadsByRole[role] || ["system"];
    chatThreadSelect.innerHTML = "";
    available.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = threadLabels[t] || t;
        chatThreadSelect.appendChild(opt);
    });

    // Default thread logic
    if (available.includes("instructor_dispatcher")) currentThread = "instructor_dispatcher";
    else if (available.includes("dispatcher_rtp")) currentThread = "dispatcher_rtp";
    else currentThread = "system";

    chatThreadSelect.value = currentThread;

    available.forEach(t => loadThread(t));

    chatThreadSelect.addEventListener("change", (e) => {
        currentThread = e.target.value;
        renderChat();
    });

    chatSendBtn.addEventListener("click", sendMessage);
    chatInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") sendMessage();
    });

    chatInput.disabled = false;
    chatSendBtn.disabled = false;

    onEvent("chat_message_created", (payload) => {
        const msg = payload.message;
        if (messagesByThread[msg.thread_key]) {
            messagesByThread[msg.thread_key].push(msg);
            if (currentThread === msg.thread_key) {
                renderChat();
            }
        }
    });

    // Handle phase change to auto-switch dispatcher thread
    onEvent("session_phase_changed", (payload) => {
        // If it's a dispatcher, switch to rtp thread when recon or tactical starts
        if (role === "dispatcher" && payload.status && payload.status !== "setup" && payload.status !== "dispatch_call" && payload.status !== "enroute") {
            if (chatThreadSelect.querySelector('option[value="dispatcher_rtp"]')) {
                chatThreadSelect.value = "dispatcher_rtp";
                currentThread = "dispatcher_rtp";
                renderChat();
            }
        }
    });
}
