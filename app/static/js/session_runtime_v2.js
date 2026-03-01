import { connectSessionSocket, postJson } from "./map_editor_core.js";

const root = document.getElementById("session-runtime");

if (root) {
    const sessionCode = root.dataset.sessionCode;
    const csrfToken = root.dataset.csrfToken;
    const rawRole = root.dataset.role || "observer";
    const role = rawRole === "admin" ? "instructor" : rawRole;
    const defaultThread = root.dataset.defaultThread || "system";

    const elements = {
        status: document.getElementById("runtime-status"),
        elapsed: document.getElementById("runtime-elapsed"),
        participants: document.getElementById("runtime-participants"),
        vehicles: document.getElementById("runtime-vehicles"),
        events: document.getElementById("runtime-events"),
        chatMessages: document.getElementById("chat-messages"),
        chatThread: document.getElementById("chat-thread"),
        chatBody: document.getElementById("chat-body"),
        chatSendButton: document.getElementById("chat-send-btn"),
        dispatcherCanvas: document.getElementById("dispatcher-area-canvas"),
        dispatcherSelectionPill: document.getElementById("dispatcher-selection-pill"),
        dispatcherGuessLabel: document.getElementById("dispatcher-guess-label"),
        dispatcherMarkButton: document.getElementById("dispatcher-mark-btn"),
        dispatchButton: document.getElementById("dispatch-order-btn"),
        dispatchSpawnSelect: document.getElementById("dispatch-spawn-select"),
        dispatchFireCount: document.getElementById("dispatch-count-fire-engine"),
        dispatchLadderCount: document.getElementById("dispatch-count-ladder-engine"),
        instructorAreaCanvas: document.getElementById("instructor-area-canvas"),
        instructorObjectCanvas: document.getElementById("instructor-object-canvas"),
        runtimeEventType: document.getElementById("runtime-event-type"),
        runtimeEventPayload: document.getElementById("runtime-event-payload"),
        runtimeEventButton: document.getElementById("create-runtime-event-btn"),
        rtpCanvas: document.getElementById("rtp-object-canvas"),
        rtpStage: document.getElementById("rtp-object-stage"),
        rtpLockedNote: document.getElementById("rtp-locked-note"),
        rtpPhasePill: document.getElementById("rtp-phase-pill"),
        rtpVehicleSelect: document.getElementById("rtp-vehicle-id"),
        rtpHoseButton: document.getElementById("rtp-hose-btn"),
        rtpNozzleButton: document.getElementById("rtp-nozzle-btn"),
    };

    const state = {
        currentState: null,
        maps: new Map(),
        activeThread: defaultThread,
        manualThreadOverride: false,
        messagesByThread: {},
        pendingDispatchIndex: null,
        selectedVehicleId: elements.rtpVehicleSelect?.value || null,
        previewObjectPath: [],
    };

    function shortId(value) {
        return value ? value.slice(0, 8) : "Система";
    }

    function formatTime(isoValue) {
        const date = new Date(isoValue);
        return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
    }

    function clearCanvas(canvas) {
        if (!canvas) {
            return;
        }
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function renderChat() {
        if (!elements.chatMessages) {
            return;
        }
        const items = state.messagesByThread[state.activeThread] || [];
        elements.chatMessages.innerHTML = "";
        if (!items.length) {
            elements.chatMessages.innerHTML = '<div class="pill subtle">Сообщений пока нет.</div>';
            return;
        }
        items.forEach((item) => {
            const row = document.createElement("div");
            row.className = "pill subtle";
            row.innerHTML = `<strong>${shortId(item.participant_id)}</strong> <span>${formatTime(item.created_at)}</span><div>${item.body}</div>`;
            elements.chatMessages.appendChild(row);
        });
        elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    }

    async function loadThread(threadKey) {
        if (!threadKey) {
            return;
        }
        const response = await fetch(`/api/sessions/${sessionCode}/chat/messages?thread_key=${encodeURIComponent(threadKey)}`, {
            credentials: "same-origin",
        });
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        state.messagesByThread[threadKey] = payload.items;
        if (state.activeThread === threadKey) {
            renderChat();
        }
    }

    async function loadAllVisibleThreads() {
        if (!elements.chatThread) {
            return;
        }
        const threads = [...elements.chatThread.options].map((option) => option.value);
        await Promise.all(threads.map((thread) => loadThread(thread)));
    }

    function orderedLayers(level) {
        return [...(level?.layers || [])].sort((left, right) => left.z_index - right.z_index);
    }

    function levelForCode(mapPayload, levelCode) {
        if (!mapPayload?.levels?.length) {
            return null;
        }
        return mapPayload.levels.find((level) => level.code === levelCode) || mapPayload.levels[0];
    }

    function layerCells(level, layerKey, totalSize) {
        const layer = level?.layers?.find((item) => item.layer_key === layerKey);
        return Array.isArray(layer?.cells) ? layer.cells : new Array(totalSize).fill(0);
    }

    function tileColor(mapPayload, layerKey, code) {
        const entry = (mapPayload?.palette_manifest?.[layerKey] || []).find((item) => item.code === code);
        return entry ? entry.color : "transparent";
    }

    function travelCellBlocked(objectsCode, buildingsCode, groundCode) {
        if (buildingsCode > 0) {
            return true;
        }
        if ([1, 2, 4].includes(objectsCode)) {
            return true;
        }
        return groundCode === 7;
    }

    function fallbackSpawnIndex(areaMap) {
        const level = levelForCode(areaMap, "AREA_MAIN");
        const totalSize = areaMap.width * areaMap.height;
        const ground = layerCells(level, "ground", totalSize);
        const objects = layerCells(level, "objects", totalSize);
        const buildings = layerCells(level, "buildings", totalSize);
        for (let index = totalSize - 1; index >= 0; index -= 1) {
            if (!travelCellBlocked(objects[index], buildings[index], ground[index]) && ground[index] === 3) {
                return index;
            }
        }
        for (let index = totalSize - 1; index >= 0; index -= 1) {
            if (!travelCellBlocked(objects[index], buildings[index], ground[index])) {
                return index;
            }
        }
        return Math.max(0, totalSize - 1);
    }

    function spawnChoices(areaMap) {
        const level = levelForCode(areaMap, "AREA_MAIN");
        const totalSize = areaMap.width * areaMap.height;
        const objects = layerCells(level, "objects", totalSize);
        const configured = [];
        objects.forEach((code, index) => {
            if (code === 7) {
                configured.push(index);
            }
        });
        if (configured.length) {
            return configured.map((index, position) => ({
                index,
                label: `Выезд ${position + 1} [${index % areaMap.width}, ${Math.floor(index / areaMap.width)}]`,
            }));
        }
        const fallback = fallbackSpawnIndex(areaMap);
        return [{
            index: fallback,
            label: `Запасн. [${fallback % areaMap.width}, ${Math.floor(fallback / areaMap.width)}]`,
        }];
    }

    function drawPolyline(ctx, points, cellSize, color) {
        if (!points || points.length < 2) {
            return;
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = Math.max(2, Math.floor(cellSize * 0.16));
        ctx.beginPath();
        ctx.moveTo(points[0].x * cellSize + cellSize / 2, points[0].y * cellSize + cellSize / 2);
        points.slice(1).forEach((point) => {
            ctx.lineTo(point.x * cellSize + cellSize / 2, point.y * cellSize + cellSize / 2);
        });
        ctx.stroke();
    }

    function currentObjectLevelCode(mapPayload) {
        if (!mapPayload?.levels?.length) {
            return null;
        }
        const activeId = state.selectedVehicleId || state.currentState?.scenario?.active_object_vehicle_id || mapPayload.runtime_overlay?.active_object_vehicle_id;
        const vehicles = mapPayload.runtime_overlay?.vehicles || [];
        const activeVehicle = vehicles.find((vehicle) => vehicle.id === activeId) || vehicles[0];
        return activeVehicle?.current_level_code || mapPayload.levels[0].code;
    }

    function computeCellSize(canvas, mapPayload) {
        const wrapWidth = Math.max(420, Math.floor(canvas.parentElement?.clientWidth || 960));
        const wrapHeight = 520;
        const byWidth = Math.floor((wrapWidth - 12) / mapPayload.width);
        const byHeight = Math.floor((wrapHeight - 12) / mapPayload.height);
        return Math.max(12, Math.min(36, Math.min(byWidth, byHeight)));
    }

    function drawFireZone(ctx, mapPayload, cellSize, fireZone) {
        if (!fireZone || typeof fireZone.center_index !== "number") {
            return;
        }
        const centerX = (fireZone.center_index % mapPayload.width) * cellSize + cellSize / 2;
        const centerY = Math.floor(fireZone.center_index / mapPayload.width) * cellSize + cellSize / 2;
        const radius = (Number(fireZone.radius || 0) + 0.5) * cellSize;
        // Pulsating fire zone
        const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 600);
        const fillAlpha = 0.10 + 0.18 * pulse;
        const strokeAlpha = 0.55 + 0.4 * pulse;
        ctx.fillStyle = `rgba(220, 38, 38, ${fillAlpha.toFixed(3)})`;
        ctx.strokeStyle = `rgba(248, 113, 113, ${strokeAlpha.toFixed(3)})`;
        ctx.lineWidth = Math.max(2, Math.floor(cellSize * 0.12));
        ctx.setLineDash([cellSize * 0.3, cellSize * 0.15]);
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        // Inner glow ring
        const innerRadius = radius * 0.6;
        ctx.fillStyle = `rgba(255, 80, 20, ${(fillAlpha * 0.6).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(centerX, centerY, innerRadius, 0, Math.PI * 2);
        ctx.fill();
        // Fire icon in center
        ctx.setLineDash([]);
        ctx.font = `${Math.max(14, cellSize)}px serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("🔥", centerX, centerY);
    }

    function drawVehicles(ctx, mapPayload, level, cellSize, selectedVehicleId) {
        const vehicles = (mapPayload.runtime_overlay?.vehicles || []).filter((vehicle) => {
            if (vehicle.current_map_id !== mapPayload.id) {
                return false;
            }
            return !vehicle.current_level_code || vehicle.current_level_code === level.code;
        });
        vehicles.forEach((vehicle) => {
            const x = Math.round(vehicle.position_x);
            const y = Math.round(vehicle.position_y);
            // Draw route path if vehicle has a route
            if (vehicle.route_path && vehicle.route_path.length > 1) {
                ctx.strokeStyle = "rgba(34, 197, 94, 0.5)";
                ctx.lineWidth = Math.max(1, Math.floor(cellSize * 0.1));
                ctx.setLineDash([cellSize * 0.2, cellSize * 0.1]);
                ctx.beginPath();
                ctx.moveTo(vehicle.route_path[0].x * cellSize + cellSize / 2, vehicle.route_path[0].y * cellSize + cellSize / 2);
                vehicle.route_path.slice(1).forEach((pt) => {
                    ctx.lineTo(pt.x * cellSize + cellSize / 2, pt.y * cellSize + cellSize / 2);
                });
                ctx.stroke();
                ctx.setLineDash([]);
            }
            // Vehicle body
            const isSelected = vehicle.id === selectedVehicleId;
            const baseColor = isSelected ? "rgba(132, 204, 22, 0.95)" : "rgba(251, 146, 60, 0.92)";
            const glowColor = isSelected ? "rgba(132, 204, 22, 0.3)" : "rgba(251, 146, 60, 0.2)";
            // Glow
            ctx.fillStyle = glowColor;
            ctx.fillRect(x * cellSize, y * cellSize, cellSize, cellSize);
            // Main rect
            ctx.fillStyle = baseColor;
            ctx.fillRect(x * cellSize + cellSize * 0.14, y * cellSize + cellSize * 0.14, cellSize * 0.72, cellSize * 0.72);
            ctx.strokeStyle = "rgba(15, 23, 42, 0.95)";
            ctx.lineWidth = Math.max(1, Math.floor(cellSize * 0.08));
            ctx.strokeRect(x * cellSize + cellSize * 0.14, y * cellSize + cellSize * 0.14, cellSize * 0.72, cellSize * 0.72);
            // Vehicle emoji icon
            ctx.font = `${Math.max(10, Math.floor(cellSize * 0.5))}px serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText("🚒", x * cellSize + cellSize / 2, y * cellSize + cellSize / 2);
        });
    }

    function drawObjectTacticalOverlays(ctx, mapPayload, level, cellSize) {
        drawPolyline(ctx, state.previewObjectPath, cellSize, "rgba(34, 197, 94, 0.85)");
        (mapPayload.runtime_overlay?.hoses || []).forEach((hose) => drawPolyline(ctx, hose.polyline_points || [], cellSize, "rgba(14, 165, 233, 0.85)"));
        (mapPayload.runtime_overlay?.nozzles || []).forEach((nozzle) => {
            if (typeof nozzle.target_x !== "number" || typeof nozzle.target_y !== "number") {
                return;
            }
            const centerX = nozzle.target_x * cellSize + cellSize / 2;
            const centerY = nozzle.target_y * cellSize + cellSize / 2;
            ctx.strokeStyle = "rgba(6, 182, 212, 0.9)";
            ctx.lineWidth = Math.max(2, Math.floor(cellSize * 0.14));
            ctx.beginPath();
            ctx.arc(centerX, centerY, cellSize * 1.4, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = "rgba(6, 182, 212, 0.9)";
            ctx.beginPath();
            ctx.arc(centerX, centerY, Math.max(3, Math.floor(cellSize * 0.16)), 0, Math.PI * 2);
            ctx.fill();
        });
        drawVehicles(ctx, mapPayload, level, cellSize, state.selectedVehicleId);
    }

    function drawDispatcherTargets(ctx, mapPayload, cellSize) {
        const confirmedIndex = state.currentState?.scenario?.dispatcher_guess_index;
        if (typeof confirmedIndex === "number") {
            const x = confirmedIndex % mapPayload.width;
            const y = Math.floor(confirmedIndex / mapPayload.width);
            // Crosshair confirmed target
            const cx = x * cellSize + cellSize / 2;
            const cy = y * cellSize + cellSize / 2;
            const arm = cellSize * 0.6;
            ctx.strokeStyle = "rgba(244, 63, 94, 0.95)";
            ctx.lineWidth = Math.max(2, Math.floor(cellSize * 0.1));
            ctx.beginPath();
            ctx.moveTo(cx - arm, cy); ctx.lineTo(cx + arm, cy);
            ctx.moveTo(cx, cy - arm); ctx.lineTo(cx, cy + arm);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(cx, cy, cellSize * 0.4, 0, Math.PI * 2);
            ctx.stroke();
            // Label
            ctx.font = `bold ${Math.max(10, Math.floor(cellSize * 0.4))}px Inter, sans-serif`;
            ctx.fillStyle = "rgba(244, 63, 94, 0.95)";
            ctx.textAlign = "left";
            ctx.textBaseline = "top";
            ctx.fillText("📍", cx + arm * 0.5, cy - arm);
        }
        if (typeof state.pendingDispatchIndex === "number") {
            const x = state.pendingDispatchIndex % mapPayload.width;
            const y = Math.floor(state.pendingDispatchIndex / mapPayload.width);
            const cx = x * cellSize + cellSize / 2;
            const cy = y * cellSize + cellSize / 2;
            // Pending — pulsing blue ring
            const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 400);
            ctx.strokeStyle = `rgba(56, 189, 248, ${(0.5 + 0.5 * pulse).toFixed(3)})`;
            ctx.lineWidth = Math.max(2, Math.floor(cellSize * 0.1));
            ctx.setLineDash([cellSize * 0.15, cellSize * 0.08]);
            ctx.beginPath();
            ctx.arc(cx, cy, cellSize * 0.5, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
        }
        const level = levelForCode(mapPayload, "AREA_MAIN");
        if (level) {
            drawVehicles(ctx, mapPayload, level, cellSize, null);
        }
    }

    function renderMapOnCanvas(canvas, mapPayload, { levelCode = null, fireZone = null, dispatchMode = false, objectMode = false } = {}) {
        if (!canvas || !mapPayload) {
            if (canvas) {
                clearCanvas(canvas);
            }
            return;
        }
        const level = levelForCode(mapPayload, levelCode || (objectMode ? currentObjectLevelCode(mapPayload) : "AREA_MAIN"));
        if (!level) {
            clearCanvas(canvas);
            return;
        }
        const cellSize = computeCellSize(canvas, mapPayload);
        canvas.width = mapPayload.width * cellSize;
        canvas.height = mapPayload.height * cellSize;
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        for (let y = 0; y < mapPayload.height; y += 1) {
            for (let x = 0; x < mapPayload.width; x += 1) {
                const index = y * mapPayload.width + x;
                orderedLayers(level).forEach((layer) => {
                    const value = layer.cells[index];
                    if (!value && layer.layer_key !== "ground" && layer.layer_key !== "floor") {
                        return;
                    }
                    const color = tileColor(mapPayload, layer.layer_key, value);
                    if (!color || color === "transparent") {
                        return;
                    }
                    ctx.fillStyle = color;
                    ctx.fillRect(x * cellSize, y * cellSize, cellSize, cellSize);
                });
                ctx.strokeStyle = "rgba(255,255,255,0.06)";
                ctx.strokeRect(x * cellSize, y * cellSize, cellSize, cellSize);
            }
        }
        if (fireZone) {
            drawFireZone(ctx, mapPayload, cellSize, fireZone);
        }
        if (dispatchMode) {
            drawDispatcherTargets(ctx, mapPayload, cellSize);
        }
        if (objectMode) {
            drawObjectTacticalOverlays(ctx, mapPayload, level, cellSize);
            const fireLayer = level.layers.find(l => l.layer_key === "effects_fire");
            if (fireLayer) {
                for (let i = 0; i < fireLayer.cells.length; i++) {
                    if (fireLayer.cells[i] === 2) {
                        const fx = i % mapPayload.width;
                        const fy = Math.floor(i / mapPayload.width);
                        ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
                        ctx.font = `bold ${Math.max(10, cellSize * 0.4)}px sans-serif`;
                        ctx.textAlign = "center";
                        ctx.textBaseline = "bottom";
                        ctx.shadowColor = "rgba(0, 0, 0, 0.8)";
                        ctx.shadowBlur = 4;
                        ctx.fillText("~1000 °C", fx * cellSize + cellSize / 2, fy * cellSize - 4);
                        ctx.shadowBlur = 0;
                    }
                }
            }
        }
    }

    function cellFromCanvasEvent(canvas, mapPayload, event) {
        if (!canvas || !mapPayload) {
            return null;
        }
        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height) {
            return null;
        }
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const cellSizeX = canvas.width / mapPayload.width;
        const cellSizeY = canvas.height / mapPayload.height;
        const localX = (event.clientX - rect.left) * scaleX;
        const localY = (event.clientY - rect.top) * scaleY;
        const x = Math.floor(localX / cellSizeX);
        const y = Math.floor(localY / cellSizeY);
        if (x < 0 || y < 0 || x >= mapPayload.width || y >= mapPayload.height) {
            return null;
        }
        return { x, y, index: y * mapPayload.width + x };
    }

    async function loadMap(mapId, { force = false } = {}) {
        if (!mapId) {
            return null;
        }
        if (!force && state.maps.has(mapId)) {
            return state.maps.get(mapId);
        }
        const response = await fetch(`/api/maps/${mapId}`, { credentials: "same-origin" });
        if (!response.ok) {
            state.maps.delete(mapId);
            return null;
        }
        const payload = await response.json();
        payload.levels.forEach((level) => {
            level.layers.forEach((layer) => {
                layer.cells = Array.from(layer.cells);
            });
        });
        state.maps.set(mapId, payload);
        return payload;
    }

    function updateParticipants(payload) {
        if (!elements.participants) {
            return;
        }
        elements.participants.innerHTML = payload.participants
            .map(
                (participant) =>
                    `<div class="row spread align-center"><span>${participant.display_name || participant.id.slice(0, 8)}</span><span class="pill subtle">${participant.role}</span></div>`,
            )
            .join("");
    }

    function updateVehicles(payload) {
        if (!elements.vehicles) {
            return;
        }
        elements.vehicles.innerHTML = payload.vehicles
            .map(
                (vehicle) =>
                    `<div class="row spread align-center"><span>${vehicle.display_name}</span><span class="pill subtle">${vehicle.status}</span></div>`,
            )
            .join("");
    }

    function updateThreadOptions(payload) {
        if (!elements.chatThread) {
            return;
        }
        const allowed = new Set(payload.chat.threads);
        [...elements.chatThread.options].forEach((option) => {
            if (!allowed.has(option.value)) {
                option.remove();
            }
        });
        payload.chat.threads.forEach((thread) => {
            if ([...elements.chatThread.options].some((option) => option.value === thread)) {
                return;
            }
            const option = document.createElement("option");
            option.value = thread;
            option.textContent = thread;
            elements.chatThread.appendChild(option);
        });
        if (!state.manualThreadOverride) {
            state.activeThread = payload.chat.default_thread;
            elements.chatThread.value = state.activeThread;
        }
    }

    function updateDispatcherLabels(areaMap) {
        if (elements.dispatcherGuessLabel) {
            if (typeof state.pendingDispatchIndex === "number" && areaMap) {
                elements.dispatcherGuessLabel.value = `[${state.pendingDispatchIndex % areaMap.width}, ${Math.floor(state.pendingDispatchIndex / areaMap.width)}] #${state.pendingDispatchIndex}`;
            } else if (typeof state.currentState?.scenario?.dispatcher_guess_index === "number" && areaMap) {
                const guess = state.currentState.scenario.dispatcher_guess_index;
                elements.dispatcherGuessLabel.value = `[${guess % areaMap.width}, ${Math.floor(guess / areaMap.width)}] #${guess}`;
            } else {
                elements.dispatcherGuessLabel.value = "Не выбрано";
            }
        }
        if (elements.dispatcherSelectionPill) {
            if (typeof state.pendingDispatchIndex === "number") {
                elements.dispatcherSelectionPill.textContent = "Точка выбрана (ожидание)";
            } else if (typeof state.currentState?.scenario?.dispatcher_guess_index === "number") {
                elements.dispatcherSelectionPill.textContent = "Точка подтверждена на карте";
            } else {
                elements.dispatcherSelectionPill.textContent = "Нажмите на карту для выбора точки происшествия.";
            }
        }
    }

    function updateRtpVisibility() {
        if (!elements.rtpPhasePill || !elements.rtpStage || !elements.rtpLockedNote) {
            return;
        }
        const unlocked = Boolean(state.currentState?.scenario?.incident_revealed);
        elements.rtpPhasePill.textContent = unlocked ? "Объектная фаза активна" : "Ожидание прибытия на место";
        elements.rtpStage.classList.toggle("hidden", !unlocked);
        elements.rtpLockedNote.classList.toggle("hidden", unlocked);
    }

    function syncSpawnOptions(areaMap) {
        if (!elements.dispatchSpawnSelect || !areaMap) {
            return;
        }
        const options = spawnChoices(areaMap);
        const previous = Number(elements.dispatchSpawnSelect.value);
        elements.dispatchSpawnSelect.innerHTML = options
            .map((option) => `<option value="${option.index}">${option.label}</option>`)
            .join("");
        const validPrevious = options.some((option) => option.index === previous);
        elements.dispatchSpawnSelect.value = String(validPrevious ? previous : options[0].index);
    }

    function syncRtpVehicleOptions(objectMap) {
        if (!elements.rtpVehicleSelect || !objectMap) {
            return;
        }
        const vehicles = (objectMap.runtime_overlay?.vehicles || []).filter((vehicle) => vehicle.current_map_id === objectMap.id);
        const fallbackId = state.currentState?.scenario?.active_object_vehicle_id || objectMap.runtime_overlay?.active_object_vehicle_id;
        const selected = state.selectedVehicleId || fallbackId || vehicles[0]?.id || "";
        elements.rtpVehicleSelect.innerHTML = vehicles
            .map((vehicle) => `<option value="${vehicle.id}">${vehicle.display_name}</option>`)
            .join("");
        state.selectedVehicleId = vehicles.some((vehicle) => vehicle.id === selected) ? selected : vehicles[0]?.id || null;
        if (state.selectedVehicleId) {
            elements.rtpVehicleSelect.value = state.selectedVehicleId;
        }
    }

    async function renderDispatcherMap(force) {
        if (!elements.dispatcherCanvas) {
            return;
        }
        const areaMapId = state.currentState?.runtime_maps?.area?.[0]?.id;
        const areaMap = await loadMap(areaMapId, { force });
        if (!areaMap) {
            clearCanvas(elements.dispatcherCanvas);
            return;
        }
        syncSpawnOptions(areaMap);
        updateDispatcherLabels(areaMap);
        renderMapOnCanvas(elements.dispatcherCanvas, areaMap, { dispatchMode: true });
    }

    async function renderInstructorMaps(force) {
        const areaMapId = state.currentState?.runtime_maps?.area?.[0]?.id;
        const objectMapId = state.currentState?.runtime_maps?.object?.[0]?.id;
        const areaMap = await loadMap(areaMapId, { force });
        if (elements.instructorAreaCanvas) {
            renderMapOnCanvas(elements.instructorAreaCanvas, areaMap, {
                fireZone: state.currentState?.scenario?.area_fire_zone,
                dispatchMode: true,
            });
        }
        const objectMap = await loadMap(objectMapId, { force });
        if (elements.instructorObjectCanvas) {
            renderMapOnCanvas(elements.instructorObjectCanvas, objectMap, { objectMode: true });
        }
    }

    async function renderRtpMap(force) {
        updateRtpVisibility();
        if (!elements.rtpCanvas || !state.currentState?.scenario?.incident_revealed) {
            if (elements.rtpCanvas && !state.currentState?.scenario?.incident_revealed) {
                clearCanvas(elements.rtpCanvas);
            }
            return;
        }
        const objectMapId = state.currentState?.runtime_maps?.object?.[0]?.id;
        const objectMap = await loadMap(objectMapId, { force });
        if (!objectMap) {
            clearCanvas(elements.rtpCanvas);
            return;
        }
        syncRtpVehicleOptions(objectMap);
        renderMapOnCanvas(elements.rtpCanvas, objectMap, { objectMode: true });
    }

    async function renderAllMaps(force = false) {
        await Promise.all([
            renderDispatcherMap(force),
            renderInstructorMaps(force),
            renderRtpMap(force),
        ]);
    }

    // Animation loop for pulsating effects (fire zone, pending dispatch target)
    let lastAnimFrame = 0;
    function animationLoop(timestamp) {
        if (timestamp - lastAnimFrame > 66) { // ~15fps for smooth pulse without overhead
            lastAnimFrame = timestamp;
            const hasFireZone = state.currentState?.scenario?.area_fire_zone;
            const hasPending = typeof state.pendingDispatchIndex === "number";
            if (hasFireZone && elements.instructorAreaCanvas) {
                renderInstructorMaps(false).catch(() => { });
            }
            if ((hasPending || hasFireZone) && elements.dispatcherCanvas) {
                renderDispatcherMap(false).catch(() => { });
            }
        }
        requestAnimationFrame(animationLoop);
    }
    requestAnimationFrame(animationLoop);

    function appendEventBadge(eventType) {
        if (!elements.events) {
            return;
        }
        const row = document.createElement("div");
        row.className = "pill subtle";
        row.textContent = eventType || "event";
        const badges = [...elements.events.querySelectorAll(".pill.subtle")];
        if (badges.length === 1 && badges[0].textContent === "Событий пока нет.") {
            elements.events.innerHTML = "";
        }
        elements.events.prepend(row);
    }

    async function refreshState({ forceMaps = false } = {}) {
        const response = await fetch(`/api/sessions/${sessionCode}/state`, { credentials: "same-origin" });
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        state.currentState = payload;
        if (elements.status) {
            elements.status.textContent = payload.status;
        }
        if (elements.elapsed) {
            elements.elapsed.textContent = `${payload.time_elapsed_minutes} мин`;
        }
        updateParticipants(payload);
        updateVehicles(payload);
        updateThreadOptions(payload);
        await loadThread(state.activeThread);
        renderChat();
        updateRtpVisibility();
        await renderAllMaps(forceMaps);
    }

    async function confirmDispatcherPoint() {
        if (typeof state.pendingDispatchIndex !== "number") {
            window.alert("Сначала выберите точку на карте.");
            return;
        }
        await postJson(`/api/sessions/${sessionCode}/dispatcher/mark-incident`, csrfToken, {
            guess_index: state.pendingDispatchIndex,
        });
        state.pendingDispatchIndex = null;
        await refreshState({ forceMaps: true });
    }

    async function sendDispatchOrder() {
        const spawnIndex = elements.dispatchSpawnSelect?.value ? Number(elements.dispatchSpawnSelect.value) : null;
        await postJson(`/api/sessions/${sessionCode}/dispatch/orders`, csrfToken, {
            counts: {
                FIRE_ENGINE: Number(elements.dispatchFireCount?.value || 0),
                LADDER_ENGINE: Number(elements.dispatchLadderCount?.value || 0),
            },
            spawn_index: Number.isFinite(spawnIndex) ? spawnIndex : null,
        });
        await refreshState({ forceMaps: true });
    }

    async function submitRtpRoute(cell) {
        const objectMapId = state.currentState?.runtime_maps?.object?.[0]?.id;
        const objectMap = state.maps.get(objectMapId);
        if (!state.selectedVehicleId || !objectMap) {
            return;
        }
        const result = await postJson(
            `/api/sessions/${sessionCode}/vehicles/${state.selectedVehicleId}/object-route`,
            csrfToken,
            { points: [{ x: cell.x, y: cell.y }] },
        );
        state.previewObjectPath = (result.applied_path || []).map((point) => ({ x: point.x, y: point.y }));
        renderMapOnCanvas(elements.rtpCanvas, objectMap, { objectMode: true });
    }

    async function submitRtpStep(direction) {
        if (!state.selectedVehicleId) {
            return;
        }
        await postJson(
            `/api/sessions/${sessionCode}/vehicles/${state.selectedVehicleId}/object-drive`,
            csrfToken,
            { direction },
        );
        state.previewObjectPath = [];
        await refreshState({ forceMaps: true });
    }

    async function deployHose() {
        if (!state.selectedVehicleId) {
            window.alert("Сначала выберите машину на карте объекта.");
            return;
        }
        const objectMapId = state.currentState?.runtime_maps?.object?.[0]?.id;
        const objectMap = state.maps.get(objectMapId);
        const activeVehicle = (objectMap?.runtime_overlay?.vehicles || []).find((vehicle) => vehicle.id === state.selectedVehicleId);
        if (!activeVehicle) {
            window.alert("Выбранная машина ещё не на карте объекта.");
            return;
        }
        let polylinePoints = state.previewObjectPath;
        if (!polylinePoints.length) {
            const startX = Math.round(activeVehicle.position_x);
            const startY = Math.round(activeVehicle.position_y);
            polylinePoints = [
                { x: startX, y: startY },
                { x: Math.min((objectMap?.width || startX + 1) - 1, startX + 1), y: startY },
            ];
        }
        await postJson(`/api/sessions/${sessionCode}/hoses`, csrfToken, {
            source_vehicle_id: state.selectedVehicleId,
            polyline_points: polylinePoints,
        });
        await refreshState({ forceMaps: true });
    }

    async function activateNozzle() {
        const objectMapId = state.currentState?.runtime_maps?.object?.[0]?.id;
        const objectMap = state.maps.get(objectMapId);
        const hose = (objectMap?.runtime_overlay?.hoses || []).find((item) => item.source_vehicle_id === state.selectedVehicleId);
        if (!hose) {
            window.alert("Сначала проложите рукав.");
            return;
        }
        const target = state.previewObjectPath.at(-1) || hose.polyline_points?.at(-1);
        if (!target) {
            window.alert("Сначала выберите целевую клетку.");
            return;
        }
        await postJson(`/api/sessions/${sessionCode}/nozzles`, csrfToken, {
            hose_id: hose.id,
            target_x: target.x,
            target_y: target.y,
            flow_lps: 5.0,
        });
        await refreshState({ forceMaps: true });
    }

    async function createRuntimeEvent() {
        const eventType = elements.runtimeEventType?.value;
        const rawPayload = elements.runtimeEventPayload?.value || "{}";
        await postJson(`/api/sessions/${sessionCode}/events`, csrfToken, {
            event_type: eventType,
            payload: JSON.parse(rawPayload),
        });
    }

    async function sendChatMessage() {
        const body = elements.chatBody?.value.trim();
        if (!body) {
            return;
        }
        await postJson(`/api/sessions/${sessionCode}/chat/messages`, csrfToken, {
            thread_key: state.activeThread,
            body,
        });
        elements.chatBody.value = "";
    }

    if (elements.dispatcherCanvas) {
        elements.dispatcherCanvas.addEventListener("pointerdown", (event) => {
            const areaMapId = state.currentState?.runtime_maps?.area?.[0]?.id;
            const areaMap = state.maps.get(areaMapId);
            const cell = cellFromCanvasEvent(elements.dispatcherCanvas, areaMap, event);
            if (!cell) {
                return;
            }
            state.pendingDispatchIndex = cell.index;
            updateDispatcherLabels(areaMap);
            renderDispatcherMap(false).catch(() => { });
        });
    }

    elements.dispatcherMarkButton?.addEventListener("click", () => {
        confirmDispatcherPoint().catch((error) => window.alert(error.message));
    });

    elements.dispatchButton?.addEventListener("click", () => {
        sendDispatchOrder().catch((error) => window.alert(error.message));
    });

    elements.rtpVehicleSelect?.addEventListener("change", () => {
        state.selectedVehicleId = elements.rtpVehicleSelect.value || null;
        state.previewObjectPath = [];
        renderRtpMap(false).catch(() => { });
    });

    elements.rtpCanvas?.addEventListener("pointerdown", (event) => {
        if (!state.currentState?.scenario?.incident_revealed) {
            return;
        }
        const objectMapId = state.currentState?.runtime_maps?.object?.[0]?.id;
        const objectMap = state.maps.get(objectMapId);
        const cell = cellFromCanvasEvent(elements.rtpCanvas, objectMap, event);
        if (!cell || !objectMap) {
            return;
        }
        const levelCode = currentObjectLevelCode(objectMap);
        const vehicle = (objectMap.runtime_overlay?.vehicles || []).find((item) => {
            if (item.current_map_id !== objectMap.id) {
                return false;
            }
            if (item.current_level_code && item.current_level_code !== levelCode) {
                return false;
            }
            return Math.round(item.position_x) === cell.x && Math.round(item.position_y) === cell.y;
        });
        if (vehicle) {
            state.selectedVehicleId = vehicle.id;
            state.previewObjectPath = [];
            if (elements.rtpVehicleSelect) {
                elements.rtpVehicleSelect.value = vehicle.id;
            }
            renderRtpMap(false).catch(() => { });
            return;
        }
        if (state.selectedVehicleId) {
            submitRtpRoute(cell).catch((error) => window.alert(error.message));
        }
    });

    elements.rtpHoseButton?.addEventListener("click", () => {
        deployHose().catch((error) => window.alert(error.message));
    });

    elements.rtpNozzleButton?.addEventListener("click", () => {
        activateNozzle().catch((error) => window.alert(error.message));
    });

    elements.runtimeEventButton?.addEventListener("click", () => {
        createRuntimeEvent().catch((error) => window.alert(error.message));
    });

    if (elements.chatThread && elements.chatBody && elements.chatSendButton) {
        state.activeThread = elements.chatThread.value || defaultThread;
        elements.chatThread.addEventListener("change", () => {
            state.manualThreadOverride = true;
            state.activeThread = elements.chatThread.value;
            renderChat();
            loadThread(state.activeThread).catch(() => { });
        });
        elements.chatSendButton.addEventListener("click", () => {
            sendChatMessage().catch((error) => window.alert(error.message));
        });
        elements.chatBody.addEventListener("keypress", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                sendChatMessage().catch((error) => window.alert(error.message));
            }
        });
    }

    document.addEventListener("keydown", (event) => {
        if (!state.currentState?.scenario?.incident_revealed || role !== "rtp") {
            return;
        }
        if (event.key === "h" || event.key === "H") {
            event.preventDefault();
            deployHose().catch((error) => window.alert(error.message));
            return;
        }
        if (event.key === "n" || event.key === "N") {
            event.preventDefault();
            activateNozzle().catch((error) => window.alert(error.message));
            return;
        }
        const direction = {
            ArrowUp: "up",
            ArrowDown: "down",
            ArrowLeft: "left",
            ArrowRight: "right",
        }[event.key];
        if (!direction) {
            return;
        }
        event.preventDefault();
        submitRtpStep(direction).catch((error) => window.alert(error.message));
    });

    window.addEventListener("resize", () => {
        renderAllMaps(false).catch(() => { });
    });

    connectSessionSocket(sessionCode, csrfToken, (message) => {
        if (message.type === "scenario_tick") {
            if (typeof message.time_elapsed_minutes === "number" && elements.elapsed) {
                elements.elapsed.textContent = `${message.time_elapsed_minutes} мин`;
            }
            if (typeof message.status === "string" && elements.status) {
                elements.status.textContent = message.status;
            }
            return;
        }
        if (message.type === "event_created") {
            appendEventBadge(message.event_type);
            return;
        }
        if (message.type === "chat_message_created") {
            const thread = message.message.thread_key;
            if (!state.messagesByThread[thread]) {
                state.messagesByThread[thread] = [];
            }
            state.messagesByThread[thread].push(message.message);
            if (thread === state.activeThread) {
                renderChat();
            }
            return;
        }
        if (message.type === "object_phase_unlocked" && role === "dispatcher" && !state.manualThreadOverride && elements.chatThread) {
            if ([...elements.chatThread.options].some((option) => option.value === "dispatcher_rtp")) {
                state.activeThread = "dispatcher_rtp";
                elements.chatThread.value = state.activeThread;
                renderChat();
            }
        }
        if ([
            "session_phase_changed",
            "participant_role_updated",
            "vehicle_path_updated",
            "vehicle_arrived",
            "object_phase_unlocked",
            "map_patch_applied",
            "hose_state_changed",
            "fire_state_changed",
            "system_notice",
        ].includes(message.type)) {
            refreshState({ forceMaps: true }).catch(() => { });
        }
    });

    refreshState({ forceMaps: true }).catch(() => { });
    loadAllVisibleThreads().catch(() => { });
}
