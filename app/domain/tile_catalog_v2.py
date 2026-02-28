from __future__ import annotations

from dataclasses import asdict, dataclass


DEFAULT_LAYER_ORDER = [
    "ground",
    "objects",
    "buildings",
    "effects_fire",
    "effects_smoke",
    "markers",
]

DEFAULT_AREA_LEVELS = [("AREA_MAIN", "Общая карта")]
DEFAULT_OBJECT_LEVELS = [
    ("B1", "Подвал"),
    ("F1", "Этаж 1"),
    ("F2", "Этаж 2"),
    ("F3", "Этаж 3"),
    ("ATTIC", "Чердак"),
    ("ROOF", "Крыша"),
]


@dataclass(frozen=True)
class TileDefinition:
    code: int
    label: str
    color: str
    category: str


TILE_CATALOG: dict[str, list[TileDefinition]] = {
    "ground": [
        TileDefinition(0, "Пусто", "#101820", "Покрытия"),
        TileDefinition(1, "Трава", "#5f9846", "Покрытия"),
        TileDefinition(2, "Грунт", "#8f6d45", "Покрытия"),
        TileDefinition(3, "Асфальт", "#3d434a", "Покрытия"),
        TileDefinition(4, "Бетон", "#a8b0b8", "Покрытия"),
        TileDefinition(5, "Плитка", "#8a8684", "Покрытия"),
        TileDefinition(6, "Песок", "#cfb872", "Покрытия"),
        TileDefinition(7, "Лес", "#2d5f2e", "Покрытия"),
    ],
    "objects": [
        TileDefinition(0, "Пусто", "transparent", "Объекты"),
        TileDefinition(1, "Забор", "#6b513d", "Преграды"),
        TileDefinition(2, "Стена", "#8c8f91", "Преграды"),
        TileDefinition(3, "Ворота", "#c9a227", "Преграды"),
        TileDefinition(4, "Шлагбаум", "#f6d365", "Преграды"),
        TileDefinition(5, "Гидрант", "#cc2936", "Вода"),
        TileDefinition(6, "Пожарный водоем", "#246eb9", "Вода"),
        TileDefinition(7, "Водоем", "#2d8fdd", "Вода"),
        TileDefinition(8, "Площадка забора воды", "#57b5ff", "Вода"),
    ],
    "buildings": [
        TileDefinition(0, "Пусто", "transparent", "Здания"),
        TileDefinition(1, "Частный дом", "#b16448", "Здания"),
        TileDefinition(2, "Многоэтажка", "#9f7157", "Здания"),
        TileDefinition(3, "Склад / ангар", "#7c5c45", "Здания"),
        TileDefinition(4, "Промышленное здание", "#5c5148", "Здания"),
        TileDefinition(5, "Внешняя стена", "#d8c3a5", "Контур"),
        TileDefinition(6, "Перегородка", "#eee2dc", "Контур"),
        TileDefinition(7, "Дверь", "#2a9d8f", "Контур"),
        TileDefinition(8, "Окно", "#7dd3fc", "Контур"),
        TileDefinition(9, "Лестница", "#ffb703", "Контур"),
    ],
    "effects_fire": [
        TileDefinition(0, "Пусто", "transparent", "Эффекты"),
        TileDefinition(1, "Очаг пожара", "#ff4d00", "Эффекты"),
        TileDefinition(2, "Подожжено", "#ff8c42", "Эффекты"),
        TileDefinition(3, "Горит", "#ffd166", "Эффекты"),
    ],
    "effects_smoke": [
        TileDefinition(0, "Пусто", "transparent", "Эффекты"),
        TileDefinition(1, "Легкий дым", "rgba(180,180,180,0.35)", "Эффекты"),
        TileDefinition(2, "Средний дым", "rgba(120,120,120,0.45)", "Эффекты"),
        TileDefinition(3, "Плотный дым", "rgba(70,70,70,0.65)", "Эффекты"),
    ],
    "markers": [
        TileDefinition(0, "Пусто", "transparent", "Тактика"),
        TileDefinition(1, "Точка техники", "#06d6a0", "Тактика"),
        TileDefinition(2, "Точка ствола", "#118ab2", "Тактика"),
        TileDefinition(3, "Очаг", "#ef476f", "Тактика"),
        TileDefinition(4, "Зона пожара", "#f78c6b", "Тактика"),
        TileDefinition(5, "Зона задымления", "#adb5bd", "Тактика"),
        TileDefinition(6, "Пострадавший", "#ffe66d", "Тактика"),
        TileDefinition(7, "Путь эвакуации", "#3a86ff", "Тактика"),
    ],
}


def serialize_catalog() -> dict[str, list[dict[str, object]]]:
    return {layer_key: [asdict(item) for item in items] for layer_key, items in TILE_CATALOG.items()}


def max_code_for_layer(layer_key: str) -> int:
    return max(item.code for item in TILE_CATALOG[layer_key])
