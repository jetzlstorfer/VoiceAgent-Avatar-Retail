# Copilot Instructions

## Architecture

This is a **real-time voice agent** for retail e-commerce, combining Azure OpenAI's Voice Live API with an Azure Speech avatar. The system has three communication layers:

1. **Frontend → Backend**: WebSocket (`/ws/sessions/{id}`) carries JSON events (`audio_chunk`, `user_text`, `commit_audio`, `request_response`) with audio as base64-encoded PCM16.
2. **Backend → Azure Voice Live API**: A second WebSocket connection proxies audio and function-call events to Azure OpenAI's GPT Realtime model.
3. **Avatar video**: A direct WebRTC connection (recvonly) from the browser to Azure Speech, negotiated via SDP offers routed through the backend (`POST /sessions/{id}/avatar-offer`).

The backend is a **FastAPI** (Python 3.12) app serving as a stateful proxy—it manages per-session WebSocket connections to Azure and dispatches function tool calls (search, orders, shipments, analysis). Sessions are stored in-memory (not persistent).

The frontend is a **React 19 + TypeScript** SPA built with Vite. It captures microphone audio via the Web Audio API, downsamples to 24 kHz PCM16, and streams it over the WebSocket. Avatar video renders via an `RTCPeerConnection`.

In production, the frontend is compiled to `backend/static/` and served by FastAPI with an SPA catch-all route. In development, Vite proxies `/sessions` and `/ws` to the backend on port 8000.

## Build & Run Commands

```bash
# Full dev setup: create venv, install deps, build frontend, copy to backend/static, start uvicorn with --reload
make run

# Quick restart (skip venv install): copy frontend build + run uvicorn
make run-copy

# Install backend only (creates .venv, pip install)
make install

# Build frontend only
make build-frontend

# Copy frontend dist → backend/static
make copy-frontend

# Frontend dev server (port 5173, proxies API to :8000)
cd frontend && npm install && npm run dev
```

The backend entrypoint is `app.main:app` (uvicorn). The production startup command is in `start.sh`.

## Testing

There is no automated test suite. The only test file is `backend/test_avatar_characters.py`, a manual script that validates Azure Speech avatar characters via WebRTC:

```bash
cd backend && source .venv/bin/activate && python test_avatar_characters.py
```

If adding tests, use `pytest` for the backend. There is no frontend test setup.

## CI/CD

- **`pr-build.yml`**: Runs `make build-frontend` (Node 22) and `make install` (Python 3.12) on PRs to `main`. Build-only, no tests.
- **Deployment**: Manual via `deploy.sh` (Docker build → ACR push → Azure Container App update) or `azd up` for interactive Azure Developer CLI deployment.

## Key Conventions

### Backend (`backend/app/`)

- **Async everywhere**: All route handlers and service functions use `async def` / `await`.
- **Session management**: `SessionManager` uses a dict + `asyncio.Lock`. Sessions are created via `POST /sessions` and cleaned up on WebSocket disconnect or shutdown.
- **Function tool dispatch**: `tools.py` defines callable functions (`perform_search_based_qna`, `create_delivery_order`, etc.) registered in `AVAILABLE_FUNCTIONS` dict. The `VoiceLiveSession` receives tool-call events from Azure and dispatches them by name.
- **Environment-driven config**: All Azure service credentials and endpoints come from environment variables loaded via `python-dotenv`. See `env.sample` for the full list. The helper `_ensure_env()` in `tools.py` raises clear errors for missing vars.
- **Event queue pattern**: Each WebSocket client gets its own `asyncio.Queue` from the session for receiving events.

### Frontend (`frontend/src/`)

- **Single-component app**: Most logic lives in `App.tsx` (~1000+ lines) using React hooks for state (`useState`, `useRef`, `useCallback`, `useEffect`). No state management library.
- **Audio pipeline**: Microphone → `ScriptProcessorNode` → downsample to 24 kHz → float32-to-PCM16 → base64 → WebSocket.
- **Two Vite configs**: `vite.config.ts` (dev, with proxy) and `vite.config.prod.ts` (prod, `base: "/static/"`). Production build uses `npm run build:prod`.

### Environment Variables

Required for operation (see `env.sample`):
- `AZURE_VOICE_LIVE_ENDPOINT`, `VOICE_LIVE_MODEL`, `AZURE_OPENAI_API_KEY` — core Voice Live connection
- `AZURE_VOICE_AVATAR_CHARACTER` — avatar persona (e.g., "lisa")
- `ai_search_url`, `ai_search_key`, `ai_index_name` — Azure AI Search for QnA
- `ecom_api_url` — e-commerce product/order API
- `logic_app_url_shipment_orders`, `logic_app_url_call_log_analysis` — Logic App webhooks

### Docker

Multi-stage build: Node.js builds the frontend (`npm run build:prod`), then Python stage copies it to `backend/static/` and runs uvicorn. Health check hits `GET /health`.
