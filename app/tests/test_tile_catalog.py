from app.domain.tile_catalog_v3 import (
    AREA_LAYER_ORDER,
    DEFAULT_AREA_LEVELS,
    DEFAULT_OBJECT_LEVELS,
    IGNITION_THRESHOLDS,
    MAX_OBJECT_FLOORS,
    OBJECT_LAYER_ORDER,
    AREA_TILE_CATALOG,
    OBJECT_TILE_CATALOG,
    area_travel_cost,
    ignition_threshold,
    max_code_for_layer,
)


def test_every_layer_has_zero_tile() -> None:
    for catalog in (AREA_TILE_CATALOG, OBJECT_TILE_CATALOG):
        for layer_key, items in catalog.items():
            assert items[0].code == 0
            kind = "area" if catalog is AREA_TILE_CATALOG else "object"
            assert max_code_for_layer(layer_key, kind) >= 0


def test_area_layers_exclude_fire_and_smoke() -> None:
    assert AREA_LAYER_ORDER == ["ground", "objects", "buildings", "markers"]
    assert "effects_fire" not in AREA_TILE_CATALOG
    assert "effects_smoke" not in AREA_TILE_CATALOG


def test_object_layers_include_fire_and_smoke() -> None:
    assert OBJECT_LAYER_ORDER == ["floor", "walls", "openings", "interior", "effects_fire", "effects_smoke", "markers"]


def test_default_levels_and_floor_limits() -> None:
    assert DEFAULT_AREA_LEVELS == [("AREA_MAIN", "Общая карта", 0)]
    assert DEFAULT_OBJECT_LEVELS == [("F1", "Этаж 1", 1)]
    assert MAX_OBJECT_FLOORS == 100


def test_ignition_thresholds_match_expected_materials() -> None:
    assert IGNITION_THRESHOLDS[("openings", 1)] == 10
    assert IGNITION_THRESHOLDS[("walls", 1)] == 10
    assert IGNITION_THRESHOLDS[("floor", 1)] == 10
    assert ignition_threshold("walls", 4) == 300
    assert ignition_threshold("floor", 0) == 10_000
    assert ignition_threshold("unknown", 9) == 10_000


def test_area_travel_cost_matches_road_and_grass_speeds() -> None:
    assert area_travel_cost(1, 0, 0) == 1
    assert area_travel_cost(2, 0, 0) == 1
    assert area_travel_cost(3, 0, 0) == 2
    assert area_travel_cost(4, 0, 0) == 2
    assert area_travel_cost(7, 0, 0) is None
