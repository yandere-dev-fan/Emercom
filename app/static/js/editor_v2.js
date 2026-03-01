import { connectSessionSocket, postJson } from "./map_editor_core.js";
import { createObjectTacticalOverlay } from "./object_tactical_overlay.js";

const root = document.getElementById("editor-root");

if (!root) {
    throw new Error("Editor root element was not found.");
}

const state = {
    mapId: root.dataset.mapId,
    mapKind: root.dataset.mapKind,
    sessionCode: root.dataset.sessionCode || "",
    role: root.dataset.role,
    csrfToken: root.dataset.csrfToken,
    mode: root.dataset.mode || "session",
    apiMapBase: root.dataset.apiMapBase || `/api/maps/${root.dataset.mapId}`,
    exportUrl: root.dataset.exportUrl || `/api/maps/${root.dataset.mapId}/export`,
    importUrl: root.dataset.importUrl || "/api/maps/import",
    editorUrlPattern: root.dataset.editorUrlPattern || "/maps/{id}",
    wsEnabled: root.dataset.wsEnabled === "true",
    canEdit: root.dataset.canEdit === "true" || ["admin", "instructor", "host_admin"].includes(root.dataset.role),
    canCreateObjectMap: root.dataset.canCreateObjectMap === "true",
    map: null,
    runtimeOverlay: null,
    activeTool: "pencil",
    activeLevelId: null,
    activeLayerKey: "ground",
    selectedTileValue: 1,
    selectedCell: null,
    zoom: 1,
    showGrid: true,
    pointerDown: false,
    dragStart: null,
    pending: null,
    undoStack: [],
    redoStack: [],
    ws: null,
    selectedVehicleId: null,
    previewPath: [],
};

const elements = {
    canvas: document.getElementById("map-canvas"),
    miniCanvas: document.getElementById("mini-map"),
    paletteList: document.getElementById("palette-list"),
    layerList: document.getElementById("layer-list"),
    levelList: document.getElementById("level-list"),
    selectedTileLabel: document.getElementById("selected-tile-label"),
    statusLine: document.getElementById("status-line"),
    version: document.getElementById("map-version"),
    snapshotList: document.getElementById("snapshot-list"),
    brushSize: document.getElementById("brush-size"),
    importJson: document.getElementById("import-json"),
    exportBtn: document.getElementById("export-btn"),
    selectedBuilding: document.getElementById("selected-building"),
    createObjectMapBtn: document.getElementById("create-object-map-btn"),
    snapshotBtn: document.getElementById("snapshot-btn"),
};

const canvasCtx = elements.canvas.getContext("2d");
const miniCtx = elements.miniCanvas.getContext("2d");

const overlay = createObjectTacticalOverlay({
    getRuntimeOverlay: () => state.runtimeOverlay,
    getCellSize: () => currentCellSize(),
    getActiveLevelCode: () => activeLevel()?.code || null,
});

const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => Array.from(document.querySelectorAll(selector));

function setStatus(message) {
    if (elements.statusLine) {
        elements.statusLine.textContent = message;
    }
}

function activeLevel() {
    return state.map?.levels.find((level) => level.id === state.activeLevelId) || null;
}

function activeLayer() {
    const level = activeLevel();
    if (!level) {
        return null;
    }
    return level.layers.find((layer) => layer.layer_key === state.activeLayerKey) || null;
}

function orderedLayers(level = activeLevel()) {
    return level ? [...level.layers].sort((left, right) => left.z_index - right.z_index) : [];
}

function currentCellSize() {
    return Math.max(1, Math.floor((state.map?.cell_size_px || 1) * state.zoom));
}

function isRtpTacticalMode() {
    return state.mode === "session"
        && state.role === "rtp"
        && state.mapKind === "object"
        && Boolean(state.runtimeOverlay?.tactical_permissions?.can_route_vehicle);
}

function ensureArrays() {
    state.map.levels.forEach((level) => {
        level.layers.forEach((layer) => {
            layer.cells = Array.from(layer.cells);
            if (layer.visible === undefined) {
                layer.visible = true;
            }
            if (layer.locked === undefined) {
                layer.locked = false;
            }
        });
    });
}

