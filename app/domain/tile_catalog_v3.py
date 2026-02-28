from __future__ import annotations

from dataclasses import dataclass


AREA_LAYER_ORDER = ["ground", "objects", "buildings", "effects_fire", "effects_smoke", "markers"]
OBJECT_LAYER_ORDER = ["floor", "walls", "openings", "interior", "effects_fire", "effects_smoke", "markers"]
DEFAULT_AREA_LEVELS = [("AREA_MAIN", "Общая карта", 0)]
DEFAULT_OBJECT_LEVELS = [("F1", "Этаж 1", 1)]
MAX_OBJECT_FLOORS = 100


@dataclass(frozen=True)
class TileDefinition:
    code: int
    label: str
    color: str
    category: str


AREA_TILE_CATALOG: dict[str, list[TileDefinition]] = {
    "ground": [
        TileDefinition(0, "Пусто", "#0f172a", "Покрытие"),
        TileDefinition(1, "Основная дорога", "#4b5563", "Дороги"),
        TileDefinition(2, "Дворовой проезд", "#6b7280", "Дороги"),
        TileDefinition(3, "Трава", "#4d7c0f", "Покрытие"),
        TileDefinition(4, "Грунт", "#9a6b3f", "Покрытие"),
        TileDefinition(5, "Асфальт", "#334155", "Покрытие"),
        TileDefinition(6, "Бетон", "#94a3b8", "Покрытие"),
        TileDefinition(7, "Вода", "#0ea5e9", "Вода"),
    ],
    "objects": [
        TileDefinition(0, "Пусто", "transparent", "Объекты"),
        TileDefinition(1, "Забор", "#7c5c45", "Препятствия"),
        TileDefinition(2, "Стена", "#8b8f97", "Препятствия"),
        TileDefinition(3, "Ворота", "#d97706", "Препятствия"),
        TileDefinition(4, "Шлагбаум", "#fbbf24", "Препятствия"),
        TileDefinition(5, "Пожарный гидрант", "#dc2626", "Водоисточники"),
        TileDefinition(6, "Пожарный водоем", "#2563eb", "Водоисточники"),
        TileDefinition(7, "Точка выезда техники", "#22c55e", "Служебные"),
    ],
    "buildings": [
        TileDefinition(0, "Пусто", "transparent", "Здания"),
        TileDefinition(1, "Частный дом", "#b45309", "Здания"),
        TileDefinition(2, "Многоэтажка", "#92400e", "Здания"),
        TileDefinition(3, "Склад / ангар", "#78350f", "Здания"),
        TileDefinition(4, "Промышленное здание", "#57534e", "Здания"),
    ],
    "markers": [
        TileDefinition(0, "Пусто", "transparent", "Тактика"),
        TileDefinition(1, "Метка диспетчера", "#e11d48", "Тактика"),
        TileDefinition(2, "Точка техники", "#10b981", "Тактика"),
        TileDefinition(3, "Точка подачи", "#0ea5e9", "Тактика"),
        TileDefinition(4, "Зона эвакуации", "#38bdf8", "Тактика"),
    ],
    "effects_fire": [
        TileDefinition(0, "Пусто", "transparent", "Огонь"),
        TileDefinition(1, "Горит", "#f97316", "Огонь"),
        TileDefinition(2, "Очаг", "#dc2626", "Огонь"),
    ],
    "effects_smoke": [
        TileDefinition(0, "Пусто", "transparent", "Дым"),
        TileDefinition(1, "Легкий дым", "rgba(156,163,175,0.35)", "Дым"),
        TileDefinition(2, "Плотный дым", "rgba(75,85,99,0.55)", "Дым"),
    ],
}

