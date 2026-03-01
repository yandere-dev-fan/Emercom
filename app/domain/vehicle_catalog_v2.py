from __future__ import annotations


VEHICLE_CATALOG: dict[str, dict[str, object]] = {
    "FIRE_ENGINE": {
        "vehicle_type": "FIRE_ENGINE",
        "display_name": "Пожарная машина",
        "max_speed_kph": 60,
        "cells_per_minute": 60.0,
        "water_capacity_l": 3200,
        "foam_capacity_l": 180,
        "pump_flow_lps": 40,
        "road_width_requirement": 2,
        "offroad_penalty": 1.5,
        "can_access_upper_levels": False,
        "turn_penalty_class": 1,
    },
    "LADDER_ENGINE": {
        "vehicle_type": "LADDER_ENGINE",
        "display_name": "Пожарная машина с лестницей",
        "max_speed_kph": 45,
        "cells_per_minute": 45.0,
        "water_capacity_l": 2500,
        "foam_capacity_l": 150,
        "pump_flow_lps": 35,
        "road_width_requirement": 2,
        "offroad_penalty": 1.8,
        "can_access_upper_levels": True,
        "turn_penalty_class": 2,
    },
}