function tileDefinitions(layerKey) {
    return state.map?.palette_manifest?.[layerKey] || [];
}

function layerLabel(layerKey) {
    const labels = {
        ground: "Ground",
        objects: "Objects",
        buildings: "Buildings",
        floor: "Floor",
        walls: "Walls",
        openings: "Openings",
        interior: "Interior",
        effects_fire: "Fire",
        effects_smoke: "Smoke",
        markers: "Markers",
    };
    return labels[layerKey] || layerKey;
}

function tileColor(layerKey, code) {
    const item = tileDefinitions(layerKey).find((entry) => entry.code === code);
    return item ? item.color : "transparent";
}

function tileLabel(layerKey, code) {
    const item = tileDefinitions(layerKey).find((entry) => entry.code === code);
    return item ? item.label : `Code ${code}`;
}

function updateVersion(version) {
    state.map.version = version;
    if (elements.version) {
        elements.version.textContent = String(version);
    }
}

function updateSelectedTileLabel() {
    if (!elements.selectedTileLabel) {
        return;
    }
    const current = tileDefinitions(state.activeLayerKey).find((tile) => tile.code === state.selectedTileValue);
    elements.selectedTileLabel.textContent = current ? `${current.label} (${current.code})` : "None";
}

function refreshActiveLayer() {
    const level = activeLevel();
    if (!level) {
        state.activeLayerKey = "ground";
        return;
    }
    if (!level.layers.some((layer) => layer.layer_key === state.activeLayerKey)) {
        state.activeLayerKey = orderedLayers(level)[0]?.layer_key || "ground";
    }
}

function renderLevelList() {
    if (!elements.levelList || !state.map) {
        return;
    }
    elements.levelList.innerHTML = "";
    state.map.levels.forEach((level) => {
        const row = document.createElement("label");
        row.className = "level-row";
        row.innerHTML = `<input type="radio" name="level" ${level.id === state.activeLevelId ? "checked" : ""}><span>${level.title}</span>`;
        row.querySelector("input").addEventListener("change", () => {
            state.activeLevelId = level.id;
            state.selectedCell = null;
            refreshActiveLayer();
            renderLayerList();
            renderPalette();
            renderCanvas();
        });
        elements.levelList.appendChild(row);
    });
}

function renderLayerList() {
    if (!elements.layerList) {
        return;
    }
    elements.layerList.innerHTML = "";
    orderedLayers().forEach((layer) => {
        const row = document.createElement("div");
        row.className = "layer-row";
        row.innerHTML = `
            <input type="radio" name="layer" ${layer.layer_key === state.activeLayerKey ? "checked" : ""}>
            <span>${layerLabel(layer.layer_key)}</span>
            <input type="checkbox" class="small-toggle visibility" ${layer.visible ? "checked" : ""} title="Visible">
            <input type="checkbox" class="small-toggle locked" ${layer.locked ? "checked" : ""} title="Locked">
        `;
        row.querySelector('input[type="radio"]').addEventListener("change", () => {
            state.activeLayerKey = layer.layer_key;
            state.selectedTileValue = 1;
            renderPalette();
        });
        row.querySelector(".visibility").addEventListener("change", (event) => {
            layer.visible = event.target.checked;
            renderCanvas();
        });
        row.querySelector(".locked").addEventListener("change", (event) => {
            layer.locked = event.target.checked;
        });
        elements.layerList.appendChild(row);
    });
}

function renderPalette() {
    if (!elements.paletteList) {
        return;
    }
    elements.paletteList.innerHTML = "";
    tileDefinitions(state.activeLayerKey).forEach((tile) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "palette-row";
        button.innerHTML = `<span class="swatch" style="background:${tile.color}"></span><span>${tile.label}</span>`;
        button.addEventListener("click", () => {
            state.selectedTileValue = tile.code;
            updateSelectedTileLabel();
        });
        elements.paletteList.appendChild(button);
    });
    updateSelectedTileLabel();
}

function resizeCanvas() {
    const size = currentCellSize();
    elements.canvas.width = state.map.width * size;
    elements.canvas.height = state.map.height * size;
}

