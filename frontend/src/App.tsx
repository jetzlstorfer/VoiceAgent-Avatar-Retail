import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from 'react-markdown';

type LogEntry = { id: string; text: string };

type WsEvent = {
    type: string;
    delta?: string;
    transcript?: string;
    item_id?: string;
    status?: string;
    payload?: unknown;
    session_id?: string;
    name?: string;
};

const BACKEND_HTTP_BASE = (import.meta.env.VITE_BACKEND_BASE as string | undefined) ?? window.location.origin;
const BACKEND_WS_BASE = BACKEND_HTTP_BASE.replace(/^http/, "ws");
const TARGET_SAMPLE_RATE = 24000;
const INT16_MAX = 32767;

function float32ToBase64(data: Float32Array): string {
    const buffer = new Uint8Array(data.buffer);
    let result = "";
    for (let i = 0; i < buffer.length; i += 1) {
        result += String.fromCharCode(buffer[i]);
    }
    return btoa(result);
}

function downsampleBuffer(buffer: Float32Array, inputRate: number, targetRate: number): Float32Array {
    if (targetRate === inputRate) {
        return buffer;
    }
    const ratio = inputRate / targetRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
        let accum = 0;
        let count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
            accum += buffer[i];
            count += 1;
        }
        result[offsetResult] = count > 0 ? accum / count : 0;
        offsetResult += 1;
        offsetBuffer = nextOffsetBuffer;
    }
    return result;
}

function pcm16Base64ToFloat32(b64: string): Float32Array<ArrayBuffer> {
    const binary = atob(b64);
    const len = binary.length / 2;
    const result = new Float32Array(len) as Float32Array<ArrayBuffer>;
    for (let i = 0; i < len; i += 1) {
        const index = i * 2;
        const sample = (binary.charCodeAt(index + 1) << 8) | binary.charCodeAt(index);
        const signed = sample >= 0x8000 ? sample - 0x10000 : sample;
        result[i] = signed / INT16_MAX;
    }
    return result;
}

function useLog(): [LogEntry[], (message: string) => void] {
    const [entries, setEntries] = useState<LogEntry[]>([]);
    const append = useCallback((text: string) => {
        setEntries((prev: LogEntry[]) => [{ id: crypto.randomUUID(), text }, ...prev.slice(0, 99)]);
    }, []);
    return [entries, append];
}

