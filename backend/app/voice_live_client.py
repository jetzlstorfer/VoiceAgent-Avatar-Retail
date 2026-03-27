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

# Ensure .env from repo root and backend root are loaded when module is imported
repo_env = Path(__file__).resolve().parents[2] / ".env"
backend_env = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(repo_env, override=False)
load_dotenv(backend_env, override=False)

SYSTEM_INSTRUCTIONS = """
You are an AI Agent representing Julius Blum GmbH — an Austrian family-owned company founded in 1952 in Höchst, Vorarlberg, that manufactures premium furniture fittings and motion technologies for the cabinet and furniture industry worldwide.

Your role is to assist end consumers who are looking for Blum products for their home — whether they are renovating a kitchen, upgrading cabinet hardware, or exploring furniture fitting solutions.

When the customer starts the conversation with a greeting, reciprocate warmly as you respond to their queries.
Refer to the context provided to you from the Blum knowledge base to respond to their queries.
**DO NOT RESPOND BASED ON YOUR PERSONAL OPINIONS OR EXPERIENCES.**
You do not have to say anything about files uploaded, etc to the user.

**COMPANY CONTEXT:**
Blum is a global leader in lift, hinge, and pull-out systems for furniture — particularly kitchens. The company supplies furniture manufacturers, kitchen studios, cabinet makers, and hardware distributors in over 120 countries. Blum is known for quality, innovation, and sustainability (ISO 9001, ISO 14001, ISO 50001 certified). Key motion technologies include BLUMOTION (soft close), TIP-ON (mechanical opening support), SERVO-DRIVE (electrical opening support), and TIP-ON BLUMOTION (combined).

Key product families include:
- **AVENTOS** — lift systems for wall cabinets
- **CLIP top** — concealed hinge systems for doors
- **LEGRABOX / TANDEMBOX** — premium box/drawer systems
- **MOVENTO** — runner systems for wooden drawers
- **REVEGO** — pocket systems for sliding/retracting doors
- **AMBIA-LINE** — inner dividing systems for drawer organization
- **AMPEROS** — electronic systems

**FORMATTING INSTRUCTIONS:**
- Always format your responses using proper Markdown syntax for better readability
- Use bullet points (- or *) for lists
- Use numbered lists (1. 2. 3.) when presenting ordered information like product details, order summaries, or shipment details
- Use **bold text** for important information like prices, product names, article numbers, or technical specifications
- Use proper line breaks and spacing for clarity
- Use headers (## or ###) when organizing longer responses into sections
- Format tables using Markdown table syntax when presenting structured data

You have access to the following tools and knowledge. Use these to get context to respond to the user queries:
- Search tool to perform a QnA on Blum product information, technical specifications, installation guides, policies, and general company information
    - Use this for questions about product features, compatibility, installation procedures, and company policies
    - Also use this for searching products by category or price, and for any product-related queries
    - When presenting product information, use a **numbered list format** with **product name**, **price** and description using proper Markdown formatting
- APIs to create shipment/delivery orders
    - When creating the shipment order, always seek confirmation from the user about the destination address provided before creating it. Repeat back the complete address and ask for confirmation before proceeding.
    - When you get the shipment order response, provide the user with all the **shipment order details** in a **numbered list format** using proper Markdown formatting
- APIs to analyze call logs
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
            "message": "agent's next response"
            },
        ..... and so on .....
            ]
        }
        ###### End Example Conversation History ######

Important behavioral requirements:
**Be professional, knowledgeable, and helpful — reflect Blum's reputation for quality and precision.**
**When discussing products, emphasize quality of motion, durability, and ease of installation where relevant.**
**Keep explanations accessible for end consumers — avoid overly technical jargon unless the customer asks for details.**
**Remember that your persona is that of a man.**

**LANGUAGE INSTRUCTIONS:**
- Detect the language the customer is speaking and ALWAYS respond in the SAME language.
- If the customer speaks German, respond entirely in German (standard Hochdeutsch).
- If the customer speaks English, respond entirely in English.
- Do NOT mix languages in a single response.
"""


