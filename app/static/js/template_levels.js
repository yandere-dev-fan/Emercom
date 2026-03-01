const root = document.getElementById("editor-root");
const addLevelBtn = document.getElementById("add-level-btn");

async function loadTemplateMap() {
    if (!root) {
        return null;
    }
    const response = await fetch(root.dataset.apiMapBase, { credentials: "same-origin" });
    if (!response.ok) {
        return null;
    }
    return response.json();
}

async function syncAddLevelAvailability() {
    if (!root || !addLevelBtn) {
        return;
    }
    const map = await loadTemplateMap();
    if (!map) {
        addLevelBtn.disabled = false;
        return;
    }
    const isObjectMap = root.dataset.mapKind === "object";
    addLevelBtn.disabled = !isObjectMap || map.levels.length >= 100;
}

if (root && addLevelBtn) {
    syncAddLevelAvailability().catch(() => {});
    addLevelBtn.addEventListener("click", async () => {
        if (addLevelBtn.disabled) {
            return;
        }
        addLevelBtn.disabled = true;
        try {
            const response = await fetch(root.dataset.apiAddLevel, {
                method: "POST",
                credentials: "same-origin",
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.detail || `Request failed (${response.status})`);
            }
            window.location.reload();
        } catch (error) {
            window.alert(error.message);
            addLevelBtn.disabled = false;
        }
    });
}