function App() {
    // Set the document title when the component mounts
    useEffect(() => {
        document.title = "Contoso Retail - Azure Voice Live Avatar Agent";
    }, []);

    const [sessionId, setSessionId] = useState<string | null>(null);
    const [micActive, setMicActive] = useState(false);
    const [avatarReady, setAvatarReady] = useState(false);
    const [avatarLoading, setAvatarLoading] = useState(false);
    const [avatarPaused, setAvatarPaused] = useState(false);
    const [assistantTranscript, setAssistantTranscript] = useState("");
    const [userTranscript, setUserTranscript] = useState("");
    const [entries, appendLog] = useLog();
    const [avatarIceServers, setAvatarIceServers] = useState<RTCIceServer[]>([]);

    const wsRef = useRef<WebSocket | null>(null);
    const pcRef = useRef<RTCPeerConnection | null>(null);
    const videoRef = useRef<HTMLVideoElement | null>(null);
    const remoteAudioRef = useRef<HTMLAudioElement | null>(null);

    const mediaStreamRef = useRef<MediaStream | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);

    const playbackCtxRef = useRef<AudioContext | null>(null);
    const playbackCursorRef = useRef<number>(0);

    const ensurePlaybackContext = useCallback(() => {
        if (!playbackCtxRef.current) {
            playbackCtxRef.current = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
            playbackCursorRef.current = playbackCtxRef.current.currentTime;
        }
        const ctx = playbackCtxRef.current;
        if (ctx?.state === "suspended") {
            ctx.resume().catch(() => undefined);
        }
        return playbackCtxRef.current;
    }, []);

    const schedulePlayback = useCallback(
        (deltaB64: string) => {
            const audioCtx = ensurePlaybackContext();
            const floatSamples = pcm16Base64ToFloat32(deltaB64);
            if (!floatSamples.length) {
                return;
            }
            const buffer = audioCtx.createBuffer(1, floatSamples.length, TARGET_SAMPLE_RATE);
            buffer.copyToChannel(floatSamples, 0);
            const source = audioCtx.createBufferSource();
            source.buffer = buffer;
            source.connect(audioCtx.destination);
            const startAt = Math.max(playbackCursorRef.current, audioCtx.currentTime + 0.02);
            source.start(startAt);
            playbackCursorRef.current = startAt + buffer.duration;
        },
        [ensurePlaybackContext]
    );

    const teardownMic = useCallback(() => {
        processorRef.current?.disconnect();
        audioCtxRef.current?.close().catch(() => undefined);
        mediaStreamRef.current?.getTracks().forEach((track: MediaStreamTrack) => track.stop());
        processorRef.current = null;
        audioCtxRef.current = null;
        mediaStreamRef.current = null;
        setMicActive(false);
    }, []);

    useEffect(() => () => teardownMic(), [teardownMic]);

    const connectWebSocket = useCallback(
        (id: string) => {
            const ws = new WebSocket(`${BACKEND_WS_BASE}/ws/sessions/${id}`);
            wsRef.current = ws;

            ws.onopen = () => appendLog("WebSocket connected");
            ws.onclose = () => {
                appendLog("WebSocket closed");
                teardownMic();
            };
            ws.onerror = (event: Event) => appendLog(`WebSocket error: ${event.type}`);

            ws.onmessage = (msg) => {
                const data: WsEvent = JSON.parse(msg.data);
                switch (data.type) {
                    case "session_ready":
                        if (data.session_id) {
                            appendLog(`Session ready: ${data.session_id}`);
                        }
                        break;
                    case "assistant_audio_delta":
                        if (typeof data.delta === "string") {
                            schedulePlayback(data.delta);
                        }
                        break;
                    case "assistant_transcript_delta":
                        if (typeof data.delta === "string") {
                            setAssistantTranscript((prev: string) => prev + data.delta);
                        }
                        break;
                    case "assistant_transcript_done":
                        if (typeof data.transcript === "string") {
                            setAssistantTranscript(data.transcript);
                        }
                        break;
                    case "user_transcript_completed":
                        if (typeof data.transcript === "string") {
                            setUserTranscript(data.transcript);
                        }
                        break;
                    case "function_call_completed":
                        appendLog(`Function call completed: ${data.name ?? "unknown"}`);
                        break;
                    case "error":
                        appendLog(`Server error: ${JSON.stringify(data.payload)}`);
                        break;
                    case "event": {
                        const payload = data.payload as Record<string, any> | undefined;
                        if (payload?.type === "session.updated") {
                            const session = payload.session ?? {};
                            const avatar = session.avatar ?? {};
                            const candidateSources = [
                                avatar.ice_servers,
                                session.rtc?.ice_servers,
                                session.ice_servers,
                            ].find((value) => Array.isArray(value));
                            if (candidateSources) {
                                const normalized: RTCIceServer[] = candidateSources
                                    .map((entry: any) => {
                                        if (typeof entry === "string") {
                                            return { urls: entry } as RTCIceServer;
                                        }
                                        if (entry && typeof entry === "object") {
                                            const { urls, username, credential } = entry;
                                            if (!urls) {
                                                return null;
                                            }
                                            return {
                                                urls,
                                                username,
                                                credential,
                                            } as RTCIceServer;
                                        }
                                        return null;
                                    })
                                    .filter((entry): entry is RTCIceServer => Boolean(entry));
                                if (normalized.length) {
                                    setAvatarIceServers(normalized);
                                    appendLog(
                                        `Received ${normalized.length} ICE server${normalized.length > 1 ? "s" : ""} from session`
                                    );
                                }
                            }
                        }
                        break;
                    }
                    default:
                        break;
                }
            };
        },
        [appendLog, schedulePlayback, teardownMic]
    );

    const createSession = useCallback(async () => {
        const response = await fetch(`${BACKEND_HTTP_BASE}/sessions`, { method: "POST" });
        if (!response.ok) {
            throw new Error(`Failed to create session: ${response.status}`);
        }
        const { session_id } = await response.json();
        setSessionId(session_id);
        appendLog(`Session created: ${session_id}`);
        connectWebSocket(session_id);
        return session_id;
    }, [appendLog, connectWebSocket]);

    useEffect(() => {
        createSession().catch((err: unknown) => appendLog(`Error creating session: ${String(err)}`));
    }, [appendLog, createSession]);

    const startMic = useCallback(async () => {
        if (!wsRef.current) {
            appendLog("WebSocket not ready");
            return;
        }
        const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const audioContext = new AudioContext();
        if (audioContext.state === "suspended") {
            try {
                await audioContext.resume();
            } catch {
                /* ignore */
            }
        }

        const playbackCtx = ensurePlaybackContext();
        if (playbackCtx && playbackCtx.state === "suspended") {
            try {
                await playbackCtx.resume();
            } catch {
                /* ignore */
            }
        }

        const source = audioContext.createMediaStreamSource(mediaStream);
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (event: AudioProcessingEvent) => {
            const input = event.inputBuffer.getChannelData(0);
            const downsampled = downsampleBuffer(input, audioContext.sampleRate, TARGET_SAMPLE_RATE);
            if (!downsampled.length) {
                return;
            }
            const base64 = float32ToBase64(downsampled);
            wsRef.current?.send(
                JSON.stringify({
                    type: "audio_chunk",
                    data: base64,
                    encoding: "float32",
                })
            );
        };
        source.connect(processor);
        processor.connect(audioContext.destination);

        mediaStreamRef.current = mediaStream;
        audioCtxRef.current = audioContext;
        processorRef.current = processor;
        setMicActive(true);
        appendLog("Microphone streaming started");
    }, [appendLog]);

    const stopMic = useCallback(() => {
        teardownMic();
        appendLog("Microphone streaming stopped");
    }, [appendLog, teardownMic]);

    const sendTextPrompt = useCallback(async () => {
        if (!sessionId) {
            return;
        }
        const text = prompt("Enter a message for the assistant");
        if (!text) {
            return;
        }
        const response = await fetch(`${BACKEND_HTTP_BASE}/sessions/${sessionId}/text`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        if (!response.ok) {
            appendLog(`Failed to send text: ${response.status}`);
        }
    }, [appendLog, sessionId]);

    const startAvatar = useCallback(async () => {
        if (!sessionId) {
            appendLog("Session not ready");
            return;
        }
        if (pcRef.current) {
            appendLog("Avatar already connected");
            return;
        }

        setAvatarLoading(true);
        appendLog("Initializing avatar connection...");

        try {
            const pc = new RTCPeerConnection({
                bundlePolicy: "max-bundle",
                iceServers: avatarIceServers,
            });
            pcRef.current = pc;

            pc.addTransceiver("audio", { direction: "recvonly" });
            pc.addTransceiver("video", { direction: "recvonly" });

                pc.ontrack = (event) => {
                const [stream] = event.streams;
                if (!stream) {
                    return;
                }

                if (event.track.kind === "video" && videoRef.current) {
                    videoRef.current.srcObject = stream;
                    videoRef.current
                        .play()
                        .catch(() => {
                            /* ignore auto-play rejection; user interaction already occurred */
                        });
                    appendLog("Avatar video track received");
                }

                if (event.track.kind === "audio") {
                    let audioEl = remoteAudioRef.current;
                    if (!audioEl) {
                        audioEl = document.createElement("audio");
                        audioEl.autoplay = true;
                        audioEl.controls = false;
                        audioEl.style.display = "none";
                        audioEl.setAttribute("playsinline", "true");
                        audioEl.muted = false;
                        document.body.appendChild(audioEl);
                        remoteAudioRef.current = audioEl;
                    }
                    audioEl.srcObject = stream;
                    audioEl.play().catch(() => undefined);
                    appendLog("Avatar audio track received");
                }
            };

            const gatheringFinished = new Promise<void>((resolve) => {
                if (pc.iceGatheringState === "complete") {
                    resolve();
                } else {
                    pc.addEventListener("icegatheringstatechange", () => {
                        if (pc.iceGatheringState === "complete") {
                            resolve();
                        }
                    });
                }
            });

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            await gatheringFinished;

            const localSdp = pc.localDescription?.sdp;
            if (!localSdp) {
                appendLog("Failed to obtain local SDP");
                return;
            }

            const response = await fetch(`${BACKEND_HTTP_BASE}/sessions/${sessionId}/avatar-offer`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sdp: localSdp }),
            });

            if (!response.ok) {
                appendLog(`Avatar offer failed: ${response.status}`);
                setAvatarLoading(false);
                return;
            }

            const { sdp } = await response.json();
            await pc.setRemoteDescription({ type: "answer", sdp });
            setAvatarLoading(false);
            setAvatarReady(true);
            appendLog("Avatar connected");
        } catch (error) {
            appendLog(`Avatar connection error: ${String(error)}`);
            setAvatarLoading(false);
            if (pcRef.current) {
                pcRef.current.close();
                pcRef.current = null;
            }
        }
    }, [appendLog, sessionId, avatarIceServers]);

    const teardownAvatar = useCallback(() => {
        pcRef.current?.close();
        pcRef.current = null;
        if (videoRef.current) {
            videoRef.current.srcObject = null;
        }
        if (remoteAudioRef.current) {
            remoteAudioRef.current.pause();
            remoteAudioRef.current.srcObject = null;
            remoteAudioRef.current.remove();
            remoteAudioRef.current = null;
        }
        setAvatarLoading(false);
        setAvatarReady(false);
        setAvatarPaused(false);
        appendLog("Avatar connection closed");
    }, [appendLog]);

    const pauseAvatar = useCallback(() => {
        if (videoRef.current) {
            videoRef.current.pause();
        }
        if (remoteAudioRef.current) {
            remoteAudioRef.current.pause();
        }
        setAvatarPaused(true);
        appendLog("Avatar paused");
    }, [appendLog]);

    const unpauseAvatar = useCallback(() => {
        if (videoRef.current) {
            videoRef.current.play().catch(() => {
                /* ignore auto-play rejection */
            });
        }
        if (remoteAudioRef.current) {
            remoteAudioRef.current.play().catch(() => {
                /* ignore auto-play rejection */
            });
        }
        setAvatarPaused(false);
        appendLog("Avatar resumed");
    }, [appendLog]);

    return (
        <main>
            <h1>Contoso Retail - Azure Voice Live Avatar Agent</h1>
            <p>Stream audio to Azure Voice Live, receive tool-calling responses, and render avatar video.</p>

            <section className="section">
                <h2>Controls</h2>
                <div className="controls">
                    <button 
                        onClick={() => window.location.reload()} 
                        className="refresh-button"
                        title="Click on Refresh to get started with this demo"
                    >
                        🔄 Refresh
                    </button>
                    <button onClick={micActive ? stopMic : startMic}>{micActive ? "Stop Microphone" : "Start Microphone"}</button>
                    <button className="secondary" onClick={sendTextPrompt} disabled={!sessionId}>
                        Send Text Prompt
                    </button>
                    <button onClick={startAvatar} disabled={!sessionId || avatarLoading || avatarReady}>
                        {avatarLoading ? "Connecting Avatar..." : "Start Avatar"}
                    </button>
                    <button 
                        onClick={avatarPaused ? unpauseAvatar : pauseAvatar} 
                        disabled={!avatarReady || avatarLoading}
                    >
                        {avatarPaused ? "Resume Avatar" : "Pause Avatar"}
                    </button>
                    <button 
                        onClick={() => {}} 
                        disabled={true}
                        className="danger"
                        title="Not implemented yet"
                    >
                        Stop Avatar
                    </button>
                </div>
            </section>

            <section className="section video-wrapper">
                <h2>Avatar Stream</h2>
                <div className="video-container">
                    <video ref={videoRef} autoPlay playsInline muted={false} controls={false} />
                    {avatarLoading && (
                        <div className="avatar-loading-overlay">
                            <div className="loading-spinner"></div>
                            <p>Loading Avatar...</p>
                        </div>
                    )}
                    {avatarPaused && avatarReady && (
                        <div className="avatar-paused-overlay">
                            <div className="pause-icon">⏸️</div>
                            <p>Avatar Paused</p>
                        </div>
                    )}
                    {!avatarReady && !avatarLoading && (
                        <div className="avatar-placeholder">
                            <p>Click "Start Avatar" to begin video stream</p>
                        </div>
                    )}
                </div>
            </section>

            <section className="section">
                <h2>Transcripts</h2>
                <div>
                    <strong>User:</strong>
                    <p>{userTranscript || "(waiting for speech)"}</p>
                </div>
                <div>
                    <strong>Assistant:</strong>
                    <div className="assistant-response">
                        {assistantTranscript ? (
                            <ReactMarkdown>{assistantTranscript}</ReactMarkdown>
                        ) : (
                            <p>(waiting for response)</p>
                        )}
                    </div>
                </div>
            </section>

            <section className="section">
                <h2>Event Log</h2>
                <div className="log-pane">
                    {entries.map((entry) => (
                        <div key={entry.id} className="log-entry">
                            {entry.text}
                        </div>
                    ))}
                </div>
            </section>
        </main>
    );
}

export default App;
