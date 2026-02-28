from __future__ import annotations

from heapq import heappop, heappush


def manhattan_distance(a: int, b: int, width: int) -> int:
    ax, ay = a % width, a // width
    bx, by = b % width, b // width
    return abs(ax - bx) + abs(ay - by)


def neighbors(index: int, width: int, height: int) -> list[int]:
    x = index % width
    y = index // width
    result: list[int] = []
    if x > 0:
        result.append(index - 1)
    if x < width - 1:
        result.append(index + 1)
    if y > 0:
        result.append(index - width)
    if y < height - 1:
        result.append(index + width)
    return result


def weighted_a_star(
    *,
    width: int,
    height: int,
    start_index: int,
    target_index: int,
    cell_costs: list[int | None],
) -> list[int]:
    if start_index == target_index:
        return [start_index]
    open_heap: list[tuple[int, int]] = []
    heappush(open_heap, (0, start_index))
    came_from: dict[int, int] = {}
    g_score = {start_index: 0}
    f_score = {start_index: manhattan_distance(start_index, target_index, width)}

    while open_heap:
        _, current = heappop(open_heap)
        if current == target_index:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        for nxt in neighbors(current, width, height):
            step_cost = cell_costs[nxt]
            if step_cost is None:
                continue
            tentative = g_score[current] + step_cost
            if tentative >= g_score.get(nxt, 10**9):
                continue
            came_from[nxt] = current
            g_score[nxt] = tentative
            priority = tentative + manhattan_distance(nxt, target_index, width)
            f_score[nxt] = priority
            heappush(open_heap, (priority, nxt))
    return []
