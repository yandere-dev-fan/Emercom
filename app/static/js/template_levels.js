const root = document.getElementById("editor-root");
const addLevelBtn = document.getElementById("add-level-btn");

if (root && addLevelBtn) {
    addLevelBtn.addEventListener("click", async () => {
        try {
            const response = await fetch(root.dataset.apiAddLevel, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: "{}",
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.detail || `Ошибка ${response.status}`);
            }
            window.location.reload();
        } catch (error) {
            window.alert(error.message);
        }
    });
}