function renderSelectionOutline(size) {
    if (!state.selectedCell || state.selectedCell.levelId !== state.activeLevelId) {
        return;
    }
    const x = state.selectedCell.index % state.map.width;
    const y = Math.floor(state.selectedCell.index / state.map.width);
    canvasCtx.strokeStyle = "rgba(34, 197, 94, 0.95)";
    canvasCtx.lineWidth = Math.max(2, Math.floor(size * 0.12));
    canvasCtx.strokeRect(x * size + 1, y * size + 1, Math.max(1, size - 2), Math.max(1, size - 2));
}

function renderCanvas() {
    if (!state.map) {
        return;
    }
    resizeCanvas();
    const size = currentCellSize();
    canvasCtx.clearRect(0, 0, elements.canvas.width, elements.canvas.height);
    for (let y = 0; y < state.map.height; y += 1) {
        for (let x = 0; x < state.map.width; x += 1) {
            const index = y * state.map.width + x;
            orderedLayers().filter((layer) => layer.visible).forEach((layer) => {
                const value = layer.cells[index];
                if (!value && layer.layer_key !== "ground") {
                    return;
                }
                const color = tileColor(layer.layer_key, value);
                if (!color || color === "transparent") {
                    return;
                }
                canvasCtx.fillStyle = color;
                canvasCtx.fillRect(x * size, y * size, size, size);
            });
            if (state.showGrid) {
                canvasCtx.strokeStyle = "rgba(255,255,255,0.06)";
                canvasCtx.strokeRect(x * size, y * size, size, size);
            }
        }
    }
    renderSelectionOutline(size);
    overlay.draw(canvasCtx, { selectedVehicleId: state.selectedVehicleId, previewPath: state.previewPath });
    renderMiniMap();
}

function renderMiniMap() {
    if (!state.map) {
        return;
    }
    const scale = Math.min(elements.miniCanvas.width / state.map.width, elements.miniCanvas.height / state.map.height);
    miniCtx.clearRect(0, 0, elements.miniCanvas.width, elements.miniCanvas.height);
    for (let y = 0; y < state.map.height; y += 1) {
        for (let x = 0; x < state.map.width; x += 1) {
            const index = y * state.map.width + x;
            let color = "#0f172a";
            for (const layer of orderedLayers()) {
                if (!layer.visible) {
                    continue;
                }
                const layerColor = tileColor(layer.layer_key, layer.cells[index]);
                if (layerColor && layerColor !== "transparent") {
                    color = layerColor;
                }
            }
            miniCtx.fillStyle = color;
            miniCtx.fillRect(x * scale, y * scale, Math.ceil(scale), Math.ceil(scale));
        }
    }
}

function cellFromEvent(event) {
    const rect = elements.canvas.getBoundingClientRect();
    const size = currentCellSize();
    const x = Math.floor((event.clientX - rect.left) / size);
    const y = Math.floor((event.clientY - rect.top) / size);
    if (x < 0 || y < 0 || x >= state.map.width || y >= state.map.height) {
        return null;
    }
    return { x, y, index: y * state.map.width + x };
}

function setSelectedCell(cell) {
    state.selectedCell = { levelId: state.activeLevelId, index: cell.index };
    renderCanvas();
}

function beginPending() {
    if (!state.canEdit) {
        setStatus("Read-only mode.");
        return false;
    }
    const layer = activeLayer();
    if (!layer || layer.locked) {
        setStatus("Active layer is locked.");
        return false;
    }
    state.pending = { before: new Map(), after: new Map() };
    return true;
}

function writeToPending(index, value) {
    const layer = activeLayer();
    if (!state.pending.before.has(index)) {
        state.pending.before.set(index, layer.cells[index]);
    }
    state.pending.after.set(index, value);
    layer.cells[index] = value;
}

function applyBrush(x, y, value) {
    const brush = Number(elements.brushSize?.value || 1);
    const radius = Math.floor((brush - 1) / 2);
    for (let offsetY = 0; offsetY < brush; offsetY += 1) {
        for (let offsetX = 0; offsetX < brush; offsetX += 1) {
            const cellX = x + offsetX - radius;
            const cellY = y + offsetY - radius;
            if (cellX < 0 || cellY < 0 || cellX >= state.map.width || cellY >= state.map.height) {
                continue;
            }
            writeToPending(cellY * state.map.width + cellX, value);
        }
    }
}

