from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional, Set

import websockets  # type: ignore[import]
from azure.identity import DefaultAzureCredential
from websockets import WebSocketClientProtocol  # type: ignore[import]

try:
    from websockets.protocol import State as WebSocketState  # type: ignore[import]
except ImportError:  # pragma: no cover - older websockets versions
    WebSocketState = None  # type: ignore[assignment]

from .audio_utils import float_frame_base64_to_pcm16_base64
from .tools import AVAILABLE_FUNCTIONS, TOOLS_LIST
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Ensure .env from backend root is loaded when module is imported
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

SYSTEM_INSTRUCTIONS = """
You are an AI Agent tasked with responding to questions from the customers of Contoso retail fashions regarding their shopping requirements. 
When the customer starts the conversation with a greeting, reciprocate as you respond to their queries. 
Refer to the context provided to you from the Contoso retail knowledge base to respond to their queries.
**DO NOT RESPOND BASED ON YOUR PERSONAL OPINIONS OR EXPERIENCES**
You do not have to say anything about files uploaded, etc to the user.
You have access to the following tools and knowledge. Use these to get context to respond to the user queries:
- API to search for products by category
    - These are the distinct category names for which sample data is available in the Contoso eCom APIs:
        > Apparel, Garments, Winter wear, Stockings, Active wear, Swim wear, Formal wear, Accessories
    - When the user query provides a category name in the request and asks for products in that category, the category name you pass to the API must be one of the above
    - If the user asks you the available categories, provide them the above list.
    - When you get the product search results, present them to the user in a numbered list format, with product name, price and description
- API to order a product based on product id and quantity
    - Always reconfirm with all the particulars (product details, quantities, prices, total amount) before creating the order. Wait for explicit user confirmation before proceeding.
    - In your response to the user, provide the order details including order id, product details, quantities, prices, total amount in a numbered list format
- APIs to create shipment orders
    - When creating the shipment order, always seek confirmation from the user about the destination address provided before creating it. Repeat back the complete address and ask for confirmation before proceeding.
    - When you get the shipment order response, provide the user with all the shipment order details in a numbered list format
- Search tool to perform a QnA on general Contoso retail policies, procedures and QnA
- APIs to analyze call logs.
    - When the user indicates there are no more questions, and wants to end the conversation, you must implicitly call this API to log the entire conversation for analysis. The user does not need to know you are doing this.
    - **You do not need to send the results of the analysis back to the user. You could just say the conversation has been logged for analysis.**
    - The data you send this API for call log analysis should be the full conversation between the customer and you and should be like:
        ###### Example Conversation History ###### 
        {
        "conversation": [
            {
            "role": "user",
            "message": "user input"
            },
            {
            "role": "assistant",
            "message": "agent response"
            },

            {
            "role": "user",
            "message": "user next question?"
            },
            {
            "role": "assistant",
            "message": "agent's next response "
            },
        ..... and so on ..... 
            ]
        }
        ###### End Example Conversation History###### 

Important confirmation requirements:
**Empathize with the customer when you respond**
**Remember that your persona is that of a woman. When you speak to the customer in Hindi, mind your gender when you respond**
"""


