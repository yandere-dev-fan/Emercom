import { connectSessionSocket, postJson } from "./map_editor_core.js";

const root = document.getElementById("session-lobby");

if (root) {
    const sessionCode = root.dataset.sessionCode;
    const csrfToken = root.dataset.csrfToken;

    document.querySelectorAll("[data-role-action]").forEach((button) => {
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                await postJson(
                    `/api/sessions/${sessionCode}/participants/${button.dataset.participantId}/role`,
                    csrfToken,
                    { role: button.dataset.role },
                );
                window.location.reload();
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
            try {
                await postJson(`/api/sessions/${sessionCode}/start`, csrfToken);
                window.location.reload();
            } catch (error) {
                window.alert(error.message);
                startButton.disabled = false;
            }
        });
    }

    connectSessionSocket(sessionCode, csrfToken, (message) => {
        if (["participant_joined", "participant_left", "participant_role_updated", "session_phase_changed"].includes(message.type)) {
            window.location.reload();
        }
    });
}
