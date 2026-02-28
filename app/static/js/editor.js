const root = document.getElementById("editor-root");

if (!root) {
    throw new Error("Editor root not found.");
}

const state = {
    mapId: root.dataset.mapId,
    sessionCode: root.dataset.sessionCode,
    role: root.dataset.role,
    csrfToken: root.dataset.csrfToken,
    map: null,
    activeTool: "pencil",
    activeLevelId: null,
    activeLayerKey: "ground",
    selectedTileValue: 1,
    zoom: 1,
    showGrid: true,
    pointerDown: false,
    dragStart: null,
    pending: null,
    undoStack: [],
    redoStack: [],
    ws: null,
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
};

const canvasCtx = elements.canvas.getContext("2d");
const miniCtx = elements.miniCanvas.getContext("2d");

const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => Array.from(document.querySelectorAll(selector));

function setStatus(message) {
    elements.statusLine.textContent = message;
}

function activeLevel() {
    return state.map.levels.find((level) => level.id === state.activeLevelId);
}

function activeLayer() {
    const level = activeLevel();
    return level.layers.find((layer) => layer.layer_key === state.activeLayerKey);
}

function tileDefinitions(layerKey) {
    return state.map.palette_manifest[layerKey] || [];
}

function tileColor(layerKey, code) {
    const found = tileDefinitions(layerKey).find((item) => item.code === code);
    return found ? found.color : "transparent";
}

