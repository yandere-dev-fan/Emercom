from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class TrainingSession(Base):
    __tablename__ = "training_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    join_key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    participants: Mapped[list["Participant"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")
    auth_sessions: Mapped[list["AuthSession"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")
    maps: Mapped[list["MapDocument"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")
    scenario_state: Mapped["ScenarioState | None"] = relationship(
        back_populates="training_session",
        cascade="all, delete-orphan",
        uselist=False,
    )
    vehicles: Mapped[list["VehicleInstance"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")
    runtime_events: Mapped[list["RuntimeEvent"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="training_session", cascade="all, delete-orphan")


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    training_session_id: Mapped[str] = mapped_column(ForeignKey("training_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="participants")
    auth_sessions: Mapped[list["AuthSession"]] = relationship(back_populates="participant", cascade="all, delete-orphan")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="participant")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="participant")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    training_session_id: Mapped[str] = mapped_column(ForeignKey("training_sessions.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="auth_sessions")
    participant: Mapped["Participant"] = relationship(back_populates="auth_sessions")


class MapDocument(Base):
    __tablename__ = "map_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("training_sessions.id"), nullable=True, index=True)
    parent_map_id: Mapped[str | None] = mapped_column(ForeignKey("map_documents.id"), nullable=True)
    source_template_id: Mapped[str | None] = mapped_column(ForeignKey("map_documents.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="session")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    cell_size_px: Mapped[int] = mapped_column(Integer, nullable=False)
    meters_per_cell: Mapped[int] = mapped_column(Integer, nullable=False)
    map_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    training_session: Mapped["TrainingSession | None"] = relationship(back_populates="maps", foreign_keys=[session_id])
    parent_map: Mapped["MapDocument | None"] = relationship(remote_side=[id], backref="object_maps", foreign_keys=[parent_map_id])
    source_template: Mapped["MapDocument | None"] = relationship(remote_side=[id], backref="runtime_copies", foreign_keys=[source_template_id])
    levels: Mapped[list["MapLevel"]] = relationship(back_populates="map_document", cascade="all, delete-orphan", order_by="MapLevel.sort_order")
    snapshots: Mapped[list["MapSnapshot"]] = relationship(back_populates="map_document", cascade="all, delete-orphan")
    import_jobs: Mapped[list["ImportJob"]] = relationship(back_populates="map_document", cascade="all, delete-orphan")


class MapLevel(Base):
    __tablename__ = "map_levels"
    __table_args__ = (UniqueConstraint("map_id", "code", name="uq_map_level_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    map_id: Mapped[str] = mapped_column(ForeignKey("map_documents.id"), index=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    map_document: Mapped["MapDocument"] = relationship(back_populates="levels")
    layers: Mapped[list["MapLayer"]] = relationship(back_populates="map_level", cascade="all, delete-orphan", order_by="MapLayer.z_index")


class MapLayer(Base):
    __tablename__ = "map_layers"
    __table_args__ = (UniqueConstraint("level_id", "layer_key", name="uq_level_layer"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    level_id: Mapped[str] = mapped_column(ForeignKey("map_levels.id"), index=True)
    layer_key: Mapped[str] = mapped_column(String(32), nullable=False)
    z_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_visible_default: Mapped[bool] = mapped_column(Boolean, default=True)
    is_locked_default: Mapped[bool] = mapped_column(Boolean, default=False)
    encoding: Mapped[str] = mapped_column(String(32), default="uint16-zlib")
    cells_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    max_code: Mapped[int] = mapped_column(Integer, nullable=False)

    map_level: Mapped["MapLevel"] = relationship(back_populates="layers")


class MapSnapshot(Base):
    __tablename__ = "map_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    map_id: Mapped[str] = mapped_column(ForeignKey("map_documents.id"), index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    map_document: Mapped["MapDocument"] = relationship(back_populates="snapshots")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("training_sessions.id"), nullable=True, index=True)
    participant_id: Mapped[str | None] = mapped_column(ForeignKey("participants.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    training_session: Mapped["TrainingSession | None"] = relationship(back_populates="audit_events")
    participant: Mapped["Participant | None"] = relationship(back_populates="audit_events")


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    map_id: Mapped[str | None] = mapped_column(ForeignKey("map_documents.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    map_document: Mapped["MapDocument | None"] = relationship(back_populates="import_jobs")


class ScenarioState(Base):
    __tablename__ = "scenario_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("training_sessions.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="setup")
    area_map_id: Mapped[str] = mapped_column(ForeignKey("map_documents.id"), nullable=False)
    object_map_id: Mapped[str] = mapped_column(ForeignKey("map_documents.id"), nullable=False)
    time_elapsed_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weather_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="clear")
    wind_direction_deg: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wind_speed_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    time_of_day: Mapped[str] = mapped_column(String(16), nullable=False, default="day")
    incident_area_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incident_object_level_code: Mapped[str] = mapped_column(String(16), nullable=False, default="F1")
    incident_object_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incident_revealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dispatcher_guess_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dispatcher_guess_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detection_radius_cells: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    available_vehicles_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    runtime_state_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="scenario_state")
    area_map: Mapped["MapDocument"] = relationship(foreign_keys=[area_map_id])
    object_map: Mapped["MapDocument"] = relationship(foreign_keys=[object_map_id])
    vehicles: Mapped[list["VehicleInstance"]] = relationship(back_populates="scenario_state", cascade="all, delete-orphan")
    runtime_events: Mapped[list["RuntimeEvent"]] = relationship(back_populates="scenario_state", cascade="all, delete-orphan")


class HostAdminSession(Base):
    __tablename__ = "host_admin_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VehicleInstance(Base):
    __tablename__ = "vehicle_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("training_sessions.id"), index=True)
    scenario_state_id: Mapped[str] = mapped_column(ForeignKey("scenario_states.id"), index=True)
    vehicle_type: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="staged")
    current_map_id: Mapped[str] = mapped_column(ForeignKey("map_documents.id"), nullable=False)
    current_level_code: Mapped[str] = mapped_column(String(16), nullable=False, default="AREA_MAIN")
    position_x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    position_y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    heading_deg: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    speed_mps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    water_remaining_l: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    foam_remaining_l: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    route_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="vehicles")
    scenario_state: Mapped["ScenarioState"] = relationship(back_populates="vehicles")
    current_map: Mapped["MapDocument"] = relationship(foreign_keys=[current_map_id])


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("training_sessions.id"), index=True)
    scenario_state_id: Mapped[str] = mapped_column(ForeignKey("scenario_states.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_by_participant_id: Mapped[str | None] = mapped_column(ForeignKey("participants.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="runtime_events")
    scenario_state: Mapped["ScenarioState"] = relationship(back_populates="runtime_events")
    created_by_participant: Mapped["Participant | None"] = relationship(foreign_keys=[created_by_participant_id])


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("training_sessions.id"), index=True)
    participant_id: Mapped[str | None] = mapped_column(ForeignKey("participants.id"), nullable=True, index=True)
    thread_key: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="chat_messages")
    participant: Mapped["Participant | None"] = relationship(back_populates="chat_messages")
