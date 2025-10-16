from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .session_manager import SessionManager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class SessionResponse(BaseModel):
    session_id: str


class AvatarOfferRequest(BaseModel):
    sdp: str


class AvatarAnswerResponse(BaseModel):
    sdp: str


class TextMessageRequest(BaseModel):
    text: str


class AudioCommitResponse(BaseModel):
    status: str


session_manager = SessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # pylint: disable=unused-argument
    try:
        yield
    finally:
        # ensure all sessions are cleaned up
        remaining = await session_manager.list_session_ids()
        await asyncio.gather(*[session_manager.remove_session(session_id) for session_id in remaining])


app = FastAPI(title="Azure Voice Live Avatar Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


async def _ensure_session(session_id: str):
    try:
        return await session_manager.get_session(session_id)
    except KeyError as exc:  # pylint: disable=raise-missing-from
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/sessions", response_model=SessionResponse)
async def create_session() -> SessionResponse:
    session = await session_manager.create_session()
    return SessionResponse(session_id=session.session_id)


@app.post("/sessions/{session_id}/avatar-offer", response_model=AvatarAnswerResponse)
async def handle_avatar_offer(session_id: str, request: AvatarOfferRequest) -> AvatarAnswerResponse:
    session = await _ensure_session(session_id)
    server_sdp = await session.connect_avatar(request.sdp)
    return AvatarAnswerResponse(sdp=server_sdp)


@app.post("/sessions/{session_id}/text")
async def send_text_message(session_id: str, request: TextMessageRequest) -> Dict[str, str]:
    session = await _ensure_session(session_id)
    await session.send_user_message(request.text)
    return {"status": "queued"}


@app.post("/sessions/{session_id}/commit-audio", response_model=AudioCommitResponse)
async def commit_audio(session_id: str) -> AudioCommitResponse:
    session = await _ensure_session(session_id)
    await session.commit_audio()
    return AudioCommitResponse(status="committed")


@app.websocket("/ws/sessions/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        session = await _ensure_session(session_id)
    except HTTPException:
        await websocket.close(code=4404)
        return

    queue = session.create_event_queue()

    async def emitter():
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            logger.info("Websocket emitter disconnect for session %s", session_id)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Emitter failed: %s", exc)

    emitter_task = asyncio.create_task(emitter())

    await websocket.send_json({"type": "session_ready", "session_id": session_id})

    try:
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")
            if msg_type == "audio_chunk":
                audio_data = message.get("data")
                encoding = message.get("encoding", "float32")
                await session.send_audio_chunk(audio_data, encoding=encoding)
            elif msg_type == "commit_audio":
                await session.commit_audio()
            elif msg_type == "clear_audio":
                await session.clear_audio()
            elif msg_type == "user_text":
                await session.send_user_message(message.get("text", ""))
            elif msg_type == "request_response":
                await session.request_response()
            else:
                logger.warning("Unknown WS message type: %s", msg_type)
    except WebSocketDisconnect:
        logger.info("Client disconnected from session %s", session_id)
    finally:
        emitter_task.cancel()
        session.remove_event_queue(queue)