function linePoints(start, end) {
    return overlay.cellLine(start.x, start.y, end.x, end.y);
}

function fillArea(startIndex) {
    const layer = activeLayer();
    const target = layer.cells[startIndex];
    const replacement = state.activeTool === "eraser" ? 0 : state.selectedTileValue;
    if (target === replacement) {
        return;
    }
    const queue = [startIndex];
    const seen = new Set(queue);
    while (queue.length) {
        const index = queue.shift();
        const x = index % state.map.width;
        const y = Math.floor(index / state.map.width);
        if (layer.cells[index] !== target) {
            continue;
        }
        writeToPending(index, replacement);
        const neighbors = [];
        if (x > 0) {
            neighbors.push(index - 1);
        }
        if (x < state.map.width - 1) {
            neighbors.push(index + 1);
        }
        if (y > 0) {
            neighbors.push(index - state.map.width);
        }
        if (y < state.map.height - 1) {
            neighbors.push(index + state.map.width);
        }
        neighbors.forEach((next) => {
            if (!seen.has(next)) {
                seen.add(next);
                queue.push(next);
            }
        });
    }
}

async function submitPending({ recordHistory = true } = {}) {
    const layer = activeLayer();
    const writes = [];
    const before = [];
    state.pending.after.forEach((value, index) => {
        const previous = state.pending.before.get(index);
        if (previous === value) {
            layer.cells[index] = previous;
            return;
        }
        writes.push({ index, value });
        before.push({ index, value: previous });
    });
    if (!writes.length) {
        state.pending = null;
        renderCanvas();
        return;
    }
    try {
        const payload = await postJson(`${state.apiMapBase}/patches`, state.csrfToken, {
            base_version: state.map.version,
            client_event_id: crypto.randomUUID(),
            changes: [{ level_id: state.activeLevelId, layer_key: state.activeLayerKey, writes }],
        });
        updateVersion(payload.version);
        if (recordHistory) {
            state.undoStack.push({ levelId: state.activeLevelId, layerKey: state.activeLayerKey, before, after: writes });
            state.redoStack = [];
        }
        setStatus(`Saved version ${payload.version}.`);
    } catch (error) {
        await fetchMap();
        setStatus(error.message);
    } finally {
        state.pending = null;
        renderCanvas();
    }
}

async function performUndo(entry, direction) {
    state.activeLevelId = entry.levelId;
    state.activeLayerKey = entry.layerKey;
    renderLevelList();
    renderLayerList();
    renderPalette();
    state.pending = { before: new Map(), after: new Map() };
    const writes = direction === "undo" ? entry.before : entry.after;
    writes.forEach((write) => writeToPending(write.index, write.value));
    await submitPending({ recordHistory: false });
    if (direction === "undo") {
        state.redoStack.push(entry);
    } else {
        state.undoStack.push(entry);
    }
}

async function fetchSnapshots() {
    if (!elements.snapshotList) {
        return;
    }
    const response = await fetch(`${state.apiMapBase}/snapshots`, { credentials: "same-origin" });
    if (!response.ok) {
        elements.snapshotList.innerHTML = "";
        return;
    }
    const payload = await response.json();
    elements.snapshotList.innerHTML = "";
    if (!payload.items.length) {
        elements.snapshotList.innerHTML = '<div class="pill subtle">No snapshots yet.</div>';
        return;
    }
    payload.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "pill subtle";
        row.textContent = `${item.label} - v${item.version}`;
        elements.snapshotList.appendChild(row);
    });
}

async function createSnapshot() {
    if (!state.canEdit) {
        setStatus("Read-only mode.");
        return;
    }
    const label = window.prompt("Snapshot label", `snapshot-${Date.now()}`);
    if (!label) {
        return;
    }
    await postJson(`${state.apiMapBase}/snapshots`, state.csrfToken, { label });
    await fetchSnapshots();
    setStatus("Snapshot saved.");
}

