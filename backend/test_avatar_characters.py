"""
Test script to find valid avatar characters for your Azure Speech resource.
This will try common avatar character names and report which ones are accepted.
Uses proper WebRTC negotiation with aiortc and base64-encoded SDP.
"""
import asyncio
import os
import sys
import base64
from dotenv import load_dotenv
import websockets
import json
from aiortc import RTCPeerConnection, RTCSessionDescription

load_dotenv()

# Common avatar character + style combinations to test
# Format: (character, style) - style is required for validation
COMMON_CHARACTERS = [
    # Standard prebuilt avatars with styles
    ("lisa", "casual-sitting"),  # Most common - test first
    ("lisa", "casual"),
    ("lisa", "sitting"),
    ("james", "casual-sitting"),
    ("michelle", "casual-sitting"),
]

async def test_avatar_character(character_name: str, style: str | None) -> tuple[str, bool, str]:
    """Test if a character + style combination is valid using proper WebRTC negotiation."""
    endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
    model = os.getenv("VOICE_LIVE_MODEL", "gpt-realtime")
    
    if not endpoint or not api_key:
        print("Error: AZURE_VOICE_LIVE_ENDPOINT and AZURE_OPENAI_API_KEY must be set")
        sys.exit(1)
    
    # Build WebSocket URL - MUST use /voice-live/realtime path
    ws_url = endpoint.replace("https://", "wss://").rstrip("/")
    ws_url += f"/voice-live/realtime?api-version={api_version}&model={model}"
    
    # Use additional_headers for websockets 12.0+
    headers = [
        ("api-key", api_key),
    ]
    
    # Create WebRTC peer connection
    pc = RTCPeerConnection()
    
    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            # Wait for session.created
            msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            event = json.loads(msg)
            if event.get("type") != "session.created":
                await pc.close()
                return (f"{character_name}+{style}", False, f"Unexpected first event: {event.get('type')}")
            
            # Build avatar config
            avatar_config = {
                "character": character_name,
                "customized": False,
                "video": {
                    "resolution": {"width": 1280, "height": 720},
                    "bitrate": 2000000
                }
            }
            
            # Add style if provided (required for most characters)
            if style:
                avatar_config["style"] = style
            
            # Send session.update with avatar config
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio", "avatar"],
                    "avatar": avatar_config
                }
            }
            await ws.send(json.dumps(session_update))
            
            # Wait for session.updated after config
            session_updated = False
            session_modalities = None
            while not session_updated:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                event = json.loads(msg)
                
                if event.get("type") == "session.updated":
                    session_updated = True
                    # Check what modalities were actually accepted
                    session_modalities = event.get("session", {}).get("modalities", [])
                    print(f"[modalities:{','.join(session_modalities)}]", end=" ", flush=True)
                    
                    # Check if avatar was actually accepted
                    if "avatar" not in session_modalities:
                        await pc.close()
                        await ws.close()
                        return (f"{character_name}+{style}", False, "Avatar modality not supported by resource")
                        
                elif event.get("type") == "error":
                    error = event.get("error", {})
                    await pc.close()
                    await ws.close()
                    return (f"{character_name}+{style}", False, f"Config error: {error.get('message', 'Unknown')}")
            
            # Create REAL WebRTC offer using aiortc
            # Request downstream media only; the service sends streams to us
            pc.addTransceiver("audio", direction="recvonly")
            pc.addTransceiver("video", direction="recvonly")
            
            # Create and set local description (this generates ICE candidates)
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            
            # CRITICAL: Wait for ICE gathering to complete
            # The service expects a complete SDP with all ICE candidates
            print(f"[ICE gathering]", end=" ", flush=True)
            max_wait = 100  # 5 seconds max
            wait_count = 0
            ice_completed = pc.iceGatheringState == "complete"
            while not ice_completed and wait_count < max_wait:
                await asyncio.sleep(0.05)
                wait_count += 1
                ice_completed = pc.iceGatheringState == "complete"
            print(f"[{pc.iceGatheringState}]", end=" ", flush=True)
            
            # Get the SDP
            client_sdp = pc.localDescription.sdp
            
            # Debug: Check SDP format
            print(f"[SDP len:{len(client_sdp)}, lines:{client_sdp.count(chr(10))}]", end=" ", flush=True)

            # Encode SDP per current Voice Live API expectations
            sdp_payload = json.dumps({"type": "offer", "sdp": client_sdp})
            client_sdp_b64 = base64.b64encode(sdp_payload.encode()).decode()
            
            # Try with event_id (optional in some API versions)
            avatar_connect = {
                "type": "session.avatar.connect",
                "event_id": f"avatar_{int(asyncio.get_event_loop().time() * 1000)}",
                "client_sdp": client_sdp_b64,
                "rtc_configuration": {
                    "bundle_policy": "max-bundle"
                }
            }
            
            # Serialize to JSON
            json_msg = json.dumps(avatar_connect)
            print(f"[json_len:{len(json_msg)}]", flush=True)
            
            await ws.send(json_msg)
            
            # Wait for session.avatar.connecting (success) or error
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                event = json.loads(msg)
                
                # DEBUG: Print ALL events to see what we're getting
                print(f"\n[DEBUG Event: {event.get('type')}]", flush=True)
                if event.get("type") == "error":
                    print(f"[DEBUG Error details: {event.get('error')}]", flush=True)
                
                if event.get("type") == "session.avatar.connecting":
                    # Success! Character + style is valid and server returned SDP answer
                    server_sdp_raw = event.get("server_sdp", "")
                    
                    print(f"[DEBUG server_sdp type: {type(server_sdp_raw)}, starts with: {server_sdp_raw[:20] if server_sdp_raw else 'EMPTY'}]", flush=True)
                    
                    server_sdp = server_sdp_raw
                    answer_type = "answer"

                    # Decode server SDP: current API base64-encodes a JSON payload
                    if server_sdp_raw and not server_sdp_raw.startswith("v=0"):
                        try:
                            decoded_text = base64.b64decode(server_sdp_raw).decode()
                        except Exception:
                            decoded_text = ""

                        if decoded_text:
                            try:
                                parsed = json.loads(decoded_text)
                                server_sdp = parsed.get("sdp", "")
                                answer_type = parsed.get("type", "answer") or "answer"
                            except json.JSONDecodeError:
                                # Not JSON; treat decoded text as raw SDP
                                server_sdp = decoded_text

                    if not server_sdp:
                        await pc.close()
                        await ws.close()
                        return (f"{character_name}+{style}", False, "Error: Empty server SDP received")

                    # Set remote description to complete handshake
                    answer = RTCSessionDescription(sdp=server_sdp, type=answer_type)
                    try:
                        await pc.setRemoteDescription(answer)
                    except Exception as rtc_error:
                        await pc.close()
                        await ws.close()
                        return (f"{character_name}+{style}", False, f"Error setting remote description: {rtc_error}")
                    
                    await pc.close()
                    await ws.close()
                    style_str = f"+{style}" if style else ""
                    return (f"{character_name}{style_str}", True, "Valid character+style ✓")
                
                elif event.get("type") == "error":
                    error = event.get("error", {})
                    await pc.close()
                    await ws.close()
                    if error.get("code") == "avatar_verification_failed":
                        return (f"{character_name}+{style}", False, f"Not found: {error.get('message', 'Character not found')}")
                    else:
                        return (f"{character_name}+{style}", False, f"Error: {error.get('message', 'Unknown error')}")
                
                # Ignore other events (like rate_limits.updated) and keep waiting
                
    except asyncio.TimeoutError:
        await pc.close()
        return (f"{character_name}+{style}", False, "Timeout waiting for response")
    except Exception as e:
        await pc.close()
        return (f"{character_name}+{style}", False, f"Connection error: {str(e)}")

