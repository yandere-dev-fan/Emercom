from app.domain.fire_sim import apply_fire_tick


def test_fire_tick_spreads_heat_with_expected_weights() -> None:
    next_heat, next_state, next_smoke = apply_fire_tick(
        width=3,
        height=3,
        floor_cells=[1] * 9,
        wall_cells=[0] * 9,
        openings_cells=[0] * 9,
        interior_cells=[0] * 9,
        heat=[0, 0, 0, 0, 4, 0, 0, 0, 0],
        state=[0, 0, 0, 0, 2, 0, 0, 1, 0],
        nozzle_targets=[],
    )
    assert next_heat[0] == 2
    assert next_heat[1] == 2
    assert next_heat[3] == 3
    assert next_smoke[4] == 2
    assert next_smoke[7] == 1
    assert next_state[4] == 2


def test_nozzle_reduces_heat_in_target_radius() -> None:
    next_heat, _, _ = apply_fire_tick(
        width=3,
        height=3,
        floor_cells=[1] * 9,
        wall_cells=[0] * 9,
        openings_cells=[0] * 9,
        interior_cells=[0] * 9,
        heat=[10] * 9,
        state=[0] * 9,
        nozzle_targets=[4],
    )
    assert next_heat[4] == 8
    assert next_heat[0] == 8
    assert next_heat[8] == 8


def test_origin_fire_can_downgrade_only_after_cooling() -> None:
    _, next_state, _ = apply_fire_tick(
        width=1,
        height=1,
        floor_cells=[1],
        wall_cells=[0],
        openings_cells=[0],
        interior_cells=[0],
        heat=[1],
        state=[2],
        nozzle_targets=[0],
    )
    assert next_state == [1]
