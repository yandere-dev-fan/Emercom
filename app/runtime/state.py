from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ManagedRuntimeSession:
    session_id: str
    session_code: str
    running: bool = True
    last_tick_at: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)
