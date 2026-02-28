const root = document.getElementById("editor-root");

if (!root) {
    throw new Error("Корневой элемент редактора не найден.");
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
    canReorder: root.dataset.canReorder === "true",
    canCreateObjectMap: root.dataset.canCreateObjectMap === "true",
    map: null,
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

const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => Array.from(document.querySelectorAll(selector));

function setStatus(message) {
    elements.statusLine.textContent = message;
}

function activeLevel() {
    return state.map.levels.find((level) => level.id === state.activeLevelId);
}

function orderedLayers(level = activeLevel()) {
    return [...level.layers].sort((left, right) => left.z_index - right.z_index);
}

function refreshActiveLayer() {
    const level = activeLevel();
    if (!level) {
        state.activeLayerKey = "ground";
        return;
    }
    const layer = level.layers.find((item) => item.layer_key === state.activeLayerKey);
    if (!layer) {
        state.activeLayerKey = orderedLayers(level)[0].layer_key;
    }
}

function activeLayer() {
    const level = activeLevel();
    return level.layers.find((layer) => layer.layer_key === state.activeLayerKey);
}

function tileDefinitions(layerKey) {
    return state.map.palette_manifest[layerKey] || [];
}

function layerLabel(layerKey) {
    const labels = {
        ground: "Покрытие",
        objects: "Объекты",
        buildings: "Здания",
        effects_fire: "Огонь",
        effects_smoke: "Дым",
        markers: "Маркеры",
    };
    return labels[layerKey] || layerKey;
}

function tileColor(layerKey, code) {
    const found = tileDefinitions(layerKey).find((item) => item.code === code);
    return found ? found.color : "transparent";
}

function tileLabel(layerKey, code) {
    const found = tileDefinitions(layerKey).find((item) => item.code === code);
    return found ? found.label : `Код ${code}`;
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

function updateSelectedTileLabel() {
    const current = tileDefinitions(state.activeLayerKey).find((tile) => tile.code === state.selectedTileValue);
    elements.selectedTileLabel.textContent = current ? `${current.label} (${current.code})` : "Ничего";
}

function getBuildingSelectionInfo() {
    if (!elements.selectedBuilding || !state.selectedCell || state.selectedCell.levelId !== state.activeLevelId) {
        return null;
    }
    const level = activeLevel();
    const buildingsLayer = level.layers.find((layer) => layer.layer_key === "buildings");
    if (!buildingsLayer) {
        return null;
    }
    const buildingCode = buildingsLayer.cells[state.selectedCell.index] || 0;
    if (buildingCode === 0) {
        return null;
    }
    const x = state.selectedCell.index % state.map.width;
    const y = Math.floor(state.selectedCell.index / state.map.width);
    return {
        levelId: state.selectedCell.levelId,
        index: state.selectedCell.index,
        code: buildingCode,
        label: tileLabel("buildings", buildingCode),
        x,
        y,
    };
}

function updateObjectMapControls() {
    if (!elements.selectedBuilding || !elements.createObjectMapBtn) {
        return;
    }
    const selection = getBuildingSelectionInfo();
    if (!selection) {
        elements.selectedBuilding.textContent = "Выберите клетку здания на карте.";
        elements.createObjectMapBtn.disabled = true;
        return;
    }
    elements.selectedBuilding.textContent = `Выбрано: ${selection.label} в точке [${selection.x}, ${selection.y}].`;
    elements.createObjectMapBtn.disabled = !state.canCreateObjectMap;
}

function setSelectedCell(cell) {
    if (!elements.selectedBuilding) {
        return;
    }
    state.selectedCell = { levelId: state.activeLevelId, index: cell.index };
    updateObjectMapControls();
    renderCanvas();
}

async function fetchMap() {
    const response = await fetch(state.apiMapBase, { credentials: "same-origin" });
    if (!response.ok) {
        throw new Error("Не удалось загрузить карту.");
    }
    const previousLevelId = state.activeLevelId;
    state.map = await response.json();
    ensureArrays();
    state.activeLevelId = state.map.levels.some((level) => level.id === previousLevelId)
        ? previousLevelId
        : state.map.levels[0].id;
    refreshActiveLayer();
    state.selectedTileValue = 1;
    renderLevelList();
    renderLayerList();
    renderPalette();
    updateObjectMapControls();
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
            refreshActiveLayer();
            state.selectedCell = null;
            renderLayerList();
            renderPalette();
            updateObjectMapControls();
            renderCanvas();
        });
        elements.levelList.appendChild(row);
    });
}

