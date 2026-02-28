from app.domain.pathfinding import weighted_a_star


def test_weighted_a_star_prefers_lower_total_cost() -> None:
    # 3x3 grid, center top row is expensive, algorithm should go around.
    costs = [
        1, 5, 1,
        1, 1, 1,
        1, 1, 1,
    ]
    path = weighted_a_star(width=3, height=3, start_index=0, target_index=2, cell_costs=costs)
    assert path[0] == 0
    assert path[-1] == 2
    assert 1 not in path[1:-1]
