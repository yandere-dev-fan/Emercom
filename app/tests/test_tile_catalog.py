from app.domain.tile_catalog_v3 import AREA_TILE_CATALOG, OBJECT_TILE_CATALOG, max_code_for_layer


def test_every_layer_has_zero_tile() -> None:
    for catalog in (AREA_TILE_CATALOG, OBJECT_TILE_CATALOG):
        for layer_key, items in catalog.items():
            assert items[0].code == 0
            kind = "area" if catalog is AREA_TILE_CATALOG else "object"
            assert max_code_for_layer(layer_key, kind) >= 0
