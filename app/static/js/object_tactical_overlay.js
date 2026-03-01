function cellLine(startX, startY, endX, endY) {
    const cells = [];
    let x0 = startX;
    let y0 = startY;
    const x1 = endX;
    const y1 = endY;
    const dx = Math.abs(x1 - x0);
    const sx = x0 < x1 ? 1 : -1;
    const dy = -Math.abs(y1 - y0);
    const sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    while (true) {
        cells.push({ x: x0, y: y0 });
        if (x0 === x1 && y0 === y1) {
            break;
        }
        const twice = 2 * err;
        if (twice >= dy) {
            err += dy;
            x0 += sx;
        }
        if (twice <= dx) {
            err += dx;
            y0 += sy;
        }
    }
    return cells;
}

export function createObjectTacticalOverlay({ getRuntimeOverlay, getCellSize, getActiveLevelCode }) {
    function activeVehicles() {
        const runtimeOverlay = getRuntimeOverlay();
        const levelCode = getActiveLevelCode();
        const vehicles = runtimeOverlay?.vehicles || [];
        return vehicles.filter((vehicle) => !vehicle.current_level_code || vehicle.current_level_code === levelCode);
    }

    function findVehicle(vehicleId) {
        return activeVehicles().find((vehicle) => vehicle.id === vehicleId) || null;
    }

    function hitTestVehicle(cellX, cellY) {
        return activeVehicles().find(
            (vehicle) => Math.round(vehicle.position_x) === cellX && Math.round(vehicle.position_y) === cellY,
        ) || null;
    }

    function drawPolyline(ctx, points, color, size) {
        if (!points || points.length < 2) {
            return;
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = Math.max(2, Math.floor(size * 0.18));
        ctx.beginPath();
        ctx.moveTo(points[0].x * size + size / 2, points[0].y * size + size / 2);
        points.slice(1).forEach((point) => {
            ctx.lineTo(point.x * size + size / 2, point.y * size + size / 2);
        });
        ctx.stroke();
    }

    function draw(ctx, { selectedVehicleId, previewPath = [] }) {
        const runtimeOverlay = getRuntimeOverlay();
        if (!runtimeOverlay) {
            return;
        }
        const size = getCellSize();
        drawPolyline(ctx, previewPath, "rgba(34, 197, 94, 0.85)", size);
        (runtimeOverlay.hoses || []).forEach((hose) => drawPolyline(ctx, hose.polyline_points || [], "rgba(14, 165, 233, 0.8)", size));
        (runtimeOverlay.nozzles || []).forEach((nozzle) => {
            const centerX = nozzle.target_x * size + size / 2;
            const centerY = nozzle.target_y * size + size / 2;
            ctx.strokeStyle = "rgba(6, 182, 212, 0.9)";
            ctx.lineWidth = Math.max(2, Math.floor(size * 0.14));
            ctx.beginPath();
            ctx.arc(centerX, centerY, size * 1.4, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = "rgba(6, 182, 212, 0.85)";
            ctx.beginPath();
            ctx.arc(centerX, centerY, Math.max(3, size * 0.18), 0, Math.PI * 2);
            ctx.fill();
        });
        activeVehicles().forEach((vehicle) => {
            const x = Math.round(vehicle.position_x);
            const y = Math.round(vehicle.position_y);
            ctx.fillStyle = vehicle.id === selectedVehicleId ? "rgba(132, 204, 22, 0.95)" : "rgba(251, 146, 60, 0.9)";
            ctx.fillRect(x * size + size * 0.18, y * size + size * 0.18, size * 0.64, size * 0.64);
            ctx.strokeStyle = "rgba(15, 23, 42, 0.9)";
            ctx.lineWidth = Math.max(1, Math.floor(size * 0.08));
            ctx.strokeRect(x * size + size * 0.18, y * size + size * 0.18, size * 0.64, size * 0.64);
        });
    }

    return {
        cellLine,
        draw,
        findVehicle,
        hitTestVehicle,
    };
}
