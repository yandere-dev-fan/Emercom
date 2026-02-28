from __future__ import annotations

from collections.abc import Iterable

from app.domain.tile_catalog_v3 import ignition_threshold, get_heat_release, get_conductivity


def get_neighbors_with_distance(index: int, width: int, height: int) -> list[tuple[int, float]]:
    x = index % width
    y = index // width
    result = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = x + dx
            ny = y + dy
            if 0 <= nx < width and 0 <= ny < height:
                neighbor_idx = ny * width + nx
                distance = 1.414 if dx != 0 and dy != 0 else 1.0
                result.append((neighbor_idx, distance))
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

    for i in range(len(next_heat)):
        if next_heat[i] > 0:
            next_heat[i] = max(0, next_heat[i] - 1)

    for index, cell_state in enumerate(state):
        if cell_state <= 0:
            continue
            
        base_heat_release = max(
            get_heat_release("interior", interior_cells[index]),
            get_heat_release("floor", floor_cells[index]),
            get_heat_release("walls", wall_cells[index])
        )
        
        generated_heat = base_heat_release if base_heat_release > 0 else (5 if cell_state == 2 else 2)
        smoke[index] = 2 if cell_state == 2 else 1

        for neighbor_idx, distance in get_neighbors_with_distance(index, width, height):
            conductivity = min([
                get_conductivity("walls", wall_cells[neighbor_idx]),
                get_conductivity("openings", openings_cells[neighbor_idx])
            ])

            transferred_heat = int((generated_heat / distance) * conductivity)
            if transferred_heat > 0:
                next_heat[neighbor_idx] += transferred_heat

    for target_index in nozzle_targets:
        neighbors = [n[0] for n in get_neighbors_with_distance(target_index, width, height)]
        for affected in [target_index, *neighbors]:
            next_heat[affected] = max(0, next_heat[affected] - 15)

    for index, cell_state in enumerate(state):
        if cell_state == 2:
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
