import { connectSessionSocket, parsePolyline, postJson } from "./map_editor_core.js";
import { initChat } from "./chat.js";

const root = document.getElementById("session-runtime");

if (root) {
    const sessionCode = root.dataset.sessionCode;
    const csrfToken = root.dataset.csrfToken;
    const roleLabels = {
        instructor: "создатель",
        dispatcher: "диспетчер",
        rtp: "РТП",
        observer: "наблюдатель",
        waiting: "ожидание",
    };
    const statusLabels = {
        setup: "подготовка",
        dispatch_call: "вызов",
        enroute: "в пути",
        recon: "разведка",
        tactical: "тактика",
        contained: "локализация",
        finished: "завершено",
    };
    const vehicleStatusLabels = {
        staged: "ожидает",
        moving: "движение",
        driving: "ручное управление",
    };
    const eventLabels = {
        wind_shift: "Смена ветра",
        water_source_failure: "Проблема с водоисточником",
        vehicle_breakdown: "Поломка техники",
        route_blocked: "Перекрытие маршрута",
        secondary_fire: "Вторичный очаг",
        collapse_warning: "Риск обрушения",
        visibility_drop: "Падение видимости",
    };

    const statusEl = document.getElementById("runtime-status");
    const timeScaleEl = document.getElementById("runtime-time-scale");
    const elapsedEl = document.getElementById("runtime-elapsed");
    const participantsEl = document.getElementById("runtime-participants");
    const vehiclesEl = document.getElementById("runtime-vehicles");
    const eventsEl = document.getElementById("runtime-events");

    async function refreshState() {
        const response = await fetch(`/api/sessions/${sessionCode}/state`, { credentials: "same-origin" });
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        statusEl.textContent = statusLabels[payload.status] || payload.status;
        timeScaleEl.textContent = `${payload.time_scale}x`;
        elapsedEl.textContent = `${payload.time_elapsed_seconds}с`;
        participantsEl.innerHTML = payload.participants
            .map((participant) => `<div class="row spread align-center"><span>${participant.display_name || participant.id.slice(0, 8)}</span><span class="pill subtle">${roleLabels[participant.role] || participant.role}</span></div>`)
            .join("");
        vehiclesEl.innerHTML = payload.vehicles
            .map((vehicle) => `<div class="row spread align-center"><span>${vehicle.display_name}</span><span class="pill subtle">${vehicleStatusLabels[vehicle.status] || vehicle.status}</span></div>`)
            .join("");
    }

    document.querySelectorAll("[data-time-scale]").forEach((button) => {
        button.addEventListener("click", async () => {
            try {
                await postJson(`/api/sessions/${sessionCode}/time-scale`, csrfToken, { time_scale: Number(button.dataset.timeScale) });
                await refreshState();
            } catch (error) {
                window.alert(error.message);
            }
        });
    });

    const eventButton = document.getElementById("create-runtime-event-btn");
    if (eventButton) {
        eventButton.addEventListener("click", async () => {
            try {
                const eventType = document.getElementById("runtime-event-type").value;
                const rawPayload = document.getElementById("runtime-event-payload").value || "{}";
                await postJson(`/api/sessions/${sessionCode}/events`, csrfToken, {
                    event_type: eventType,
                    payload: JSON.parse(rawPayload),
                });
            } catch (error) {
                window.alert(error.message);
            }
        });
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
                const vehicleTypes = [...document.querySelectorAll(".dispatch-vehicle-type:checked")].map((node) => node.value);
                await postJson(`/api/sessions/${sessionCode}/dispatch/orders`, csrfToken, { vehicle_types: vehicleTypes });
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
                await postJson(`/api/sessions/${sessionCode}/vehicles/${vehicleId}/route`, csrfToken, { points });
                await refreshState();
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
                await postJson(`/api/sessions/${sessionCode}/vehicles/${vehicleId}/drive-intent`, csrfToken, {
                    heading_deg: Number(document.getElementById("rtp-heading").value),
                    speed_mps: Number(document.getElementById("rtp-speed").value),
                });
                await refreshState();
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

    connectSessionSocket(sessionCode, csrfToken, (message) => {
        if (message.type === "scenario_tick") {
            if (typeof message.time_elapsed_seconds === "number") {
                elapsedEl.textContent = `${message.time_elapsed_seconds}с`;
            }
            if (typeof message.time_scale === "number") {
                timeScaleEl.textContent = `${message.time_scale}x`;
            }
            if (typeof message.status === "string") {
                statusEl.textContent = statusLabels[message.status] || message.status;
            }
        } else if (["session_phase_changed", "participant_role_updated", "vehicle_state_changed"].includes(message.type)) {
            refreshState().catch(() => { });
        } else if (message.type === "event_created" && eventsEl) {
            const row = document.createElement("div");
            row.className = "pill subtle";
            row.textContent = eventLabels[message.event_type] || message.event_type || "событие";
            eventsEl.prepend(row);
        }
    });

    initChat(sessionCode, csrfToken, root.dataset.role || "");
}
