import { connectSessionSocket, postJson } from "./map_editor_core.js";

const root = document.getElementById("session-lobby");

if (root) {
    const sessionCode = root.dataset.sessionCode;
    const csrfToken = root.dataset.csrfToken;
    let isNavigating = false;

    // Grace period: ignore WebSocket events for the first 3 seconds after page load
    // to prevent infinite reload loop (server broadcasts "participant_joined" on WS connect)
    const pageLoadTime = Date.now();
    const GRACE_MS = 3000;

    function safeReload() {
        if (isNavigating) return;
        isNavigating = true;
        window.location.reload();
    }

    document.querySelectorAll("[data-role-action]").forEach((button) => {
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                await postJson(
                    `/api/sessions/${sessionCode}/participants/${button.dataset.participantId}/role`,
                    csrfToken,
                    { role: button.dataset.role },
                );
                safeReload();
            } catch (error) {
                window.alert(error.message);
                button.disabled = false;
            }
        });
    });

    const startButton = document.getElementById("start-drill-btn");
    if (startButton) {
        startButton.addEventListener("click", async () => {
            startButton.disabled = true;
            startButton.textContent = "⏳ Запуск...";
            try {
                await postJson(`/api/sessions/${sessionCode}/start`, csrfToken);
                // Navigate directly to the session page (now shows runtime)
                isNavigating = true;
                window.location.href = `/sessions/${sessionCode}`;
            } catch (error) {
                window.alert(error.message);
                startButton.disabled = false;
                startButton.textContent = "🔥 Начать учения";
            }
        });
    }

    // Debounced reload timer
    let reloadTimer = null;

    connectSessionSocket(sessionCode, csrfToken, (message) => {
        if (isNavigating) return;

        // Ignore events during startup grace period to prevent reload loop
        if (Date.now() - pageLoadTime < GRACE_MS) {
            return;
        }

        if (message.type === "session_phase_changed") {
            // Session started by someone else — navigate to runtime
            isNavigating = true;
            window.location.href = `/sessions/${sessionCode}`;
            return;
        }

        if (["participant_joined", "participant_left", "participant_role_updated"].includes(message.type)) {
            // Debounce: only reload once even if multiple events arrive quickly
            if (reloadTimer) clearTimeout(reloadTimer);
            reloadTimer = setTimeout(() => safeReload(), 1000);
        }
    });
}
