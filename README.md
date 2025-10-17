# Retail E-Com Voice Live Agent with Avatar

This solution extends the original Chainlit + Python prototype by splitting the workload into a Python backend and a TypeScript browser client. The backend keeps the Azure Voice Live realtime session (including all tool calls) while the browser attaches to the avatar stream through WebRTC.

## Architecture Overview

- **Backend (`backend/`)** – FastAPI service that manages Azure Voice Live sessions, streams microphone audio to the realtime API, dispatches server events back to the browser, and brokers function call responses using Azure AI Search, Logic Apps, and the Contoso e-commerce APIs.
- **Frontend (`frontend/`)** – Vite + React client that captures user audio, streams PCM chunks to the backend over WebSocket, renders assistant audio locally, and negotiates the avatar WebRTC session in the browser.
- **Legacy prototype** – The original Chainlit assets remain under `python-archives/` for reference.

## Prerequisites

- Python 3.10+
- Node.js 20+
- Azure resources:
  - Speech resource enabled for Voice Live API
  - Azure AI Search service (with an index + semantic configuration)
  - Logic Apps for shipments and call log analysis
  - Contoso retail sample APIs (or your equivalent business APIs)
- Authentication via either `DefaultAzureCredential` (Managed Identity, Visual Studio Code sign-in, or Azure CLI login) **or** an Azure OpenAI API key via `AZURE_OPENAI_API_KEY`.

## Configuration

Copy `.env.sample` to `.env` and fill in the required values:

```bash
cp .env.sample .env
```

Key settings:

