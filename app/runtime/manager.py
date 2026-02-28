from __future__ import annotations

import asyncio

from app.db.models import TrainingSession
from app.runtime.state import ManagedRuntimeSession
from app.runtime.tick_loop import run_runtime_loop


class RuntimeManager:
    def __init__(self, ws_manager) -> None:
        self._ws_manager = ws_manager
        self._sessions: dict[str, ManagedRuntimeSession] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, training_session: TrainingSession) -> ManagedRuntimeSession:
        existing = self._sessions.get(training_session.id)
        if existing and existing.running:
            return existing
        managed = ManagedRuntimeSession(session_id=training_session.id, session_code=training_session.session_code)
        self._sessions[training_session.id] = managed
        self._tasks[training_session.id] = asyncio.create_task(
            run_runtime_loop(managed, ws_manager=self._ws_manager),
            name=f"runtime-loop-{training_session.session_code}",
        )
        return managed

    async def stop(self, session_id: str) -> None:
        managed = self._sessions.get(session_id)
        if managed is not None:
            managed.running = False
        task = self._tasks.pop(session_id, None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._sessions.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        managed = self._sessions.get(session_id)
        return bool(managed and managed.running)