OBJECT_TILE_CATALOG: dict[str, list[TileDefinition]] = {
    "floor": [
        TileDefinition(0, "Пусто", "#0f172a", "Пол"),
        TileDefinition(1, "Деревянный пол", "#a16207", "Пол"),
        TileDefinition(2, "Каменный пол", "#78716c", "Пол"),
        TileDefinition(3, "Бетонный пол", "#94a3b8", "Пол"),
    ],
    "walls": [
        TileDefinition(0, "Пусто", "transparent", "Стены"),
        TileDefinition(1, "Деревянная стена", "#92400e", "Стены"),
        TileDefinition(2, "Кирпичная стена", "#b45309", "Стены"),
        TileDefinition(3, "Каменная стена", "#57534e", "Стены"),
        TileDefinition(4, "Бетонная стена", "#64748b", "Стены"),
    ],
    "openings": [
        TileDefinition(0, "Пусто", "transparent", "Проемы"),
        TileDefinition(1, "Дверь", "#22c55e", "Проемы"),
        TileDefinition(2, "Окно", "#38bdf8", "Проемы"),
        TileDefinition(3, "Лестница", "#f59e0b", "Проемы"),
        TileDefinition(4, "Внешний вход", "#84cc16", "Проемы"),
    ],
    "interior": [
        TileDefinition(0, "Пусто", "transparent", "Интерьер"),
        TileDefinition(1, "Мебель / горючая нагрузка", "#fb923c", "Интерьер"),
        TileDefinition(2, "Кухня / техника", "#f97316", "Интерьер"),
        TileDefinition(3, "Газовый узел", "#ef4444", "Интерьер"),
        TileDefinition(4, "Электрощит", "#facc15", "Интерьер"),
    ],
    "effects_fire": [
        TileDefinition(0, "Пусто", "transparent", "Огонь"),
        TileDefinition(1, "Горит", "#f97316", "Огонь"),
        TileDefinition(2, "Очаг", "#dc2626", "Огонь"),
    ],
    "effects_smoke": [
        TileDefinition(0, "Пусто", "transparent", "Дым"),
        TileDefinition(1, "Легкий дым", "rgba(156,163,175,0.35)", "Дым"),
        TileDefinition(2, "Плотный дым", "rgba(75,85,99,0.55)", "Дым"),
    ],
    "markers": [
        TileDefinition(0, "Пусто", "transparent", "Тактика"),
        TileDefinition(1, "Точка входа", "#10b981", "Тактика"),
        TileDefinition(2, "Точка ствола", "#0ea5e9", "Тактика"),
        TileDefinition(3, "Точка очага", "#e11d48", "Тактика"),
        TileDefinition(4, "Позиция техники", "#84cc16", "Тактика"),
    ],
}

IGNITION_THRESHOLDS: dict[tuple[str, int], int] = {
    ("openings", 1): 35,
    ("openings", 2): 35,
    ("walls", 1): 40,
    ("interior", 1): 45,
    ("interior", 2): 45,
    ("interior", 3): 30,
    ("floor", 1): 50,
    ("walls", 2): 180,
    ("floor", 2): 220,
    ("walls", 3): 260,
    ("floor", 3): 250,
    ("walls", 4): 300,
}


def ignition_threshold(layer_key: str, code: int) -> int:
    if code == 0:
        return 9999
    return IGNITION_THRESHOLDS.get((layer_key, code), 9999)


def object_level_code(floor_number: int) -> str:
    return f"F{floor_number}"


def object_level_title(floor_number: int) -> str:
    return f"Этаж {floor_number}"


def default_levels_for_kind(kind: str) -> list[tuple[str, str, int]]:
    return DEFAULT_AREA_LEVELS if kind == "area" else DEFAULT_OBJECT_LEVELS


def layer_order_for_kind(kind: str) -> list[str]:
    return AREA_LAYER_ORDER if kind == "area" else OBJECT_LAYER_ORDER


def tile_catalog_for_kind(kind: str) -> dict[str, list[TileDefinition]]:
    return AREA_TILE_CATALOG if kind == "area" else OBJECT_TILE_CATALOG


def serialize_catalog(kind: str | None = None) -> dict[str, list[dict[str, object]]]:
    catalog = {**AREA_TILE_CATALOG, **OBJECT_TILE_CATALOG} if kind is None else tile_catalog_for_kind(kind)
    return {
        layer_key: [{"code": item.code, "label": item.label, "color": item.color, "category": item.category} for item in items]
        for layer_key, items in catalog.items()
    }


def max_code_for_layer(layer_key: str, kind: str | None = None) -> int:
    catalog = {**AREA_TILE_CATALOG, **OBJECT_TILE_CATALOG} if kind is None else tile_catalog_for_kind(kind)
    return max(item.code for item in catalog[layer_key])


def ignition_threshold(layer_key: str, code: int) -> int:
    if code <= 0:
        return 10_000
    return IGNITION_THRESHOLDS.get((layer_key, code), 500)


def area_travel_cost(ground_code: int, objects_code: int, buildings_code: int) -> int | None:
    if buildings_code > 0:
        return None
    if objects_code in {1, 2, 4}:
        return None
    if ground_code == 1:
        return 1
    if ground_code == 2:
        return 2
    if ground_code in {5, 6}:
        return 2
    if ground_code in {3, 4}:
        return 3
    if ground_code == 7:
        return None
    return 3
