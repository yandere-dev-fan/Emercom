from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class CellWrite(BaseModel):
    index: int = Field(ge=0)
    value: int = Field(ge=0, le=65535)


class MapPatchChange(BaseModel):
    level_id: str = Field(min_length=1, max_length=36)
    layer_key: str = Field(min_length=1, max_length=32)
    writes: list[CellWrite] = Field(min_length=1)


class MapPatchRequest(BaseModel):
    base_version: int = Field(ge=1)
    client_event_id: str = Field(min_length=1, max_length=128)
    changes: list[MapPatchChange] = Field(min_length=1)


class SnapshotCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=128)


class LayerReorderRequest(BaseModel):
    level_id: str = Field(min_length=1, max_length=36)
    layer_key: str = Field(min_length=1, max_length=32)
    direction: str = Field(pattern="^(up|down)$")


class ObjectMapCreateRequest(BaseModel):
    source_level_id: str | None = Field(default=None, max_length=36)
    source_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_selection(self) -> "ObjectMapCreateRequest":
        if (self.source_level_id is None) != (self.source_index is None):
            raise ValueError("source_level_id and source_index must be provided together.")
        return self


class MapMetadataUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)


class MapCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern="^(area|object)$")
    width: int = Field(ge=1, le=256)
    height: int = Field(ge=1, le=256)
    cell_size_px: int = Field(ge=8, le=64)
    meters_per_cell: int = Field(ge=1, le=1000)
    map_type: str = Field(min_length=1, max_length=32)
    parent_map_id: str | None = Field(default=None, max_length=36)


class MapExportLevel(BaseModel):
    id: str
    code: str
    title: str
    floor_number: int = Field(ge=0, le=100)
    sort_order: int
    layers: dict[str, list[int]]


class MapExportDocument(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern="^(area|object)$")
    width: int = Field(ge=1, le=256)
    height: int = Field(ge=1, le=256)
    cell_size_px: int = Field(ge=8, le=64)
    meters_per_cell: int = Field(ge=1, le=1000)
    map_type: str = Field(min_length=1, max_length=32)
    parent_map_id: str | None = None
    version: int = Field(ge=1)
    levels: list[MapExportLevel] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_level_lengths(self) -> "MapExportDocument":
        expected = self.width * self.height
        for level in self.levels:
            for values in level.layers.values():
                if len(values) != expected:
                    raise ValueError("Layer payload does not match map dimensions.")
        return self


class ImportMapRequest(BaseModel):
    payload: MapExportDocument


class CreateSessionRequest(BaseModel):
    area_template_id: str = Field(min_length=1, max_length=36)
    object_template_id: str = Field(min_length=1, max_length=36)
    weather_kind: str = Field(pattern="^(clear|rain|snow|fog)$", default="clear")
    wind_direction_deg: int = Field(ge=0, le=359, default=0)
    wind_speed_level: int = Field(ge=0, le=5, default=1)
    time_of_day: str = Field(pattern="^(day|dusk|night)$", default="day")
    detection_radius_cells: int = Field(ge=1, le=10, default=2)
    incident_area_index: int = Field(ge=0, default=0)
    incident_object_level_code: str = Field(min_length=1, max_length=16, default="F1")
    incident_object_index: int = Field(ge=0, default=0)
    enabled_vehicle_types: list[str] = Field(min_length=1)
    display_name: str | None = Field(default=None, max_length=64)


class ParticipantRoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(dispatcher|rtp|observer|waiting)$")


class ScenarioEventCreateRequest(BaseModel):
    event_type: str = Field(
        pattern="^(wind_shift|water_source_failure|vehicle_breakdown|route_blocked|secondary_fire|collapse_warning|visibility_drop)$"
    )
    payload: dict[str, object] = Field(default_factory=dict)


class DispatcherIncidentMarkRequest(BaseModel):
    guess_index: int = Field(ge=0)


class DispatchOrderRequest(BaseModel):
    counts: dict[str, int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> "DispatchOrderRequest":
        if not any(value > 0 for value in self.counts.values()):
            raise ValueError("At least one vehicle count must be greater than zero.")
        for key, value in self.counts.items():
            if value < 0:
                raise ValueError(f"Vehicle count for {key} cannot be negative.")
        return self


class VehicleRoutePoint(BaseModel):
    x: float
    y: float


class VehicleRouteRequest(BaseModel):
    points: list[VehicleRoutePoint] = Field(min_length=1)


class VehicleDriveIntentRequest(BaseModel):
    heading_deg: int = Field(ge=0, le=359)
    speed_mps: float = Field(ge=0.0, le=50.0)


class HoseCreateRequest(BaseModel):
    source_vehicle_id: str = Field(min_length=1, max_length=36)
    polyline_points: list[VehicleRoutePoint] = Field(min_length=2)


class HoseUpdateRequest(BaseModel):
    polyline_points: list[VehicleRoutePoint] | None = None
    flow_state: str | None = Field(default=None, pattern="^(dry|charged|active|invalid)$")


class NozzleCreateRequest(BaseModel):
    hose_id: str = Field(min_length=1, max_length=64)
    target_x: float
    target_y: float
    flow_lps: float = Field(ge=0.0, le=50.0, default=5.0)


class NozzleUpdateRequest(BaseModel):
    target_x: float | None = None
    target_y: float | None = None
    flow_lps: float | None = Field(default=None, ge=0.0, le=50.0)


class SessionJoinRequest(BaseModel):
    join_key: str = Field(min_length=16, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("join_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip()


class TemplateLevelCreateRequest(BaseModel):
    pass


class VehicleObjectStepRequest(BaseModel):
    direction: str = Field(pattern="^(up|down|left|right)$")


class ChatMessageCreateRequest(BaseModel):
    thread_key: str = Field(pattern="^(instructor_dispatcher|dispatcher_rtp|system)$")
    body: str = Field(min_length=1, max_length=2000)