function updateVersion(version) {
    state.map.version = version;
    elements.version.textContent = String(version);
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

async function fetchMap() {
    const response = await fetch(`/api/maps/${state.mapId}`, { credentials: "same-origin" });
    if (!response.ok) {
        throw new Error("Failed to load map.");
    }
    state.map = await response.json();
    ensureArrays();
    state.activeLevelId = state.map.levels[0].id;
    state.activeLayerKey = state.map.levels[0].layers[0].layer_key;
    state.selectedTileValue = 1;
    renderLevelList();
    renderLayerList();
    renderPalette();
    renderCanvas();
    await fetchSnapshots();
}

function renderLevelList() {
    elements.levelList.innerHTML = "";
    state.map.levels.forEach((level) => {
        const row = document.createElement("label");
        row.className = "level-row";
        row.innerHTML = `
            <input type="radio" name="level" ${level.id === state.activeLevelId ? "checked" : ""}>
            <span>${level.title}</span>
        `;
        row.querySelector("input").addEventListener("change", () => {
            state.activeLevelId = level.id;
            state.activeLayerKey = activeLevel().layers[0].layer_key;
            renderLayerList();
            renderPalette();
            renderCanvas();
        });
        elements.levelList.appendChild(row);
    });
}

function renderLayerList() {
    elements.layerList.innerHTML = "";
    const level = activeLevel();
    level.layers.forEach((layer) => {
        const row = document.createElement("div");
        row.className = "layer-row";
        row.innerHTML = `
            <input type="radio" name="layer" ${layer.layer_key === state.activeLayerKey ? "checked" : ""}>
            <span>${layer.layer_key}</span>
            <input type="checkbox" class="small-toggle visibility" ${layer.visible ? "checked" : ""} title="Видимость">
            <input type="checkbox" class="small-toggle locked" ${layer.locked ? "checked" : ""} title="Блокировка">
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
    elements.paletteList.innerHTML = "";
    tileDefinitions(state.activeLayerKey).forEach((tile) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "palette-row";
        row.innerHTML = `<span class="swatch" style="background:${tile.color}"></span><span>${tile.label}</span>`;
        row.addEventListener("click", () => {
            state.selectedTileValue = tile.code;
            elements.selectedTileLabel.textContent = `${tile.label} (${tile.code})`;
        });
        elements.paletteList.appendChild(row);
    });
    const current = tileDefinitions(state.activeLayerKey).find((tile) => tile.code === state.selectedTileValue);
    elements.selectedTileLabel.textContent = current ? `${current.label} (${current.code})` : "Ничего";
}

function resizeCanvas() {
    const size = Math.max(1, Math.floor(state.map.cell_size_px * state.zoom));
    elements.canvas.width = state.map.width * size;
    elements.canvas.height = state.map.height * size;
}

function renderCanvas() {
    if (!state.map) {
        return;
    }
    resizeCanvas();
    const size = Math.max(1, Math.floor(state.map.cell_size_px * state.zoom));
    canvasCtx.clearRect(0, 0, elements.canvas.width, elements.canvas.height);
    const level = activeLevel();
    for (let y = 0; y < state.map.height; y += 1) {
        for (let x = 0; x < state.map.width; x += 1) {
            const index = y * state.map.width + x;
            level.layers
                .filter((layer) => layer.visible)
                .forEach((layer) => {
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
                canvasCtx.strokeStyle = "rgba(255,255,255,0.07)";
                canvasCtx.strokeRect(x * size, y * size, size, size);
            }
        }
    }
    renderMiniMap();
}

function renderMiniMap() {
    const scale = Math.min(elements.miniCanvas.width / state.map.width, elements.miniCanvas.height / state.map.height);
    miniCtx.clearRect(0, 0, elements.miniCanvas.width, elements.miniCanvas.height);
    const level = activeLevel();
    for (let y = 0; y < state.map.height; y += 1) {
        for (let x = 0; x < state.map.width; x += 1) {
            const index = y * state.map.width + x;
            let fill = "#101820";
            for (const layer of level.layers) {
                if (!layer.visible) continue;
                const color = tileColor(layer.layer_key, layer.cells[index]);
                if (color && color !== "transparent") {
                    fill = color;
                }
            }
            miniCtx.fillStyle = fill;
            miniCtx.fillRect(x * scale, y * scale, Math.ceil(scale), Math.ceil(scale));
        }
    }
}

function cellFromEvent(event) {
    const rect = elements.canvas.getBoundingClientRect();
    const size = Math.max(1, Math.floor(state.map.cell_size_px * state.zoom));
    const x = Math.floor((event.clientX - rect.left) / size);
    const y = Math.floor((event.clientY - rect.top) / size);
    if (x < 0 || y < 0 || x >= state.map.width || y >= state.map.height) {
        return null;
    }
    return { x, y, index: y * state.map.width + x };
}

function newPending() {
    return { before: new Map(), after: new Map() };
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
    const brush = Number(elements.brushSize.value);
    const radius = Math.floor((brush - 1) / 2);
    for (let offsetY = 0; offsetY < brush; offsetY += 1) {
        for (let offsetX = 0; offsetX < brush; offsetX += 1) {
            const cx = x + offsetX - radius;
            const cy = y + offsetY - radius;
            if (cx < 0 || cy < 0 || cx >= state.map.width || cy >= state.map.height) continue;
            writeToPending(cy * state.map.width + cx, value);
        }
    }
}

function linePoints(start, end) {
    const points = [];
    let x0 = start.x; let y0 = start.y;
    const x1 = end.x; const y1 = end.y;
    const dx = Math.abs(x1 - x0);
    const sx = x0 < x1 ? 1 : -1;
    const dy = -Math.abs(y1 - y0);
    const sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    while (true) {
        points.push({ x: x0, y: y0 });
        if (x0 === x1 && y0 === y1) break;
        const e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return points;
}

function fillArea(startIndex) {
    const layer = activeLayer();
    const target = layer.cells[startIndex];
    const replacement = state.activeTool === "eraser" ? 0 : state.selectedTileValue;
    if (target === replacement) return;
    const queue = [startIndex];
    const seen = new Set(queue);
    while (queue.length) {
        const index = queue.shift();
        const x = index % state.map.width;
        const y = Math.floor(index / state.map.width);
        if (layer.cells[index] !== target) continue;
        writeToPending(index, replacement);
        const neighbors = [];
        if (x > 0) neighbors.push(index - 1);
        if (x < state.map.width - 1) neighbors.push(index + 1);
        if (y > 0) neighbors.push(index - state.map.width);
        if (y < state.map.height - 1) neighbors.push(index + state.map.width);
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
        const prev = state.pending.before.get(index);
        if (prev === value) {
            layer.cells[index] = prev;
            return;
        }
        writes.push({ index, value });
        before.push({ index, value: prev });
    });
    if (!writes.length) {
        state.pending = null;
        renderCanvas();
        return;
    }
    const body = {
        base_version: state.map.version,
        client_event_id: crypto.randomUUID(),
        changes: [{ level_id: state.activeLevelId, layer_key: state.activeLayerKey, writes }],
    };
    try {
        const response = await fetch(`/api/maps/${state.mapId}/patches`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
            credentials: "same-origin",
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            throw new Error("Patch failed");
        }
        const payload = await response.json();
        updateVersion(payload.version);
        if (recordHistory) {
            state.undoStack.push({ levelId: state.activeLevelId, layerKey: state.activeLayerKey, before, after: writes });
            state.redoStack = [];
        }
        setStatus(`Изменения сохранены. Версия ${payload.version}.`);
    } catch (error) {
        await fetchMap();
        setStatus("Конфликт версии или ошибка сохранения. Карта перезагружена.");
    } finally {
        state.pending = null;
        renderCanvas();
    }
}

function beginPending() {
    if (state.role !== "admin") {
        setStatus("Режим просмотра: редактирование заблокировано.");
        return false;
    }
    const layer = activeLayer();
    if (layer.locked) {
        setStatus("Активный слой заблокирован.");
        return false;
    }
    state.pending = newPending();
    return true;
}

async function performUndo(entry, direction) {
    const level = state.map.levels.find((item) => item.id === entry.levelId);
    const layer = level.layers.find((item) => item.layer_key === entry.layerKey);
    state.activeLevelId = entry.levelId;
    state.activeLayerKey = entry.layerKey;
    renderLevelList();
    renderLayerList();
    renderPalette();
    state.pending = newPending();
    const target = direction === "undo" ? entry.before : entry.after;
    target.forEach((write) => writeToPending(write.index, write.value));
    await submitPending({ recordHistory: false });
    if (direction === "undo") {
        state.redoStack.push(entry);
    } else {
        state.undoStack.push(entry);
    }
}

async function fetchSnapshots() {
    const response = await fetch(`/api/maps/${state.mapId}/snapshots`, { credentials: "same-origin" });
    if (!response.ok) return;
    const payload = await response.json();
    elements.snapshotList.innerHTML = "";
    payload.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "pill subtle";
        row.textContent = `${item.label} · v${item.version}`;
        elements.snapshotList.appendChild(row);
    });
}

async function createSnapshot() {
    const label = window.prompt("Название снапшота", `snapshot-${Date.now()}`);
    if (!label) return;
    const response = await fetch(`/api/maps/${state.mapId}/snapshots`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
        credentials: "same-origin",
        body: JSON.stringify({ label }),
    });
    if (response.ok) {
        setStatus("Снапшот сохранён.");
        await fetchSnapshots();
    }
}

async function importJson(file) {
    const text = await file.text();
    const payload = JSON.parse(text);
    const response = await fetch("/api/maps/import", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
        credentials: "same-origin",
        body: JSON.stringify({ payload }),
    });
    if (!response.ok) {
        setStatus("Импорт не выполнен.");
        return;
    }
    const result = await response.json();
    window.location.href = `/maps/${result.map_id}`;
}

function connectSocket() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/ws/sessions/${state.sessionCode}?csrf=${encodeURIComponent(state.csrfToken)}`;
    state.ws = new WebSocket(url);
    state.ws.addEventListener("message", async (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "map_patch_applied" && payload.map_id === state.mapId) {
            payload.changes.forEach((change) => {
                const level = state.map.levels.find((item) => item.id === change.level_id);
                const layer = level.layers.find((item) => item.layer_key === change.layer_key);
                change.writes.forEach((write) => {
                    layer.cells[write.index] = write.value;
                });
            });
            updateVersion(payload.version);
            renderCanvas();
        } else if (payload.type === "snapshot_created" && payload.map_id === state.mapId) {
            await fetchSnapshots();
        } else if (payload.type === "presence_state") {
            setStatus(`Подключено участников: ${payload.connected}`);
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
    qs("#undo-btn").addEventListener("click", async () => {
        const entry = state.undoStack.pop();
        if (entry) await performUndo(entry, "undo");
    });
    qs("#redo-btn").addEventListener("click", async () => {
        const entry = state.redoStack.pop();
        if (entry) await performUndo(entry, "redo");
    });
    qs("#zoom-in").addEventListener("click", () => { state.zoom = Math.min(4, state.zoom + 0.25); renderCanvas(); });
    qs("#zoom-out").addEventListener("click", () => { state.zoom = Math.max(0.5, state.zoom - 0.25); renderCanvas(); });
    qs("#toggle-grid").addEventListener("click", () => { state.showGrid = !state.showGrid; renderCanvas(); });
    qs("#snapshot-btn").addEventListener("click", createSnapshot);
    if (elements.importJson) {
        elements.importJson.addEventListener("change", async (event) => {
            const file = event.target.files[0];
            if (file) await importJson(file);
        });
    }
}

elements.canvas.addEventListener("pointerdown", async (event) => {
    const cell = cellFromEvent(event);
    if (!cell) return;
    const layer = activeLayer();
    if (state.activeTool === "picker") {
        state.selectedTileValue = layer.cells[cell.index];
        renderPalette();
        return;
    }
    if (!beginPending()) return;
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
    if (!state.pointerDown || !state.pending) return;
    if (state.activeTool !== "pencil" && state.activeTool !== "eraser") return;
    const cell = cellFromEvent(event);
    if (!cell) return;
    applyBrush(cell.x, cell.y, state.activeTool === "eraser" ? 0 : state.selectedTileValue);
    renderCanvas();
});

elements.canvas.addEventListener("pointerup", async (event) => {
    if (!state.pointerDown || !state.pending) return;
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
    bindUi();
    await fetchMap();
    connectSocket();
    setStatus(state.role === "admin" ? "Готово к редактированию." : "Режим просмотра.");
}

boot().catch((error) => {
    console.error(error);
    setStatus("Не удалось загрузить редактор.");
});