- `AZURE_VOICE_LIVE_ENDPOINT` / `VOICE_LIVE_MODEL` – Voice Live endpoint + realtime model name (e.g. `gpt-realtime-preview`).
- `AZURE_VOICE_AVATAR_CHARACTER` – **Required**: Avatar persona that exists in your Speech Studio resource. 
  - **Find valid characters**: Go to [Speech Studio](https://speech.microsoft.com) → Your resource → Avatar section
  - **Region-specific**: Character names vary by Speech resource region
  - **Case-sensitive**: Use exact character ID from portal (e.g., `lisa`, `james`, `michelle`)
  - Common error: `avatar_verification_failed` means the character doesn't exist in your resource/region
- Optional `AZURE_VOICE_AVATAR_STYLE` – Supply only if the character supports named styles (leave unset to use the service default).
- `AZURE_OPENAI_API_KEY` – Required when authenticating with an API key instead of managed identity.
- `AZURE_VOICE_AVATAR_*` – Avatar character and optional TURN/STUN servers.
- `ai_search_*` – Azure AI Search connection settings.
- `logic_app_url_*` – Logic App webhook endpoints.
- `ecom_api_url` – Contoso sample API host.
- Optional `VITE_BACKEND_BASE` – Override when serving the frontend behind a different hostname.

## Running the Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The backend exposes:

- `POST /sessions` – Create a Voice Live session.
- `POST /sessions/{id}/avatar-offer` – Exchange WebRTC SDP for avatar video.
- `POST /sessions/{id}/text` – Send a text turn to the assistant.
- `POST /sessions/{id}/commit-audio` – Force audio commit (mostly for manual control).
- `WS /ws/sessions/{id}` – Bi-directional channel for audio streaming and realtime events.

## Running the Frontend

```powershell
cd frontend
npm install
npm run dev
```

The Vite dev server proxies API calls to `http://localhost:8000` (configure `vite.config.ts` if you deploy elsewhere).

### Browser Workflow

1. The app requests a new session from the backend and opens a WebSocket bridge.
2. Clicking **Start Microphone** captures audio, downsamples to 24 kHz float frames, and pushes base64 chunks to the backend.
3. Assistant audio deltas returned by the backend are scheduled in a browser `AudioContext` for playback.
4. Clicking **Start Avatar** creates a `RTCPeerConnection`, sends the SDP offer to `/avatar-offer`, and sets the returned answer. The avatar video and audio render through the `<video>` element.

## Tool Calling + Business Integrations

The backend reuses the original tools logic:

- `perform_search_based_qna` via Azure AI Search.
- Shipment and call log Logic App integrations.
- E-commerce catalog/order lookups via REST APIs.

Function call outputs are posted back to the realtime session so the model can continue the conversation seamlessly.

## Production Hardening Checklist

- Frontend worker for audio processing (AudioWorklet) to reduce latency.
- Persist conversation state for call log analysis payloads.
- Add authentication between browser ↔ backend (Azure AD App Service auth or Entra ID).
- Use a TURN server for the avatar stream when operating across restrictive networks.
- Instrument backend with Application Insights for latency + error tracking.

## Avatar Functionality Walkthrough

The avatar path relies on the Azure Voice Live realtime session plus a WebRTC negotiation with the browser. The following steps capture the exact code changes that enabled a reliable avatar stream.

### Backend (`backend/app/voice_live_client.py`)

- **Session configuration** – `VoiceLiveSession._session_config` enables `"avatar"` and `"animation"` modalities and injects `AZURE_VOICE_AVATAR_*` settings produced from `_build_avatar_config()`.
- **Session update** – On connect, the backend immediately sends `session.update` with the avatar block so the service returns ICE server hints in subsequent `session.updated` events.
- **SDP encoding** – `connect_avatar` wraps the browser SDP as `{"type": "offer", "sdp": ...}` and base64-encodes it. This matches the Voice Live requirement (the API rejects plain-text SDP).
- **session.avatar.connecting** – When the service responds the backend decodes the `server_sdp` (base64 JSON payload) and resolves a future so `/avatar-offer` can reply with a clean SDP answer.
- **Event fan-out** – Every raw event from the Azure websocket is broadcast to browser listeners. This is how the frontend receives `session.updated` (for ICE servers) and `session.avatar.connecting` (for UI state).

### Frontend (`frontend/src/App.tsx`)

- **Capture ICE servers** – The websocket handler watches for `event.type === "session.updated"`, normalises any `ice_servers` blocks, and stores them in React state.
- **WebRTC offer** – `startAvatar()` builds an `RTCPeerConnection` with `bundlePolicy: "max-bundle"`, adds `recvonly` audio/video transceivers, and uses the cached ICE server list when available.
- **SDP exchange** – The local offer is posted to `/sessions/{id}/avatar-offer`; the decoded SDP answer returned by the backend is applied as the remote description.
- **Track handling** – `pc.ontrack` splits audio vs. video. Video streams bind directly to the `<video>` element, while audio streams attach to a hidden `<audio>` element that auto-plays to avoid browser autoplay restrictions.
- **Audio context unlock** – Starting the microphone resumes both the capture `AudioContext` and the playback `AudioContext`, ensuring mixed PCM deltas and WebRTC audio play through the same output device.

### Session Events to Expect

1. `session.updated` – Confirms the avatar modality is active and carries TURN/STUN server configuration. The frontend must harvest these values before calling `startAvatar()`.
2. `session.avatar.connecting` – Indicates the realtime service accepted the SDP; backend responds with a decoded answer, which the frontend applies immediately.
3. `response.audio.delta` / `response.audio.done` – Continue to deliver PCM deltas even when the avatar is active. The frontend schedules these in an `AudioContext` so the audio output stays smooth while the WebRTC stream spins up.
4. `error` – Any negotiation failure is surfaced to the browser log. Typical causes include unknown avatar characters, missing ICE configuration, or malformed SDP payloads.

### Troubleshooting Tips

- Use the helper script `backend/test_avatar_characters.py` to validate character/style combinations. It performs the same base64 SDP exchange as the production client.
- A `session.avatar.connect` timeout usually means the backend never saw `session.avatar.connecting`; check that the SDP payload is base64 JSON and that `AZURE_VOICE_AVATAR_ENABLED=true`.
- If the video renders but audio is silent, confirm the `Avatar audio track received` log appears and the hidden `<audio>` element is attached in DevTools. Missing ICE servers or a suspended `AudioContext` are the most common causes.

## Deployment

This application supports both local development and production deployment to Azure Container Apps with zero workflow changes required.

### Local Development (No Changes Required)

The current local development workflow remains completely unchanged:

**Backend** (Terminal 1):
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend** (Terminal 2):
```bash
cd frontend
npm run dev
```

**Access**: `http://localhost:5173`

### How Local Development Works
- **Frontend**: Vite dev server on `localhost:5173` with hot reloading
- **Backend**: FastAPI on `localhost:8000` via Uvicorn ASGI server
- **Communication**: Frontend uses proxy configuration in `vite.config.ts` to route API calls (`/sessions/*`, `/ws/*`) to the backend
- **Technology Stack**: React + Vite → FastAPI + Uvicorn (not Flask!)

### Azure Container Apps Deployment

For production deployment, both frontend and backend are packaged into a single container that serves everything from FastAPI on port 8000.

#### Architecture Changes for Production
- **Single Origin**: FastAPI serves both API endpoints AND static React files
- **No Proxy Needed**: Eliminates CORS issues and simplifies WebSocket connections
- **Static File Serving**: React build output is served from `/static/` route
- **SPA Fallback**: Catch-all route serves `index.html` for client-side routing

#### Deployment Files
The following files have been added for containerization (no impact on local dev):

- `Dockerfile` - Multi-stage build (Node.js → Python)
- `start.sh` - Container startup script  
- `vite.config.prod.ts` - Production build configuration
- `azure-containerapp.yaml` - Container App resource definition
- `deploy.sh` - Automated deployment script
- `.dockerignore` - Exclude development files from build

#### Smart Conditional Logic
The production features only activate in containerized environments:

- **Static files**: Only mounted when `/static` directory exists (production builds)
- **SPA fallback**: Only matches non-API routes (won't interfere with local proxy)
- **Health checks**: `/health` endpoint for Container App monitoring
- **Original config**: `vite.config.ts` unchanged for local development

#### Deployment Steps

1. **Build and Push Container** (Automated Build):
   ```bash
   docker build -t yourregistry.azurecr.io/voice-live-avatar:latest .
   docker push yourregistry.azurecr.io/voice-live-avatar:latest
   ```
   
   > **How the Dockerfile Works**: The multi-stage build automatically:
   > 1. Builds the frontend using `npm run build:prod` in a Node.js container
   > 2. Copies the built frontend files to `backend/static/` in the Python container  
   > 3. Packages everything into a single production container
   >
   > **Manual Copy (Only for Local Testing)**: If you want to test the production build locally without Docker:
   > ```bash
   > cd frontend && npm run build:prod
   > Copy-Item -Path "frontend\dist\*" -Destination "backend\static\" -Recurse -Force
   > cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
   > ```

3. **Deploy to Azure Container Apps**:
   ```bash
   az containerapp create \
     --resource-group your-rg \
     --environment your-env \
     --name voice-live-avatar-app \
     --image yourregistry.azurecr.io/voice-live-avatar:latest \
     --target-port 8000 \
     --env-vars AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/
   ```

#### Environment Variables for Production
Configure these secrets in Azure Container Apps:
- `AZURE_OPENAI_API_KEY`
- `AZURE_SEARCH_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_SEARCH_ENDPOINT`
- `AZURE_VOICE_AVATAR_ENABLED=true`
- `AZURE_VOICE_AVATAR_CHARACTER=lisa`
- `AZURE_VOICE_AVATAR_STYLE=casual-sitting`

#### Production Benefits
- **Performance**: Single container, no proxy overhead
- **Scalability**: Container Apps auto-scaling based on HTTP requests
- **Reliability**: Health checks ensure container restarts on failures
- **Security**: Managed identity integration with Azure services
- **Cost**: Pay-per-use scaling down to zero when idle

### Development vs Production Comparison

| Aspect | Local Development | Production (Container Apps) |
|--------|-------------------|----------------------------|
| **Frontend** | Vite dev server (`:5173`) | Static files via FastAPI |
| **Backend** | Uvicorn (`:8000`) | Uvicorn in container (`:8000`) |
| **Communication** | Proxy configuration | Same origin |
| **Hot Reload** | ✅ Enabled | ❌ Static build |
| **CORS** | Handled by proxy | ❌ Not needed |
| **WebSocket** | Proxy passthrough | Direct connection |
| **Static Assets** | Served by Vite | Served by FastAPI |
| **Deployment** | Two separate processes | Single container |

This design ensures you can develop locally with the full-featured development experience while deploying to a production-ready, scalable container environment without any workflow disruption.

## References

- [Voice Live API reference](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-api-reference)
- [Voice Live avatar handshake](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-api-reference#sessionavatarconnect)
- [DefaultAzureCredential documentation](https://learn.microsoft.com/azure/developer/python/sdk/azure-identity-default-azure-credential)
