import { connectSessionSocket, parsePolyline, postJson } from "./map_editor_core.js";

const root = document.getElementById("session-runtime");

if (root) {
    const sessionCode = root.dataset.sessionCode;
    const csrfToken = root.dataset.csrfToken;
    const statusEl = document.getElementById("runtime-status");
    const elapsedEl = document.getElementById("runtime-elapsed");
    const eventsEl = document.getElementById("runtime-events");
    const chatMessagesEl = document.getElementById("chat-messages");
    const chatThreadEl = document.getElementById("chat-thread");
    const chatBodyEl = document.getElementById("chat-body");

    const statusLabels = {
        setup: "подготовка",
        dispatch_call: "вызов",
        enroute: "в пути",
        tactical: "объект",
        contained: "локализация",
        finished: "завершено",
    };

    async function refreshState() {
        const response = await fetch(`/api/sessions/${sessionCode}/state`, { credentials: "same-origin" });
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        statusEl.textContent = statusLabels[payload.status] || payload.status;
        elapsedEl.textContent = `${payload.time_elapsed_minutes} мин`;
    }

    async function loadChat() {
        if (!chatThreadEl || !chatMessagesEl) {
            return;
        }
        const response = await fetch(`/api/sessions/${sessionCode}/chat/messages?thread_key=${encodeURIComponent(chatThreadEl.value)}`, {
            credentials: "same-origin",
        });
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        chatMessagesEl.innerHTML = payload.items.map((item) => `<div class="pill subtle">${item.body}</div>`).join("") || '<div class="pill subtle">Пока пусто.</div>';
    }

    const markButton = document.getElementById("dispatcher-mark-btn");
    if (markButton) {
        markButton.addEventListener("click", async () => {
            try {
                const guessIndex = Number(document.getElementById("dispatcher-guess-index").value);
                await postJson(`/api/sessions/${sessionCode}/dispatcher/mark-incident`, csrfToken, { guess_index: guessIndex });
                await refreshState();
            } catch (error) {
                window.alert(error.message);
            }
        });
    }

    const dispatchButton = document.getElementById("dispatch-order-btn");
    if (dispatchButton) {
        dispatchButton.addEventListener("click", async () => {
            try {
                await postJson(`/api/sessions/${sessionCode}/dispatch/orders`, csrfToken, {
                    counts: {
                        FIRE_ENGINE: Number(document.getElementById("dispatch-count-fire-engine").value),
                        LADDER_ENGINE: Number(document.getElementById("dispatch-count-ladder-engine").value),
                    },
                });
                await refreshState();
            } catch (error) {
                window.alert(error.message);
            }
        });
    }

    const routeButton = document.getElementById("rtp-route-btn");
    if (routeButton) {
        routeButton.addEventListener("click", async () => {
            try {
                const vehicleId = document.getElementById("rtp-vehicle-id").value;
                const points = parsePolyline(document.getElementById("rtp-route-points").value);
                await postJson(`/api/sessions/${sessionCode}/vehicles/${vehicleId}/object-route`, csrfToken, { points });
            } catch (error) {
                window.alert(error.message);
            }
        });
    }

    const driveButton = document.getElementById("rtp-drive-btn");
    if (driveButton) {
        driveButton.addEventListener("click", async () => {
            try {
                const vehicleId = document.getElementById("rtp-vehicle-id").value;
                await postJson(`/api/sessions/${sessionCode}/vehicles/${vehicleId}/object-drive`, csrfToken, {
                    heading_deg: Number(document.getElementById("rtp-heading").value),
                    speed_mps: Number(document.getElementById("rtp-speed").value),
                });
            } catch (error) {
                window.alert(error.message);
            }
        });
    }

    const hoseButton = document.getElementById("rtp-hose-btn");
    if (hoseButton) {
        hoseButton.addEventListener("click", async () => {
            try {
                const vehicleId = document.getElementById("rtp-vehicle-id").value;
                const polylinePoints = parsePolyline(document.getElementById("rtp-hose-points").value);
                await postJson(`/api/sessions/${sessionCode}/hoses`, csrfToken, {
                    source_vehicle_id: vehicleId,
                    polyline_points: polylinePoints,
                });
            } catch (error) {
                window.alert(error.message);
            }
        });
    }

    const eventButton = document.getElementById("create-runtime-event-btn");
    if (eventButton) {
        eventButton.addEventListener("click", async () => {
            try {
                const eventType = document.getElementById("runtime-event-type").value;
                const rawPayload = document.getElementById("runtime-event-payload").value || "{}";
                await postJson(`/api/sessions/${sessionCode}/events`, csrfToken, { event_type: eventType, payload: JSON.parse(rawPayload) });
            } catch (error) {
                window.alert(error.message);
            }
        });
    }

    const sendChatButton = document.getElementById("chat-send-btn");
    if (sendChatButton && chatThreadEl && chatBodyEl) {
        sendChatButton.addEventListener("click", async () => {
            const body = chatBodyEl.value.trim();
            if (!body) {
                return;
            }
            try {
                await postJson(`/api/sessions/${sessionCode}/chat/messages`, csrfToken, {
                    thread_key: chatThreadEl.value,
                    body,
                });
                chatBodyEl.value = "";
                await loadChat();
            } catch (error) {
                window.alert(error.message);
            }
        });
        chatThreadEl.addEventListener("change", () => {
            loadChat().catch(() => {});
        });
    }

    connectSessionSocket(sessionCode, csrfToken, (message) => {
        if (message.type === "scenario_tick") {
            if (typeof message.time_elapsed_minutes === "number") {
                elapsedEl.textContent = `${message.time_elapsed_minutes} мин`;
            }
            if (typeof message.status === "string") {
                statusEl.textContent = statusLabels[message.status] || message.status;
            }
        } else if (message.type === "event_created" && eventsEl) {
            const row = document.createElement("div");
            row.className = "pill subtle";
            row.textContent = message.event_type || "событие";
            eventsEl.prepend(row);
        } else if (message.type === "chat_message_created") {
            loadChat().catch(() => {});
        } else if (["session_phase_changed", "vehicle_path_updated", "object_phase_unlocked"].includes(message.type)) {
            refreshState().catch(() => {});
        }
    });

    loadChat().catch(() => {});
}