async function reorderLayer(layerKey, direction) {
    if (!state.canReorder) {
        setStatus("Перестановка слоёв отключена для этого редактора.");
        return;
    }
    const response = await fetch(`${state.apiMapBase}/layers/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
        credentials: "same-origin",
        body: JSON.stringify({
            level_id: state.activeLevelId,
            layer_key: layerKey,
            direction,
        }),
    });
    if (!response.ok) {
        throw new Error("Не удалось изменить порядок слоёв.");
    }
    const payload = await response.json();
    applyLayerOrder(payload.level_id, payload.layer_order);
    updateVersion(payload.version);
    renderLayerList();
    renderCanvas();
    setStatus(`Порядок слоёв обновлён. Версия ${payload.version}.`);
}

function renderLayerList() {
    elements.layerList.innerHTML = "";
    const level = activeLevel();
    const layers = orderedLayers(level);
    layers.forEach((layer, index) => {
        const row = document.createElement("div");
        row.className = "layer-row";
        row.innerHTML = `
            <input type="radio" name="layer" ${layer.layer_key === state.activeLayerKey ? "checked" : ""}>
            <span>${layerLabel(layer.layer_key)}</span>
            <button type="button" class="button layer-move move-up" title="Выше" ${index === 0 || !state.canReorder ? "disabled" : ""}>^</button>
            <button type="button" class="button layer-move move-down" title="Ниже" ${index === layers.length - 1 || !state.canReorder ? "disabled" : ""}>v</button>
            <input type="checkbox" class="small-toggle visibility" ${layer.visible ? "checked" : ""} title="Видимость">
            <input type="checkbox" class="small-toggle locked" ${layer.locked ? "checked" : ""} title="Блокировка">
        `;
        row.querySelector('input[type="radio"]').addEventListener("change", () => {
            state.activeLayerKey = layer.layer_key;
            state.selectedTileValue = 1;
            renderPalette();
        });
        row.querySelector(".move-up").addEventListener("click", async () => {
            try {
                await reorderLayer(layer.layer_key, "up");
            } catch (error) {
                console.error(error);
                setStatus("Не удалось изменить порядок слоя.");
            }
        });
        row.querySelector(".move-down").addEventListener("click", async () => {
            try {
                await reorderLayer(layer.layer_key, "down");
            } catch (error) {
                console.error(error);
                setStatus("Не удалось изменить порядок слоя.");
            }
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
            updateSelectedTileLabel();
        });
        elements.paletteList.appendChild(row);
    });
    updateSelectedTileLabel();
}

function resizeCanvas() {
    const size = Math.max(1, Math.floor(state.map.cell_size_px * state.zoom));
    elements.canvas.width = state.map.width * size;
    elements.canvas.height = state.map.height * size;
}

function renderSelectionOutline(size) {
    if (!state.selectedCell || state.selectedCell.levelId !== state.activeLevelId) {
        return;
    }
    const x = state.selectedCell.index % state.map.width;
    const y = Math.floor(state.selectedCell.index / state.map.width);
    const selection = getBuildingSelectionInfo();
    canvasCtx.strokeStyle = selection ? "rgba(6, 214, 160, 0.9)" : "rgba(239, 131, 84, 0.9)";
    canvasCtx.lineWidth = Math.max(2, Math.floor(size * 0.1));
    canvasCtx.strokeRect(x * size + 1, y * size + 1, Math.max(size - 2, 1), Math.max(size - 2, 1));
}

function renderCanvas() {
    if (!state.map) {
        return;
    }
    resizeCanvas();
    const size = Math.max(1, Math.floor(state.map.cell_size_px * state.zoom));
    canvasCtx.clearRect(0, 0, elements.canvas.width, elements.canvas.height);
    const level = activeLevel();
    const layers = orderedLayers(level);
    for (let y = 0; y < state.map.height; y += 1) {
        for (let x = 0; x < state.map.width; x += 1) {
            const index = y * state.map.width + x;
            layers
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
    renderSelectionOutline(size);
    renderMiniMap();
}

function renderMiniMap() {
    const scale = Math.min(elements.miniCanvas.width / state.map.width, elements.miniCanvas.height / state.map.height);
    miniCtx.clearRect(0, 0, elements.miniCanvas.width, elements.miniCanvas.height);
    const level = activeLevel();
    const layers = orderedLayers(level);
    for (let y = 0; y < state.map.height; y += 1) {
        for (let x = 0; x < state.map.width; x += 1) {
            const index = y * state.map.width + x;
            let fill = "#101820";
            for (const layer of layers) {
                if (!layer.visible) {
                    continue;
                }
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
    const points = [];
    let x0 = start.x;
    let y0 = start.y;
    const x1 = end.x;
    const y1 = end.y;
    const dx = Math.abs(x1 - x0);
    const sx = x0 < x1 ? 1 : -1;
    const dy = -Math.abs(y1 - y0);
    const sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    while (true) {
        points.push({ x: x0, y: y0 });
        if (x0 === x1 && y0 === y1) {
            break;
        }
        const e2 = 2 * err;
        if (e2 >= dy) {
            err += dy;
            x0 += sx;
        }
        if (e2 <= dx) {
            err += dx;
            y0 += sy;
        }
    }
    return points;
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
    const body = {
        base_version: state.map.version,
        client_event_id: crypto.randomUUID(),
        changes: [{ level_id: state.activeLevelId, layer_key: state.activeLayerKey, writes }],
    };
    try {
        const response = await fetch(`${state.apiMapBase}/patches`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
            credentials: "same-origin",
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            throw new Error("Не удалось сохранить изменения.");
        }
        const payload = await response.json();
        updateVersion(payload.version);
        if (recordHistory) {
            state.undoStack.push({ levelId: state.activeLevelId, layerKey: state.activeLayerKey, before, after: writes });
            state.redoStack = [];
        }
        updateObjectMapControls();
        setStatus(`Изменения сохранены. Версия ${payload.version}.`);
    } catch (error) {
        console.error(error);
        await fetchMap();
        setStatus("Карта перезагружена после конфликта версии или ошибки валидации.");
    } finally {
        state.pending = null;
        renderCanvas();
    }
}

function beginPending() {
    if (!state.canEdit) {
        setStatus("Редактор открыт только для просмотра.");
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
    if (!level) {
        return;
    }
    const layer = level.layers.find((item) => item.layer_key === entry.layerKey);
    if (!layer) {
        return;
    }
    state.activeLevelId = entry.levelId;
    state.activeLayerKey = entry.layerKey;
    renderLevelList();
    renderLayerList();
    renderPalette();
    state.pending = newPending();
    const targetWrites = direction === "undo" ? entry.before : entry.after;
    targetWrites.forEach((write) => writeToPending(write.index, write.value));
    await submitPending({ recordHistory: false });
    if (direction === "undo") {
        state.redoStack.push(entry);
    } else {
        state.undoStack.push(entry);
    }
}

async function fetchSnapshots() {
    const response = await fetch(`${state.apiMapBase}/snapshots`, { credentials: "same-origin" });
    if (!response.ok) {
        return;
    }
    const payload = await response.json();
    elements.snapshotList.innerHTML = "";
    if (!payload.items.length) {
        const row = document.createElement("div");
        row.className = "pill subtle";
        row.textContent = "Снимков пока нет.";
        elements.snapshotList.appendChild(row);
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
        setStatus("В режиме просмотра нельзя создавать снимки.");
        return;
    }
    const label = window.prompt("Название снимка", `snapshot-${Date.now()}`);
    if (!label) {
        return;
    }
    const response = await fetch(`${state.apiMapBase}/snapshots`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
        credentials: "same-origin",
        body: JSON.stringify({ label }),
    });
    if (!response.ok) {
        setStatus("Не удалось сохранить снимок.");
        return;
    }
    setStatus("Снимок сохранён.");
    await fetchSnapshots();
}

async function importJson(file) {
    try {
        const text = await file.text();
        const payload = JSON.parse(text);
        const response = await fetch(state.importUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
            credentials: "same-origin",
            body: JSON.stringify({ payload }),
        });
        if (!response.ok) {
            setStatus("Не удалось импортировать файл.");
            return;
        }
        const result = await response.json();
        window.location.href = state.editorUrlPattern.replace("{id}", result.map_id);
    } catch (error) {
        console.error(error);
        setStatus("Некорректный файл импорта.");
    }
}

function applyLayerOrder(levelId, layerOrder) {
    const level = state.map.levels.find((item) => item.id === levelId);
    if (!level) {
        return;
    }
    const layersByKey = new Map(level.layers.map((layer) => [layer.layer_key, layer]));
    const reordered = [];
    layerOrder.forEach((key, index) => {
        const layer = layersByKey.get(key);
        if (!layer) {
            return;
        }
        layer.z_index = index + 1;
        reordered.push(layer);
        layersByKey.delete(key);
    });
    [...layersByKey.values()]
        .sort((left, right) => left.z_index - right.z_index)
        .forEach((layer) => {
            layer.z_index = reordered.length + 1;
            reordered.push(layer);
        });
    level.layers = reordered;
}

async function createObjectMapFromSelection() {
    if (!state.canCreateObjectMap) {
        setStatus("Создание карты объекта недоступно в этом редакторе.");
        return;
    }
    const selection = getBuildingSelectionInfo();
    if (!selection) {
        updateObjectMapControls();
        setStatus("Сначала выберите клетку здания.");
        return;
    }
    const response = await fetch(`${state.apiMapBase}/object-maps`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
        credentials: "same-origin",
        body: JSON.stringify({
            source_level_id: selection.levelId,
            source_index: selection.index,
        }),
    });
    if (!response.ok) {
        setStatus("Не удалось создать карту объекта.");
        return;
    }
    const payload = await response.json();
    window.location.href = state.editorUrlPattern.replace("{id}", payload.map_id);
}

function connectSocket() {
    if (!state.wsEnabled || !state.sessionCode) {
        return;
    }
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/ws/sessions/${state.sessionCode}?csrf=${encodeURIComponent(state.csrfToken)}`;
    state.ws = new WebSocket(url);
    state.ws.addEventListener("message", async (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "map_patch_applied" && payload.map_id === state.mapId) {
            payload.changes.forEach((change) => {
                const level = state.map.levels.find((item) => item.id === change.level_id);
                if (!level) {
                    return;
                }
                const layer = level.layers.find((item) => item.layer_key === change.layer_key);
                if (!layer) {
                    return;
                }
                change.writes.forEach((write) => {
                    layer.cells[write.index] = write.value;
                });
            });
            updateVersion(payload.version);
            updateObjectMapControls();
            renderCanvas();
        } else if (payload.type === "snapshot_created" && payload.map_id === state.mapId) {
            await fetchSnapshots();
        } else if (payload.type === "layer_order_updated" && payload.map_id === state.mapId) {
            applyLayerOrder(payload.level_id, payload.layer_order);
            updateVersion(payload.version);
            renderLayerList();
            renderCanvas();
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
        if (entry) {
            await performUndo(entry, "undo");
        }
    });
    qs("#redo-btn").addEventListener("click", async () => {
        const entry = state.redoStack.pop();
        if (entry) {
            await performUndo(entry, "redo");
        }
    });
    qs("#zoom-in").addEventListener("click", () => {
        state.zoom = Math.min(4, state.zoom + 0.25);
        renderCanvas();
    });
    qs("#zoom-out").addEventListener("click", () => {
        state.zoom = Math.max(0.5, state.zoom - 0.25);
        renderCanvas();
    });
    qs("#toggle-grid").addEventListener("click", () => {
        state.showGrid = !state.showGrid;
        renderCanvas();
    });
    elements.snapshotBtn.addEventListener("click", createSnapshot);
    if (!state.canEdit) {
        elements.snapshotBtn.disabled = true;
    }
    if (elements.importJson) {
        elements.importJson.addEventListener("change", async (event) => {
            const file = event.target.files[0];
            if (file) {
                await importJson(file);
            }
        });
    }
    if (elements.createObjectMapBtn) {
        elements.createObjectMapBtn.addEventListener("click", async () => {
            try {
                await createObjectMapFromSelection();
            } catch (error) {
                console.error(error);
                setStatus("Не удалось создать карту объекта.");
            }
        });
    }
}

elements.canvas.addEventListener("pointerdown", async (event) => {
    const cell = cellFromEvent(event);
    if (!cell) {
        return;
    }
    if (elements.selectedBuilding) {
        setSelectedCell(cell);
    }
    const layer = activeLayer();
    if (state.activeTool === "picker") {
        state.selectedTileValue = layer.cells[cell.index];
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
    if (!state.pointerDown || !state.pending) {
        return;
    }
    if (state.activeTool !== "pencil" && state.activeTool !== "eraser") {
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
    setStatus(state.canEdit ? "Редактор готов." : "Режим просмотра.");
}

boot().catch((error) => {
    console.error(error);
    setStatus("Не удалось загрузить редактор.");
});