async function importJson(file) {
    const text = await file.text();
    const result = await postJson(state.importUrl, state.csrfToken, { payload: JSON.parse(text) });
    window.location.href = state.editorUrlPattern.replace("{id}", result.map_id);
}

async function createObjectMapFromSelection() {
    if (!state.canCreateObjectMap || !state.selectedCell) {
        setStatus("Select a building cell first.");
        return;
    }
    const result = await postJson(`${state.apiMapBase}/object-maps`, state.csrfToken, {
        source_level_id: state.activeLevelId,
        source_index: state.selectedCell.index,
    });
    window.location.href = state.editorUrlPattern.replace("{id}", result.map_id);
}

async function fetchMap() {
    const response = await fetch(state.apiMapBase, { credentials: "same-origin" });
    if (!response.ok) {
        throw new Error("Failed to load map.");
    }
    const previousLevelId = state.activeLevelId;
    const payload = await response.json();
    state.map = payload;
    state.runtimeOverlay = payload.runtime_overlay || null;
    ensureArrays();
    state.activeLevelId = state.map.levels.some((level) => level.id === previousLevelId) ? previousLevelId : state.map.levels[0].id;
    refreshActiveLayer();
    renderLevelList();
    renderLayerList();
    renderPalette();
    renderCanvas();
    await fetchSnapshots();
}

function mergeRuntimeOverlayVehicle(vehiclePatch) {
    if (!state.runtimeOverlay) {
        return;
    }
    const vehicles = state.runtimeOverlay.vehicles || [];
    const vehicle = vehicles.find((item) => item.id === vehiclePatch.id || item.id === vehiclePatch.vehicle_id);
    if (!vehicle) {
        return;
    }
    Object.assign(vehicle, vehiclePatch);
}

async function submitTacticalRoute(cell) {
    if (!isRtpTacticalMode()) {
        return;
    }
    const vehicle = overlay.findVehicle(state.selectedVehicleId);
    if (!vehicle) {
        return;
    }
    const routePoints = [{ x: cell.x, y: cell.y }];
    const result = await postJson(
        `/api/sessions/${state.sessionCode}/vehicles/${vehicle.id}/object-route`,
        state.csrfToken,
        { points: routePoints },
    );
    state.previewPath = (result.applied_path || []).map((point) => ({ x: point.x, y: point.y }));
    renderCanvas();
}

async function submitTacticalStep(direction) {
    if (!isRtpTacticalMode() || !state.selectedVehicleId) {
        return;
    }
    const result = await postJson(
        `/api/sessions/${state.sessionCode}/vehicles/${state.selectedVehicleId}/object-drive`,
        state.csrfToken,
        { direction },
    );
    mergeRuntimeOverlayVehicle({
        id: result.vehicle_id,
        position_x: result.position_x,
        position_y: result.position_y,
        status: result.status,
    });
    state.previewPath = [];
    renderCanvas();
}

async function deployHoseFromSelection() {
    if (!isRtpTacticalMode() || !state.selectedVehicleId) {
        return;
    }
    const vehicle = overlay.findVehicle(state.selectedVehicleId);
    if (!vehicle) {
        return;
    }
    let polylinePoints = state.previewPath;
    if (!polylinePoints.length) {
        const startX = Math.round(vehicle.position_x);
        const startY = Math.round(vehicle.position_y);
        const endX = Math.min(state.map.width - 1, startX + 1);
        polylinePoints = [{ x: startX, y: startY }, { x: endX, y: startY }];
    }
    const result = await postJson(
        `/api/sessions/${state.sessionCode}/hoses`,
        state.csrfToken,
        { source_vehicle_id: state.selectedVehicleId, polyline_points: polylinePoints },
    );
    state.runtimeOverlay.hoses = [...(state.runtimeOverlay.hoses || []), result.hose];
    renderCanvas();
}

