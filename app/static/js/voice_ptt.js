const button = document.getElementById("ptt-btn");
const statusLine = document.getElementById("ptt-status");

if (button && statusLine) {
    let active = false;
    const channelLabels = {
        dispatch_net: "канал диспетчера",
        command_net: "командный канал",
        ops_net: "оперативный канал",
    };
    button.addEventListener("mousedown", () => {
        active = true;
        const channel = document.getElementById("voice-channel").value;
        statusLine.textContent = `PTT включен: ${channelLabels[channel] || channel} (пока только сигналинг)`;
    });
    button.addEventListener("mouseup", () => {
        if (!active) {
            return;
        }
        active = false;
        statusLine.textContent = "PTT отпущен";
    });
    button.addEventListener("mouseleave", () => {
        if (!active) {
            return;
        }
        active = false;
        statusLine.textContent = "PTT отпущен";
    });
}