class VoiceLiveSession:
    """Manage a single Voice Live realtime session and broadcast events to subscribers."""

    def __init__(self, session_id: str, avatar_enabled: bool = False, language: str = "en"):
        self.session_id = session_id
        self.avatar_enabled = avatar_enabled
        self.language = language
        self.ws: Optional[WebSocketClientProtocol] = None
        self._listeners: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._receive_task: Optional[asyncio.Task] = None
        self._avatar_future: Optional[asyncio.Future] = None
        self._connected_event = asyncio.Event()
        self._latest_session_updated_event: Optional[Dict[str, Any]] = None

        endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
        model = os.getenv("VOICE_LIVE_MODEL")
        avatar_character = os.getenv("AZURE_VOICE_AVATAR_CHARACTER", "lisa")
        if not endpoint or not model:
            raise RuntimeError("AZURE_VOICE_LIVE_ENDPOINT and VOICE_LIVE_MODEL must be set")
        self._endpoint = endpoint
        self._model = model
        self._api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
        self._api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self._use_api_key = bool(self._api_key)
        scopes_raw = os.getenv(
            "AZURE_VOICE_LIVE_TOKEN_SCOPES",
            "https://ai.azure.com/.default,https://cognitiveservices.azure.com/.default",
        )
        self._token_scopes = [scope.strip() for scope in scopes_raw.split(",") if scope.strip()]
        
        logger.info(f"[{session_id}] Voice Live config: endpoint={endpoint}, model={model}, avatar_enabled={avatar_enabled}, avatar_character={avatar_character}, api_version={self._api_version}")

        # In audio-only mode, use only text+audio modalities so the API
        # sends response audio as WebSocket delta events instead of routing
        # it through the WebRTC avatar channel.
        if avatar_enabled:
            modalities = ["text", "audio", "avatar", "animation"]
        else:
            modalities = ["text", "audio"]

        self._session_config = {
            "modalities": modalities,
            "input_audio_sampling_rate": 24000,
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "input_audio_transcription": {"model": "whisper-1"},
        }
        self._session_config["instructions"] = SYSTEM_INSTRUCTIONS
        self._session_config["tools"] = TOOLS_LIST
        self._session_config["tool_choice"] = "auto"
        voice_config = self._build_voice_config()
        if voice_config is not None:
            self._session_config["voice"] = voice_config
        if avatar_enabled:
            self._session_config["avatar"] = self._build_avatar_config()
            self._session_config["animation"] = {"model_name": "default", "outputs": ["blendshapes", "viseme_id"]}

        self._response_config = {
            "modalities": ["text", "audio"],
        }

    # Maps language codes to env var keys for custom voice endpoints and standard fallbacks
    _VOICE_CONFIG_MAP: Dict[str, Dict[str, str]] = {
        "en": {
            "custom_endpoint_env": "AZURE_CUSTOM_VOICE_ENDPOINT_ID_EN",
            "standard_voice": "en-US-AndrewMultilingualNeural",
        },
        "de": {
            "custom_endpoint_env": "AZURE_CUSTOM_VOICE_ENDPOINT_ID_DE",
            "standard_voice": "de-DE-ConradNeural",
        },
    }

    def _build_voice_config(self, language: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Build voice config based on AZURE_VOICE_SOURCE.

        The avatar is a visual-only channel — it lip-syncs to whatever TTS
        voice is configured here.  When voice source is ``avatar`` we use a
        standard Azure TTS voice (same behaviour as the Speech Studio
        playground).  Use ``custom`` if you have a Custom Neural Voice
        endpoint you want to pair with the avatar.
        """
        voice_source = os.getenv("AZURE_VOICE_SOURCE", "auto").lower().strip()
        lang_key = (language or self.language).lower().split("-")[0]
        config_entry = self._VOICE_CONFIG_MAP.get(lang_key, self._VOICE_CONFIG_MAP["en"])

        if voice_source == "avatar":
            # The avatar is purely visual — it needs a TTS voice to produce
            # audio that it then lip-syncs to.  Use the configured standard
            # voice, matching the behaviour of the Azure Speech Studio
            # playground.
            standard_voice = os.getenv("AZURE_TTS_VOICE", config_entry["standard_voice"])
            logger.info(
                "[%s] Voice source 'avatar' – using standard TTS voice for avatar "
                "lip-sync (voice=%s, lang=%s)",
                self.session_id, standard_voice, lang_key,
            )
            return {
                "name": standard_voice,
                "type": "azure-standard",
                "temperature": 0.8,
            }

        if voice_source == "custom":
            custom_endpoint_id = os.getenv(config_entry["custom_endpoint_env"], "").strip()
            if not custom_endpoint_id:
                raise RuntimeError(
                    f"AZURE_VOICE_SOURCE is 'custom' but {config_entry['custom_endpoint_env']} is not set"
                )
            voice_name = os.getenv("AZURE_TTS_VOICE", "custom-voice")
            logger.info("[%s] Voice source 'custom' for '%s' (endpoint_id=%s)", self.session_id, lang_key, custom_endpoint_id)
            return {
                "name": voice_name,
                "type": "azure-custom",
                "endpoint_id": custom_endpoint_id,
                "temperature": 0.8,
            }

        if voice_source == "standard":
            standard_voice = os.getenv("AZURE_TTS_VOICE", config_entry["standard_voice"])
            logger.info("[%s] Voice source 'standard' for '%s' (%s)", self.session_id, lang_key, standard_voice)
            return {
                "name": standard_voice,
                "type": "azure-standard",
                "temperature": 0.8,
            }

        # voice_source == "auto" (default) – custom if endpoint configured, else standard
        custom_endpoint_id = os.getenv(config_entry["custom_endpoint_env"], "").strip()
        if custom_endpoint_id:
            voice_name = os.getenv("AZURE_TTS_VOICE", os.getenv("AZURE_VOICE_AVATAR_CHARACTER", "custom-voice"))
            logger.info("[%s] Auto-detected custom voice for '%s' (endpoint_id=%s)", self.session_id, lang_key, custom_endpoint_id)
            return {
                "name": voice_name,
                "type": "azure-custom",
                "endpoint_id": custom_endpoint_id,
                "temperature": 0.8,
            }
        standard_voice = config_entry["standard_voice"]
        logger.info("[%s] Auto-detected standard voice for '%s' (%s)", self.session_id, lang_key, standard_voice)
        return {
            "name": standard_voice,
            "type": "azure-standard",
            "temperature": 0.8,
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
        if not self._ws_is_open():
            await self.connect()

    def _build_avatar_config(self) -> Dict[str, Any]:
        character = os.getenv("AZURE_VOICE_AVATAR_CHARACTER", "lisa")
        style = os.getenv("AZURE_VOICE_AVATAR_STYLE")
        customized = os.getenv("AZURE_VOICE_AVATAR_CUSTOMIZED", "false").lower() in ("true", "1", "yes")
        video_width = int(os.getenv("AZURE_VOICE_AVATAR_WIDTH", "1280"))
        video_height = int(os.getenv("AZURE_VOICE_AVATAR_HEIGHT", "720"))
        bitrate = int(os.getenv("AZURE_VOICE_AVATAR_BITRATE", "2000000"))
        config: Dict[str, Any] = {
            "character": character,
            "customized": customized,
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
                headers["api-key"] = self._api_key
                headers["Ocp-Apim-Subscription-Key"] = self._api_key
                self.ws = await websockets.connect(ws_url, additional_headers=headers)
            else:
                last_error: Optional[Exception] = None
                for scope in self._token_scopes:
                    try:
                        token = await self._get_token(scope)
                        ws_url = self._build_ws_url(token)
                        auth_headers = {**headers, "Authorization": f"Bearer {token}"}
                        self.ws = await websockets.connect(ws_url, additional_headers=auth_headers)
                        logger.info("[%s] Connected using token scope %s", self.session_id, scope)
                        last_error = None
                        break
                    except Exception as exc:  # pylint: disable=broad-except
                        last_error = exc
                        logger.warning("[%s] Failed to connect with token scope %s: %s", self.session_id, scope, str(exc))
                if self.ws is None:
                    if last_error:
                        raise RuntimeError(
                            "Authentication failed for all token scopes. "
                            "Set AZURE_OPENAI_API_KEY or ensure managed identity has Azure AI Services User role. "
                            f"Last error: {str(last_error)}"
                        ) from last_error
                    raise RuntimeError("Authentication failed: no token scopes configured")
            logger.info("[%s] Connected to Azure Voice Live", self.session_id)
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("[%s] Sending session.update with modalities: %s", self.session_id, self._session_config.get("modalities"))
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

    async def _get_token(self, scope: str) -> str:
        credential = DefaultAzureCredential()
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

    def get_cached_session_updated_event(self) -> Optional[Dict[str, Any]]:
        return self._latest_session_updated_event

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
                logger.debug(f"[{self.session_id}] Event: {event_type}")  # Log all events for debugging
                if event_type == "error":
                    logger.error(f"[{self.session_id}] Azure error: {event}")
                    await self._broadcast({"type": "error", "payload": event})
                elif event_type == "response.audio.delta":
                    delta = event.get("delta")
                    logger.info(f"[{self.session_id}] Received audio delta, length: {len(delta) if delta else 0}")
                    await self._broadcast({"type": "assistant_audio_delta", "delta": delta})
                elif event_type == "response.audio.done":
                    logger.info(f"[{self.session_id}] Audio response done")
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
                    logger.info(f"[{self.session_id}] Avatar connecting")
                    await self._broadcast({"type": "avatar_connecting"})
                elif event_type == "session.updated":
                    self._latest_session_updated_event = event
                    session = event.get("session", {})
                    logger.info(
                        "[%s] session.updated – modalities=%s, voice=%s, avatar=%s",
                        self.session_id,
                        session.get("modalities"),
                        session.get("voice"),
                        "yes" if session.get("avatar") else "no",
                    )
                    await self._broadcast({"type": "event", "payload": event})
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