async function activateNozzleFromSelection() {
    if (!isRtpTacticalMode() || !state.selectedVehicleId) {
        return;
    }
    const hoses = (state.runtimeOverlay?.hoses || []).filter((hose) => hose.source_vehicle_id === state.selectedVehicleId);
    const hose = hoses.at(-1);
    if (!hose) {
        setStatus("Create a hose first.");
        return;
    }
    const target = state.previewPath.at(-1) || hose.polyline_points.at(-1);
    const result = await postJson(
        `/api/sessions/${state.sessionCode}/nozzles`,
        state.csrfToken,
        { hose_id: hose.id, target_x: target.x, target_y: target.y, flow_lps: 5.0 },
    );
    state.runtimeOverlay.nozzles = [
        ...(state.runtimeOverlay.nozzles || []).filter((item) => item.hose_id !== hose.id),
        result.nozzle,
    ];
    renderCanvas();
}

function connectSocket() {
    if (!state.wsEnabled || !state.sessionCode) {
        return;
    }
    state.ws = connectSessionSocket(state.sessionCode, state.csrfToken, async (payload) => {
        if (payload.type === "map_patch_applied" && payload.map_id === state.mapId) {
            payload.changes.forEach((change) => {
                const level = state.map.levels.find((item) => item.id === change.level_id);
                const layer = level?.layers.find((item) => item.layer_key === change.layer_key);
                if (!layer) {
                    return;
                }
                change.writes.forEach((write) => {
                    layer.cells[write.index] = write.value;
                });
            });
            updateVersion(payload.version);
            renderCanvas();
        } else if (payload.type === "snapshot_created" && payload.map_id === state.mapId) {
            await fetchSnapshots();
        } else if (payload.type === "vehicle_path_updated") {
            (payload.vehicles || []).forEach((vehiclePatch) => mergeRuntimeOverlayVehicle(vehiclePatch));
            if (payload.vehicle_id) {
                mergeRuntimeOverlayVehicle(payload);
            }
            renderCanvas();
        } else if (payload.type === "hose_state_changed") {
            if (state.runtimeOverlay) {
                const hoses = [...(state.runtimeOverlay.hoses || [])];
                const index = hoses.findIndex((item) => item.id === payload.hose.id);
                if (index >= 0) {
                    hoses[index] = payload.hose;
                } else {
                    hoses.push(payload.hose);
                }
                state.runtimeOverlay.hoses = hoses;
                renderCanvas();
            }
        } else if (payload.type === "fire_state_changed") {
            if (state.runtimeOverlay) {
                const nozzles = [...(state.runtimeOverlay.nozzles || []).filter((item) => item.id !== payload.nozzle.id), payload.nozzle];
                state.runtimeOverlay.nozzles = nozzles;
                renderCanvas();
            }
        } else if (payload.type === "object_phase_unlocked") {
            await fetchMap();
        }
    });
}

function bindUi() {
    qsa(".tool-btn").forEach((button) => {
        button.addEventListener("click", () => {
            qsa(".tool-btn").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
            state.activeTool = button.dataset.tool;
        });
    });
    qs("#undo-btn")?.addEventListener("click", async () => {
        const entry = state.undoStack.pop();
        if (entry) {
            await performUndo(entry, "undo");
        }
    });
    qs("#redo-btn")?.addEventListener("click", async () => {
        const entry = state.redoStack.pop();
        if (entry) {
            await performUndo(entry, "redo");
        }
    });
    qs("#zoom-in")?.addEventListener("click", () => {
        state.zoom = Math.min(4, state.zoom + 0.25);
        renderCanvas();
    });
    qs("#zoom-out")?.addEventListener("click", () => {
        state.zoom = Math.max(0.5, state.zoom - 0.25);
        renderCanvas();
    });
    qs("#toggle-grid")?.addEventListener("click", () => {
        state.showGrid = !state.showGrid;
        renderCanvas();
    });
    elements.snapshotBtn?.addEventListener("click", () => createSnapshot().catch((error) => setStatus(error.message)));
    elements.importJson?.addEventListener("change", async (event) => {
        const file = event.target.files[0];
        if (file) {
            try {
                await importJson(file);
            } catch (error) {
                setStatus(error.message);
            }
        }
    });
    elements.createObjectMapBtn?.addEventListener("click", () => createObjectMapFromSelection().catch((error) => setStatus(error.message)));
    document.getElementById("rtp-hose-btn")?.addEventListener("click", () => deployHoseFromSelection().catch((error) => setStatus(error.message)));
    document.getElementById("rtp-nozzle-btn")?.addEventListener("click", () => activateNozzleFromSelection().catch((error) => setStatus(error.message)));
    document.addEventListener("keydown", (event) => {
        if (!isRtpTacticalMode()) {
            return;
        }
        if (event.key === "h" || event.key === "H") {
            event.preventDefault();
            deployHoseFromSelection().catch((error) => setStatus(error.message));
            return;
        }
        if (event.key === "n" || event.key === "N") {
            event.preventDefault();
            activateNozzleFromSelection().catch((error) => setStatus(error.message));
            return;
        }
        const directionByKey = {
            ArrowUp: "up",
            ArrowDown: "down",
            ArrowLeft: "left",
            ArrowRight: "right",
        };
        const direction = directionByKey[event.key];
        if (!direction) {
            return;
        }
        event.preventDefault();
        submitTacticalStep(direction).catch((error) => setStatus(error.message));
    });
}

