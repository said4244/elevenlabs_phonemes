"""
ws_server.py - WebSocket Server for TTS Streaming with Broadcasting

Architecture:
- TWO types of clients: AGENT (sends text) and FLUTTER (receives audio)
- Agent connects to /agent endpoint and sends text chunks
- Flutter connects to /client endpoint and receives audio
- ONE ElevenLabs session per response, audio broadcast to ALL Flutter clients

Flow:
1. Flutter connects to /client (waits for audio)
2. Agent connects to /agent
3. Agent sends append_tts messages
4. Server opens ONE ElevenLabs session
5. Audio chunks broadcast to ALL connected Flutter clients
6. Agent sends finish_tts
7. Server completes and sends "complete" to all Flutter clients
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from tts_streamer import ElevenLabsStreamer, ElevenLabsStreamingSession, StreamConfig, AudioChunk
from file_logger import AlignmentLogger

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tts_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TTS Streaming Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
VOICE_ID = os.getenv("ELEVEN_VOICE_ID")
MODEL_ID = os.getenv("ELEVEN_MODEL", "eleven_flash_v2_5")
OPTIMIZE_LATENCY = int(os.getenv("ELEVEN_OPTIMIZE_LATENCY", "4"))
LOG_ALIGNMENT = os.getenv("LOG_ALIGNMENT", "true").lower() == "true"


class TTSBroadcaster:
    """
    Singleton that manages TTS streaming and broadcasts to all clients.
    """
    
    def __init__(self):
        self._flutter_clients: Set[WebSocket] = set()
        self._agent_ws: Optional[WebSocket] = None
        
        # Current TTS session
        self._el_session: Optional[ElevenLabsStreamingSession] = None
        self._audio_task: Optional[asyncio.Task] = None
        self._is_streaming = False
        self._lock = asyncio.Lock()
        
        # Tracking for current response
        self._full_text = ""
        self._all_chars: list[str] = []
        self._all_times: list[int] = []
        self._all_durations: list[int] = []
        self._total_audio_ms = 0
        self._chunk_count = 0
        self._start_time: Optional[datetime] = None
        
        # Alignment logger
        self._alignment_logger = AlignmentLogger() if LOG_ALIGNMENT else None
        
        logger.info("TTSBroadcaster initialized")
    
    def add_flutter_client(self, ws: WebSocket):
        """Register a Flutter client to receive audio."""
        self._flutter_clients.add(ws)
        logger.info(f"Flutter client added. Total: {len(self._flutter_clients)}")
    
    def remove_flutter_client(self, ws: WebSocket):
        """Unregister a Flutter client."""
        self._flutter_clients.discard(ws)
        logger.info(f"Flutter client removed. Total: {len(self._flutter_clients)}")
    
    def set_agent(self, ws: WebSocket):
        """Set the agent WebSocket."""
        self._agent_ws = ws
        logger.info("Agent connected")
    
    def clear_agent(self):
        """Clear the agent WebSocket."""
        self._agent_ws = None
        logger.info("Agent disconnected")
    
    async def broadcast_to_clients(self, message: dict):
        """Broadcast a message to all Flutter clients."""
        if not self._flutter_clients:
            logger.warning("No Flutter clients to broadcast to")
            return
        
        disconnected = set()
        for client in self._flutter_clients:
            try:
                await client.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send to client: {e}")
                disconnected.add(client)
        
        # Remove disconnected clients
        self._flutter_clients -= disconnected
    
    async def handle_append_text(self, text: str):
        """Handle text chunk from agent."""
        async with self._lock:
            if not self._is_streaming:
                await self._start_session()
            
            if self._el_session:
                await self._el_session.send_text(text)
                self._full_text += text
                logger.debug(f"Appended text: {text[:50]}...")
    
    async def handle_finish(self):
        """Handle finish signal from agent."""
        async with self._lock:
            if not self._is_streaming or not self._el_session:
                logger.warning("Finish called but no active session")
                return
            
            logger.info("Finishing TTS stream")
            await self._el_session.finish()
            
            # Wait for audio task to complete
            if self._audio_task:
                try:
                    await asyncio.wait_for(self._audio_task, timeout=60.0)
                except asyncio.TimeoutError:
                    logger.error("Audio task timed out")
                    self._audio_task.cancel()
            
            # Log alignment
            if self._alignment_logger and self._full_text:
                self._alignment_logger.save_alignment(
                    text=self._full_text,
                    chars=self._all_chars,
                    times=self._all_times,
                    durations=self._all_durations,
                )
            
            # Broadcast completion
            await self.broadcast_to_clients({
                "type": "complete",
                "text": self._full_text,
                "total_chars": len(self._all_chars),
                "total_duration_ms": self._total_audio_ms,
            })
            
            elapsed = (datetime.now() - self._start_time).total_seconds() if self._start_time else 0
            logger.info(f"Session complete. Chunks: {self._chunk_count}, Duration: {self._total_audio_ms}ms, Time: {elapsed:.2f}s")
            
            await self._cleanup()
    
    async def _start_session(self):
        """Start a new ElevenLabs streaming session."""
        if self._is_streaming:
            await self._cleanup()
        
        # Reset state
        self._full_text = ""
        self._all_chars = []
        self._all_times = []
        self._all_durations = []
        self._total_audio_ms = 0
        self._chunk_count = 0
        self._start_time = datetime.now()
        
        # Create ElevenLabs session
        config = StreamConfig(
            voice_id=VOICE_ID,
            model_id=MODEL_ID,
            optimize_streaming_latency=OPTIMIZE_LATENCY,
        )
        self._el_session = ElevenLabsStreamingSession(config)
        await self._el_session.start()
        
        # Start background audio receiver
        self._audio_task = asyncio.create_task(self._receive_and_broadcast_audio())
        
        self._is_streaming = True
        logger.info("Started new ElevenLabs session")
    
    async def _receive_and_broadcast_audio(self):
        """Receive audio from ElevenLabs and broadcast to all Flutter clients."""
        try:
            first_chunk = True
            
            async for chunk in self._el_session.receive_audio():
                if chunk.is_final:
                    logger.info("Received final audio marker")
                    break
                
                # Log first chunk timing
                if first_chunk and self._start_time:
                    ttfa = (datetime.now() - self._start_time).total_seconds() * 1000
                    logger.info(f"Time to first audio: {ttfa:.0f}ms")
                    first_chunk = False
                
                # Accumulate alignment data
                self._all_chars.extend(chunk.chars)
                self._all_times.extend(chunk.char_start_times_ms)
                self._all_durations.extend(chunk.char_durations_ms)
                
                # Calculate audio duration
                audio_duration_ms = int(len(chunk.audio_bytes) / (2 * 22050) * 1000)
                self._total_audio_ms += audio_duration_ms
                
                # Broadcast to all Flutter clients
                await self.broadcast_to_clients({
                    "type": "chunk",
                    "audio": base64.b64encode(chunk.audio_bytes).decode("utf-8"),
                    "char_times": chunk.char_start_times_ms,
                    "char_durations": chunk.char_durations_ms,
                    "chars": chunk.chars,
                    "chunk_index": self._chunk_count,
                })
                
                self._chunk_count += 1
                logger.debug(f"Broadcast chunk {self._chunk_count}: {len(chunk.audio_bytes)} bytes")
        
        except asyncio.CancelledError:
            logger.info("Audio task cancelled")
        except Exception as e:
            logger.error(f"Error receiving audio: {e}", exc_info=True)
            await self.broadcast_to_clients({"type": "error", "message": str(e)})
    
    async def _cleanup(self):
        """Clean up current session."""
        if self._audio_task:
            self._audio_task.cancel()
            try:
                await self._audio_task
            except asyncio.CancelledError:
                pass
            self._audio_task = None
        
        if self._el_session:
            await self._el_session.close()
            self._el_session = None
        
        self._is_streaming = False
        logger.info("Session cleaned up")
    
    async def handle_single_shot(self, ws: WebSocket, text: str):
        """Handle single-shot TTS request (direct from Flutter)."""
        logger.info(f"Single-shot TTS: {text[:50]}...")
        
        config = StreamConfig(
            voice_id=VOICE_ID,
            model_id=MODEL_ID,
            optimize_streaming_latency=OPTIMIZE_LATENCY,
        )
        streamer = ElevenLabsStreamer(config)
        
        start_time = datetime.now()
        chars = []
        times = []
        durations = []
        total_ms = 0
        chunk_count = 0
        
        try:
            async for chunk in streamer.stream_text(text):
                if chunk.is_final:
                    break
                
                if chunk_count == 0:
                    ttfa = (datetime.now() - start_time).total_seconds() * 1000
                    logger.info(f"First chunk in {ttfa:.0f}ms")
                
                chars.extend(chunk.chars)
                times.extend(chunk.char_start_times_ms)
                durations.extend(chunk.char_durations_ms)
                
                audio_duration_ms = int(len(chunk.audio_bytes) / (2 * 22050) * 1000)
                total_ms += audio_duration_ms
                
                await ws.send_json({
                    "type": "chunk",
                    "audio": base64.b64encode(chunk.audio_bytes).decode("utf-8"),
                    "char_times": chunk.char_start_times_ms,
                    "char_durations": chunk.char_durations_ms,
                    "chars": chunk.chars,
                    "chunk_index": chunk_count,
                })
                chunk_count += 1
            
            if self._alignment_logger:
                self._alignment_logger.save_alignment(
                    text=text,
                    chars=chars,
                    times=times,
                    durations=durations,
                )
            
            await ws.send_json({
                "type": "complete",
                "text": text,
                "total_chars": len(chars),
                "total_duration_ms": total_ms,
            })
            
            logger.info(f"Single-shot complete. Duration: {total_ms}ms")
        
        except Exception as e:
            logger.error(f"Single-shot error: {e}")
            await ws.send_json({"type": "error", "message": str(e)})


# Global broadcaster
broadcaster = TTSBroadcaster()


@app.websocket("/client")
async def flutter_client_endpoint(websocket: WebSocket):
    """
    Endpoint for Flutter clients to receive TTS audio.
    Flutter connects here and receives audio chunks broadcast from agent's TTS.
    """
    await websocket.accept()
    broadcaster.add_flutter_client(websocket)
    logger.info("Flutter client connected to /client")
    
    try:
        while True:
            try:
                data = await websocket.receive_json()
                action = data.get("action")
                
                if action == "start_tts":
                    # Single-shot TTS directly from Flutter
                    text = data.get("text", "")
                    if text:
                        await broadcaster.handle_single_shot(websocket, text)
                
                elif action == "ping":
                    await websocket.send_json({"type": "pong"})
                
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
    
    except WebSocketDisconnect:
        logger.info("Flutter client disconnected")
    finally:
        broadcaster.remove_flutter_client(websocket)


@app.websocket("/agent")
async def agent_endpoint(websocket: WebSocket):
    """
    Endpoint for the LiveKit agent to send TTS text.
    Agent sends text chunks here, audio is broadcast to Flutter clients.
    """
    await websocket.accept()
    broadcaster.set_agent(websocket)
    logger.info("Agent connected to /agent")
    
    try:
        while True:
            try:
                data = await websocket.receive_json()
                action = data.get("action")
                
                if action == "append_tts":
                    text = data.get("text", "")
                    if text:
                        await broadcaster.handle_append_text(text)
                
                elif action == "finish_tts":
                    await broadcaster.handle_finish()
                
                elif action == "ping":
                    await websocket.send_json({"type": "pong"})
                
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
    
    except WebSocketDisconnect:
        logger.info("Agent disconnected")
    finally:
        broadcaster.clear_agent()


# Keep the old endpoint for backwards compatibility
@app.websocket("/stream_tts")
async def legacy_endpoint(websocket: WebSocket):
    """
    Legacy endpoint - acts as /client by default.
    If it receives append_tts, it acts as /agent.
    """
    await websocket.accept()
    is_agent = False
    broadcaster.add_flutter_client(websocket)
    logger.info("Client connected to /stream_tts (legacy)")
    
    try:
        while True:
            try:
                data = await websocket.receive_json()
                action = data.get("action")
                
                if action == "append_tts":
                    # This is an agent connection
                    if not is_agent:
                        is_agent = True
                        broadcaster.remove_flutter_client(websocket)
                        broadcaster.set_agent(websocket)
                        logger.info("Legacy client promoted to agent")
                    
                    text = data.get("text", "")
                    if text:
                        await broadcaster.handle_append_text(text)
                
                elif action == "finish_tts":
                    await broadcaster.handle_finish()
                
                elif action == "start_tts":
                    text = data.get("text", "")
                    if text:
                        await broadcaster.handle_single_shot(websocket, text)
                
                elif action == "ping":
                    await websocket.send_json({"type": "pong"})
                
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
    
    except WebSocketDisconnect:
        logger.info("Legacy client disconnected")
    finally:
        if is_agent:
            broadcaster.clear_agent()
        else:
            broadcaster.remove_flutter_client(websocket)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "flutter_clients": len(broadcaster._flutter_clients),
        "agent_connected": broadcaster._agent_ws is not None,
        "is_streaming": broadcaster._is_streaming,
    }


if __name__ == "__main__":
    import uvicorn
    
    if not VOICE_ID:
        logger.error("ELEVEN_VOICE_ID not set!")
        exit(1)
    
    port = int(os.getenv("TTS_WS_PORT", 8081))
    logger.info(f"Starting TTS server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