async def main():
    print("=" * 80)
    print("Testing Avatar Characters for Azure Voice Live")
    print("=" * 80)
    print(f"Endpoint: {os.getenv('AZURE_VOICE_LIVE_ENDPOINT')}")
    print(f"Testing {len(COMMON_CHARACTERS)} character + style combinations...")
    print("Using REAL WebRTC negotiation with aiortc\n")
    
    results = []
    for character, style in COMMON_CHARACTERS:
        style_str = f"+{style}" if style else " (no style)"
        print(f"Testing '{character}{style_str}'...", end=" ", flush=True)
        name, is_valid, message = await test_avatar_character(character, style)
        results.append((name, is_valid, message))
        if is_valid:
            print(f"[VALID]")  # Changed from ✓ for Windows console
        else:
            print(f"[X] {message}")  # Changed from ✗ for Windows console
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)
    
    print("\n" + "=" * 80)
    print("SUMMARY - Valid Characters:")
    print("=" * 80)
    valid_chars = [r for r in results if r[1]]
    if valid_chars:
        for name, _, _ in valid_chars:
            print(f"  [OK] {name}")  # Changed from ✓ for Windows console
        
        # Parse first valid result for env file
        first_valid = valid_chars[0][0]
        if "+" in first_valid:
            char, style = first_valid.split("+")
            print(f"\nUpdate your .env file:")
            print(f"AZURE_VOICE_AVATAR_CHARACTER={char}")
            print(f"AZURE_VOICE_AVATAR_STYLE={style}")
        else:
            print(f"\nUpdate your .env file:")
            print(f"AZURE_VOICE_AVATAR_CHARACTER={first_valid}")
    else:
        print("  No valid characters found from the common list.")
        print("\n  This could mean:")
        print("  1. Your Speech resource doesn't have avatar features enabled")
        print("  2. Avatars are not available in your region")
        print("  3. The character IDs use a different naming format")
        print("\n  Please check:")
        print("  - Azure Portal → Your Speech resource → Avatar section")
        print("  - https://speech.microsoft.com → Your resource → Avatar tab")
        print("  - Verify your resource is in a supported region (West US 2, West Europe, Southeast Asia, Central India)")
        print("  - Ensure you're using S0 (Standard) pricing tier")

if __name__ == "__main__":
    asyncio.run(main())
