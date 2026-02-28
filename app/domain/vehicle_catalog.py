from __future__ import annotations


VEHICLE_CATALOG: dict[str, dict[str, object]] = {
    "AC": {
        "vehicle_type": "AC",
        "display_name": "Автоцистерна",
        "max_speed_kph": 60,
        "water_capacity_l": 3200,
        "foam_capacity_l": 180,
        "pump_flow_lps": 40,
        "road_width_requirement": 2,
        "offroad_penalty": 1.5,
        "can_access_upper_levels": False,
        "turn_penalty_class": 1,
    },
    "APP": {
        "vehicle_type": "APP",
        "display_name": "Автомобиль первой помощи",
        "max_speed_kph": 72,
        "water_capacity_l": 1000,
        "foam_capacity_l": 90,
        "pump_flow_lps": 20,
        "road_width_requirement": 1,
        "offroad_penalty": 1.2,
        "can_access_upper_levels": False,
        "turn_penalty_class": 0,
    },
    "ACL": {
        "vehicle_type": "ACL",
        "display_name": "Автоцистерна с лестницей",
        "max_speed_kph": 48,
        "water_capacity_l": 2500,
        "foam_capacity_l": 150,
        "pump_flow_lps": 35,
        "road_width_requirement": 2,
        "offroad_penalty": 1.8,
        "can_access_upper_levels": True,
        "turn_penalty_class": 2,
    },
}
