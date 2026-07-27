from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from .voice_live_client import VoiceLiveSession

logger = logging.getLogger(__name__)

_SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))  # 30 min default
_REAPER_INTERVAL_SECONDS = int(os.getenv("SESSION_REAPER_INTERVAL_SECONDS", "60"))


class SessionManager:
    """Creates and tracks live sessions for each connected browser client."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[VoiceLiveSession, float]] = {}  # session_id -> (session, last_activity)
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    async def create_session(self, avatar_enabled: bool = False, language: str = "en") -> VoiceLiveSession:
        session_id = str(uuid.uuid4())
        session = VoiceLiveSession(session_id, avatar_enabled=avatar_enabled, language=language)
        await session.connect()
        async with self._lock:
            self._sessions[session_id] = (session, time.monotonic())
        logger.info("Created Voice Live session %s", session_id)
        return session

    async def get_session(self, session_id: str) -> VoiceLiveSession:
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                raise KeyError(f"Session {session_id} not found")
            session, _ = entry
            self._sessions[session_id] = (session, time.monotonic())
            return session

    async def touch_session(self, session_id: str) -> None:
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry:
                self._sessions[session_id] = (entry[0], time.monotonic())

    async def list_session_ids(self) -> list[str]:
        async with self._lock:
            return list(self._sessions.keys())

    async def active_session_count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def remove_session(self, session_id: str) -> None:
        async with self._lock:
            entry = self._sessions.pop(session_id, None)
        if entry:
            await entry[0].disconnect()
            logger.info("Removed session %s", session_id)

    # ── Idle session reaper ──

    def start_reaper(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())
            logger.info("Session reaper started (ttl=%ds, interval=%ds)", _SESSION_TTL_SECONDS, _REAPER_INTERVAL_SECONDS)

    def stop_reaper(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
            self._reaper_task = None

    async def _reaper_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_REAPER_INTERVAL_SECONDS)
                await self._reap_idle_sessions()
        except asyncio.CancelledError:
            pass

    async def _reap_idle_sessions(self) -> None:
        now = time.monotonic()
        stale_ids: list[str] = []
        async with self._lock:
            for sid, (_, last_active) in self._sessions.items():
                if now - last_active > _SESSION_TTL_SECONDS:
                    stale_ids.append(sid)
        for sid in stale_ids:
            logger.info("Reaping idle session %s", sid)
            await self.remove_session(sid)