elements.canvas.addEventListener("pointerdown", async (event) => {
    const cell = cellFromEvent(event);
    if (!cell) {
        return;
    }
    if (isRtpTacticalMode()) {
        const vehicle = overlay.hitTestVehicle(cell.x, cell.y);
        if (vehicle) {
            state.selectedVehicleId = vehicle.id;
            state.previewPath = [];
            renderCanvas();
            return;
        }
        if (state.selectedVehicleId) {
            await submitTacticalRoute(cell);
        }
        return;
    }
    setSelectedCell(cell);
    if (state.activeTool === "picker") {
        state.selectedTileValue = activeLayer()?.cells[cell.index] || 0;
        updateSelectedTileLabel();
        return;
    }
    if (!beginPending()) {
        return;
    }
    state.pointerDown = true;
    state.dragStart = cell;
    if (state.activeTool === "fill") {
        fillArea(cell.index);
        await submitPending();
        state.pointerDown = false;
        return;
    }
    if (state.activeTool === "pencil" || state.activeTool === "eraser") {
        applyBrush(cell.x, cell.y, state.activeTool === "eraser" ? 0 : state.selectedTileValue);
        renderCanvas();
    }
});

elements.canvas.addEventListener("pointermove", (event) => {
    if (!state.pointerDown || !state.pending || (state.activeTool !== "pencil" && state.activeTool !== "eraser")) {
        return;
    }
    const cell = cellFromEvent(event);
    if (!cell) {
        return;
    }
    applyBrush(cell.x, cell.y, state.activeTool === "eraser" ? 0 : state.selectedTileValue);
    renderCanvas();
});

elements.canvas.addEventListener("pointerup", async (event) => {
    if (!state.pointerDown || !state.pending) {
        return;
    }
    const end = cellFromEvent(event) || state.dragStart;
    if (state.activeTool === "line") {
        linePoints(state.dragStart, end).forEach((point) => applyBrush(point.x, point.y, state.selectedTileValue));
    } else if (state.activeTool === "rect") {
        const minX = Math.min(state.dragStart.x, end.x);
        const maxX = Math.max(state.dragStart.x, end.x);
        const minY = Math.min(state.dragStart.y, end.y);
        const maxY = Math.max(state.dragStart.y, end.y);
        for (let y = minY; y <= maxY; y += 1) {
            for (let x = minX; x <= maxX; x += 1) {
                applyBrush(x, y, state.selectedTileValue);
            }
        }
    }
    state.pointerDown = false;
    await submitPending();
});

elements.canvas.addEventListener("pointerleave", async () => {
    if (state.pointerDown && state.pending && (state.activeTool === "pencil" || state.activeTool === "eraser")) {
        state.pointerDown = false;
        await submitPending();
    }
});

async function boot() {
    if (elements.exportBtn) {
        elements.exportBtn.href = state.exportUrl;
    }
    bindUi();
    await fetchMap();
    connectSocket();
    setStatus(state.canEdit ? "Editor ready." : "Viewer ready.");
}

boot().catch((error) => {
    console.error(error);
    setStatus("Failed to load editor.");
});
