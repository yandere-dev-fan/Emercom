export async function postJson(url, csrfToken, payload = {}, method = "POST") {
    const response = await fetch(url, {
        method,
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
        },
        credentials: "same-origin",
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        let detail = `Ошибка запроса (${response.status})`;
        try {
            const data = await response.json();
            detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
        } catch (_error) {
            // Ignore parse errors and keep the fallback message.
        }
        throw new Error(detail);
    }
    return response.json();
}

export function parsePolyline(rawValue) {
    return rawValue
        .split(";")
        .map((chunk) => chunk.trim())
        .filter(Boolean)
        .map((chunk) => {
            const [x, y] = chunk.split(",").map((value) => Number(value.trim()));
            return { x, y };
        })
        .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
}

export function connectSessionSocket(sessionCode, csrfToken, onMessage) {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/ws/sessions/${sessionCode}?csrf=${encodeURIComponent(csrfToken)}`;
    const socket = new WebSocket(url);
    socket.addEventListener("message", (event) => {
        onMessage(JSON.parse(event.data));
    });
    return socket;
}
