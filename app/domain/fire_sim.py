from __future__ import annotations

from collections.abc import Iterable

from app.domain.tile_catalog_v3 import ignition_threshold


def king_neighbors(index: int, width: int, height: int) -> list[int]:
    x = index % width
    y = index // width
    result: list[int] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = x + dx
            ny = y + dy
            if 0 <= nx < width and 0 <= ny < height:
                result.append(ny * width + nx)
    return result


def apply_fire_tick(
    *,
    width: int,
    height: int,
    floor_cells: list[int],
    wall_cells: list[int],
    openings_cells: list[int],
    interior_cells: list[int],
    heat: list[int],
    state: list[int],
    nozzle_targets: Iterable[int],
) -> tuple[list[int], list[int], list[int]]:
    next_heat = list(heat)
    next_state = list(state)
    smoke = [0] * len(state)

    for index, cell_state in enumerate(state):
        if cell_state <= 0:
            continue
        delta = 2 if cell_state == 2 else 1
        smoke[index] = 2 if cell_state == 2 else 1
        for neighbor in king_neighbors(index, width, height):
            next_heat[neighbor] += delta

    for target_index in nozzle_targets:
        for affected in [target_index, *king_neighbors(target_index, width, height)]:
            next_heat[affected] = max(0, next_heat[affected] - 2)

    for index, cell_state in enumerate(state):
        if cell_state == 2:
            # The original seat of fire stays the strongest until sustained cooling.
            if next_heat[index] < 4:
                next_state[index] = 1
            smoke[index] = max(smoke[index], 2)
            continue

        threshold = max(
            ignition_threshold("floor", floor_cells[index]),
            ignition_threshold("walls", wall_cells[index]),
            ignition_threshold("openings", openings_cells[index]),
            ignition_threshold("interior", interior_cells[index]),
        )
        if next_heat[index] >= threshold:
            next_state[index] = 1
            smoke[index] = max(smoke[index], 1)
        elif next_heat[index] < threshold // 2:
            next_state[index] = 0

    return next_heat, next_state, smoke