class VoiceLiveSession:
    """Manage a single Voice Live realtime session and broadcast events to subscribers."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.ws: Optional[WebSocketClientProtocol] = None
        self._listeners: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._receive_task: Optional[asyncio.Task] = None
        self._avatar_future: Optional[asyncio.Future] = None
        self._connected_event = asyncio.Event()

        endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
        model = os.getenv("VOICE_LIVE_MODEL")
        if not endpoint or not model:
            raise RuntimeError("AZURE_VOICE_LIVE_ENDPOINT and VOICE_LIVE_MODEL must be set")
        self._endpoint = endpoint
        self._model = model
        self._api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
        self._api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self._use_api_key = bool(self._api_key)

        self._session_config = {
            "modalities": ["text", "audio", "avatar", "animation"],
            "input_audio_sampling_rate": 24000,
            "instructions": SYSTEM_INSTRUCTIONS,
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
            },
            "tools": TOOLS_LIST,
            "tool_choice": "auto",
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "voice": {
                "name": os.getenv("AZURE_TTS_VOICE", "en-IN-AartiIndicNeural"),
                "type": "azure-standard",
                "temperature": 0.8,
            },
            "input_audio_transcription": {"model": "whisper-1"},
            "avatar": self._build_avatar_config(),
            "animation": {"model_name": "default", "outputs": ["blendshapes", "viseme_id"]},
        }
        self._response_config = {
            "modalities": ["text", "audio"],
        }

    def _ws_is_open(self) -> bool:
        ws = self.ws
        if ws is None:
            return False
        state = getattr(ws, "state", None)
        if state is not None:
            if WebSocketState is not None:
                try:
                    if state == WebSocketState.OPEN:
                        return True
                    if state in {WebSocketState.CLOSING, WebSocketState.CLOSED}:
                        return False
                except TypeError:
                    pass
            state_name = getattr(state, "name", None)
            if isinstance(state_name, str):
                if state_name.upper() == "OPEN":
                    return True
                if state_name.upper() in {"CLOSING", "CLOSED"}:
                    return False
        open_attr = getattr(ws, "open", None)
        if isinstance(open_attr, bool):
            return open_attr
        if callable(open_attr):
            try:
                return bool(open_attr())
            except TypeError:
                pass
        closed_attr = getattr(ws, "closed", None)
        if isinstance(closed_attr, bool):
            return not closed_attr
        if callable(closed_attr):
            try:
                return not bool(closed_attr())
            except TypeError:
                pass
        close_code = getattr(ws, "close_code", None)
        return close_code is None

    async def _ensure_connection(self) -> None:
        if self._ws_is_open():
            return
        await self.connect()
        if not self._ws_is_open():
            raise RuntimeError("Session websocket is not connected")
            return False
        open_attr = getattr(self.ws, "open", None)
        if isinstance(open_attr, bool):
            return open_attr
        closed_attr = getattr(self.ws, "closed", None)
        if isinstance(closed_attr, bool):
            return not closed_attr
        if callable(closed_attr):  # type: ignore[call-overload]
            try:
                return not closed_attr()
            except TypeError:
                pass
        close_code = getattr(self.ws, "close_code", None)
        return close_code is None

    def _build_avatar_config(self) -> Dict[str, Any]:
        character = os.getenv("AZURE_VOICE_AVATAR_CHARACTER", "lisa")
        style = os.getenv("AZURE_VOICE_AVATAR_STYLE")
        video_width = int(os.getenv("AZURE_VOICE_AVATAR_WIDTH", "1280"))
        video_height = int(os.getenv("AZURE_VOICE_AVATAR_HEIGHT", "720"))
        bitrate = int(os.getenv("AZURE_VOICE_AVATAR_BITRATE", "2000000"))
        config: Dict[str, Any] = {
            "character": character,
            "customized": False,
            "video": {"resolution": {"width": video_width, "height": video_height}, "bitrate": bitrate},
        }
        if style:
            config["style"] = style
        ice_urls = os.getenv("AZURE_VOICE_AVATAR_ICE_URLS")
        if ice_urls:
            config["ice_servers"] = [
                {"urls": [url.strip() for url in ice_urls.split(",") if url.strip()]}
            ]
        return config

    async def connect(self) -> None:
        async with self._lock:
            if self._ws_is_open():
                return
            headers = {"x-ms-client-request-id": str(uuid.uuid4())}
            if self._use_api_key:
                ws_url = self._build_ws_url()
                headers["api-key"] = self._api_key  # Azure OpenAI key
            else:
                token = await self._get_token()
                ws_url = self._build_ws_url(token)
                headers["Authorization"] = f"Bearer {token}"
            self.ws = await websockets.connect(ws_url, additional_headers=headers)
            logger.info("[%s] Connected to Azure Voice Live", self.session_id)
            self._receive_task = asyncio.create_task(self._receive_loop())
            await self._send("session.update", {"session": self._session_config}, allow_reconnect=False)
            self._connected_event.set()

    async def disconnect(self) -> None:
        async with self._lock:
            if self._ws_is_open():
                await self.ws.close()
            if self._receive_task:
                self._receive_task.cancel()
            self.ws = None
            self._connected_event.clear()
            logger.info("[%s] Disconnected session", self.session_id)

    async def _get_token(self) -> str:
        credential = DefaultAzureCredential()
        scope = "https://ai.azure.com/.default"
        token = await asyncio.get_event_loop().run_in_executor(None, credential.get_token, scope)
        return token.token

    def _build_ws_url(self, agent_token: Optional[str] = None) -> str:
        azure_ws_endpoint = self._endpoint.rstrip("/").replace("https://", "wss://")
        base = f"{azure_ws_endpoint}/voice-live/realtime?api-version={self._api_version}&model={self._model}"
        if agent_token:
            return f"{base}&agent-access-token={agent_token}"
        return base

    async def _send(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        *,
        allow_reconnect: bool = True,
    ) -> None:
        if not self._ws_is_open():
            if allow_reconnect:
                await self.connect()
            if not self._ws_is_open():
                raise RuntimeError("Session websocket is not connected")
        if not self.ws:
            raise RuntimeError("Session websocket is not connected")
        payload = {"event_id": self._generate_id("evt_"), "type": event_type}
        if data:
            payload.update(data)
        await self.ws.send(json.dumps(payload))

    @staticmethod
    def _generate_id(prefix: str) -> str:
        return f"{prefix}{int(dt.datetime.utcnow().timestamp() * 1000)}"

    @staticmethod
    def _encode_client_sdp(client_sdp: str) -> str:
        payload = json.dumps({"type": "offer", "sdp": client_sdp})
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")

    @staticmethod
    def _decode_server_sdp(server_sdp_raw: Optional[str]) -> Optional[str]:
        if not server_sdp_raw:
            return None
        if server_sdp_raw.startswith("v=0"):
            return server_sdp_raw
        try:
            decoded_bytes = base64.b64decode(server_sdp_raw)
        except Exception:
            return server_sdp_raw
        try:
            decoded_text = decoded_bytes.decode("utf-8")
        except Exception:
            return server_sdp_raw
        try:
            payload = json.loads(decoded_text)
        except json.JSONDecodeError:
            return decoded_text
        if isinstance(payload, dict):
            sdp_value = payload.get("sdp")
            if isinstance(sdp_value, str) and sdp_value:
                return sdp_value
        return decoded_text

    def create_event_queue(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._listeners.add(queue)
        return queue

    def remove_event_queue(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    async def _broadcast(self, event: Dict[str, Any]) -> None:
        if not self._listeners:
            return
        for queue in list(self._listeners):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("[%s] Dropping event %s due to slow consumer", self.session_id, event.get("type"))

    async def send_user_message(self, text: str) -> None:
        await self._connected_event.wait()
        await self._ensure_connection()
        await self._send(
            "conversation.item.create",
            {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                }
            },
        )
        await self._send("response.create", {"response": self._response_config})

    async def send_audio_chunk(self, audio_b64: str, encoding: str = "float32") -> None:
        await self._connected_event.wait()
        await self._ensure_connection()
        if encoding == "float32":
            pcm_b64 = float_frame_base64_to_pcm16_base64(audio_b64)
        else:
            pcm_b64 = audio_b64
        await self._send("input_audio_buffer.append", {"audio": pcm_b64})

    async def commit_audio(self) -> None:
        await self._connected_event.wait()
        await self._ensure_connection()
        await self._send("input_audio_buffer.commit")

    async def clear_audio(self) -> None:
        await self._connected_event.wait()
        await self._ensure_connection()
        await self._send("input_audio_buffer.clear")

    async def request_response(self) -> None:
        await self._connected_event.wait()
        await self._ensure_connection()
        await self._send("response.create", {"response": self._response_config})

    async def connect_avatar(self, client_sdp: str) -> str:
        await self._connected_event.wait()
        await self._ensure_connection()
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._avatar_future = future
        encoded_sdp = self._encode_client_sdp(client_sdp)
        payload = {
            "client_sdp": encoded_sdp,
            "rtc_configuration": {"bundle_policy": "max-bundle"},
        }
        await self._send("session.avatar.connect", payload)
        try:
            server_sdp = await asyncio.wait_for(future, timeout=20)
            return server_sdp
        finally:
            self._avatar_future = None

    async def _receive_loop(self) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            async for message in ws:
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("[%s] Failed to decode message", self.session_id)
                    continue
                event_type = event.get("type")
                if event_type == "error":
                    await self._broadcast({"type": "error", "payload": event})
                elif event_type == "response.audio.delta":
                    await self._broadcast({"type": "assistant_audio_delta", "delta": event.get("delta")})
                elif event_type == "response.audio.done":
                    await self._broadcast({"type": "assistant_audio_done", "payload": event})
                elif event_type == "response.audio_transcript.delta":
                    await self._broadcast(
                        {
                            "type": "assistant_transcript_delta",
                            "delta": event.get("delta"),
                            "item_id": event.get("item_id"),
                        }
                    )
                elif event_type == "response.audio_transcript.done":
                    await self._broadcast(
                        {
                            "type": "assistant_transcript_done",
                            "transcript": event.get("transcript"),
                            "item_id": event.get("item_id"),
                        }
                    )
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    await self._broadcast(
                        {
                            "type": "user_transcript_completed",
                            "transcript": event.get("transcript"),
                            "item_id": event.get("item_id"),
                        }
                    )
                elif event_type == "input_audio_buffer.speech_started":
                    await self._broadcast({"type": "speech_started"})
                elif event_type == "input_audio_buffer.speech_stopped":
                    await self._broadcast({"type": "speech_stopped"})
                elif event_type == "input_audio_buffer.committed":
                    await self._broadcast({"type": "input_audio_committed"})
                elif event_type == "session.avatar.connecting":
                    server_sdp = event.get("server_sdp")
                    decoded_sdp = self._decode_server_sdp(server_sdp)
                    if self._avatar_future and not self._avatar_future.done():
                        if decoded_sdp is None:
                            self._avatar_future.set_exception(RuntimeError("Empty server SDP"))
                        else:
                            self._avatar_future.set_result(decoded_sdp)
                    await self._broadcast({"type": "avatar_connecting"})
                elif event_type == "response.done":
                    await self._handle_response_done(event)
                else:
                    await self._broadcast({"type": "event", "payload": event})
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("[%s] Azure Voice Live websocket receive loop ended with error", self.session_id)
            await self._broadcast({"type": "error", "payload": {"message": str(exc)}})
        finally:
            if self.ws is ws:
                self.ws = None
            logger.info("[%s] Azure Voice Live websocket closed", self.session_id)

    async def _handle_response_done(self, event: Dict[str, Any]) -> None:
        response = event.get("response", {})
        status = response.get("status")
        if status != "completed":
            await self._broadcast({"type": "response_status", "status": status})
            return
        output_items = response.get("output", [])
        if not output_items:
            return
        first_item = output_items[0]
        if first_item.get("type") != "function_call":
            return
        function_name = first_item.get("name")
        arguments = json.loads(first_item.get("arguments", "{}"))
        call_id = first_item.get("call_id")
        logger.info("[%s] Function call requested: %s", self.session_id, function_name)
        func = AVAILABLE_FUNCTIONS.get(function_name)
        if not func:
            logger.error("Function %s is not registered", function_name)
            return
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: func(**arguments))
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Function %s failed", function_name)
            result = json.dumps({"error": str(exc)})
        if not isinstance(result, str):
            result_payload = json.dumps(result)
        else:
            result_payload = result
        await self._send(
            "conversation.item.create",
            {
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result_payload,
                }
            },
        )
        await self._send("response.create", {"response": self._response_config})
        await self._broadcast({"type": "function_call_completed", "name": function_name})
