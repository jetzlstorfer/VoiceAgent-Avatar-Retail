from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict

from .voice_live_client import VoiceLiveSession

logger = logging.getLogger(__name__)


class SessionManager:
    """Creates and tracks live sessions for each connected browser client."""

    def __init__(self) -> None:
        self._sessions: Dict[str, VoiceLiveSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(self) -> VoiceLiveSession:
        session_id = str(uuid.uuid4())
        session = VoiceLiveSession(session_id)
        await session.connect()
        async with self._lock:
            self._sessions[session_id] = session
        logger.info("Created Voice Live session %s", session_id)
        return session

    async def get_session(self, session_id: str) -> VoiceLiveSession:
        async with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Session {session_id} not found")
            return self._sessions[session_id]

    async def list_session_ids(self) -> list[str]:
        async with self._lock:
            return list(self._sessions.keys())

    async def remove_session(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            await session.disconnect()
            logger.info("Removed session %s", session_id)
