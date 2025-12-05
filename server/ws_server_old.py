"""
ws_server.py - Client-facing WebSocket Server

Accepts connections from Flutter clients AND the LiveKit agent.
Streams TTS audio with alignment data from ElevenLabs.

Architecture:
- Agent connects and sends text chunks as LLM streams (append_tts)
- Server maintains ONE continuous stream to ElevenLabs
- Audio chunks flow back to Flutter in real-time
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from typing import Optional
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
        logging.FileHandler('tts_server_debug.log'),
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
OPTIMIZE_LATENCY = int(os.getenv("ELEVEN_OPTIMIZE_LATENCY", "1"))
LOG_ALIGNMENT = os.getenv("LOG_ALIGNMENT", "true").lower() == "true"


class TTSSession:
    """Manages a single TTS streaming session with support for streaming input."""
    
    def __init__(self, websocket: WebSocket, session_id: str):
        self.websocket = websocket
        self.session_id = session_id
        self.alignment_logger = AlignmentLogger() if LOG_ALIGNMENT else None
        
        # Accumulated data
        self.accumulated_chars: list[str] = []
        self.accumulated_times: list[int] = []
        self.accumulated_durations: list[int] = []
        self.accumulated_text: str = ""
        
        # Streaming session to ElevenLabs
        self._streaming_session: Optional[ElevenLabsStreamingSession] = None
        self._audio_task: Optional[asyncio.Task] = None
        self._is_streaming = False
        self._stream_lock = asyncio.Lock()
        
        self.time_offset_ms: int = 0
        self.global_chunk_index: int = 0
    
    async def start_streaming_session(self):
        """Start a new streaming session to ElevenLabs."""
        async with self._stream_lock:
            # Close any existing session
            await self._cleanup_session()
            
            # Reset accumulators
            self.accumulated_chars.clear()
            self.accumulated_times.clear()
            self.accumulated_durations.clear()
            self.accumulated_text = ""
            self.time_offset_ms = 0
            self.global_chunk_index = 0
            
            # Create new streaming session
            config = StreamConfig(
                voice_id=VOICE_ID,
                model_id=MODEL_ID,
                optimize_streaming_latency=OPTIMIZE_LATENCY,
            )
            self._streaming_session = ElevenLabsStreamingSession(config)
            await self._streaming_session.start()
            self._is_streaming = True
            
            # Start background task to receive and forward audio
            self._audio_task = asyncio.create_task(self._forward_audio())
            
            logger.info(f"Session {self.session_id}: Started streaming session to ElevenLabs")
    
    async def _cleanup_session(self):
        """Clean up existing session."""
        if self._audio_task:
            self._audio_task.cancel()
            try:
                await self._audio_task
            except asyncio.CancelledError:
                pass
            self._audio_task = None
        
        if self._streaming_session:
            await self._streaming_session.close()
            self._streaming_session = None
        
        self._is_streaming = False
    
    async def append_text(self, text: str):
        """Append text to the ongoing streaming session."""
        if not self._streaming_session or not self._is_streaming:
            await self.start_streaming_session()
        
        self.accumulated_text += text
        await self._streaming_session.send_text(text)
        logger.info(f"Session {self.session_id}: Appended text: {text[:50]}...")
    
    async def finish_streaming(self):
        """Finish the current streaming session."""
        if not self._streaming_session or not self._is_streaming:
            logger.warning(f"Session {self.session_id}: finish called but no active stream")
            return
        
        logger.info(f"Session {self.session_id}: Finishing streaming input")
        await self._streaming_session.finish()
        
        # Wait for audio task to complete
        if self._audio_task:
            try:
                await asyncio.wait_for(self._audio_task, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning(f"Session {self.session_id}: Audio task timed out")
                self._audio_task.cancel()
        
        self._is_streaming = False
        
        # Log alignment if enabled
        if self.alignment_logger and self.accumulated_text:
            self.alignment_logger.save_alignment(
                text=self.accumulated_text,
                chars=self.accumulated_chars,
                times=self.accumulated_times,
                durations=self.accumulated_durations,
            )
        
        # Send completion signal to client
        await self.websocket.send_json({
            "type": "complete",
            "total_chars": len(self.accumulated_chars),
            "total_duration_ms": self.time_offset_ms,
            "text": self.accumulated_text,
        })
        
        logger.info(f"Session {self.session_id}: Stream complete. Duration: {self.time_offset_ms}ms")
    
    async def _forward_audio(self):
        """Background task that receives audio from ElevenLabs and forwards to client."""
        try:
            start_time = datetime.now()
            chunk_count = 0
            
            async for chunk in self._streaming_session.receive_audio():
                if chunk.is_final:
                    logger.info(f"Session {self.session_id}: Received final audio marker")
                    break
                
                # Accumulate alignment data
                self.accumulated_chars.extend(chunk.chars)
                self.accumulated_times.extend(chunk.char_start_times_ms)
                self.accumulated_durations.extend(chunk.char_durations_ms)
                
                # Track timing
                if chunk_count == 0:
                    first_chunk_time = (datetime.now() - start_time).total_seconds() * 1000
                    logger.info(f"Session {self.session_id}: First audio chunk in {first_chunk_time:.0f}ms")
                
                # Update time offset
                audio_duration_ms = int(len(chunk.audio_bytes) / (2 * 22050) * 1000)
                self.time_offset_ms += audio_duration_ms
                
                # Send chunk to client
                await self.websocket.send_json({
                    "type": "chunk",
                    "audio": base64.b64encode(chunk.audio_bytes).decode("utf-8"),
                    "char_times": chunk.char_start_times_ms,
                    "char_durations": chunk.char_durations_ms,
                    "chars": chunk.chars,
                    "chunk_index": self.global_chunk_index,
                })
                
                self.global_chunk_index += 1
                chunk_count += 1
            
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"Session {self.session_id}: Audio complete. Chunks: {chunk_count}, Time: {elapsed:.2f}s")
            
        except asyncio.CancelledError:
            logger.info(f"Session {self.session_id}: Audio forwarding cancelled")
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error forwarding audio: {e}")
            try:
                await self.websocket.send_json({
                    "type": "error",
                    "message": str(e),
                })
            except:
                pass
    
    async def single_shot_tts(self, text: str):
        """Handle single-shot TTS (start_tts action from Flutter)."""
        # For single shot, we use the non-streaming approach
        logger.info(f"Session {self.session_id}: Single-shot TTS for: {text[:50]}...")
        
        # Reset state
        self.accumulated_chars.clear()
        self.accumulated_times.clear()
        self.accumulated_durations.clear()
        self.accumulated_text = text
        self.time_offset_ms = 0
        self.global_chunk_index = 0
        
        config = StreamConfig(
            voice_id=VOICE_ID,
            model_id=MODEL_ID,
            optimize_streaming_latency=OPTIMIZE_LATENCY,
        )
        streamer = ElevenLabsStreamer(config)
        
        start_time = datetime.now()
        chunk_count = 0
        
        try:
            async for chunk in streamer.stream_text(text):
                if chunk.is_final:
                    break
                
                # Accumulate
                self.accumulated_chars.extend(chunk.chars)
                self.accumulated_times.extend(chunk.char_start_times_ms)
                self.accumulated_durations.extend(chunk.char_durations_ms)
                
                if chunk_count == 0:
                    first_chunk_time = (datetime.now() - start_time).total_seconds() * 1000
                    logger.info(f"Session {self.session_id}: First chunk in {first_chunk_time:.0f}ms")
                
                audio_duration_ms = int(len(chunk.audio_bytes) / (2 * 22050) * 1000)
                self.time_offset_ms += audio_duration_ms
                
                await self.websocket.send_json({
                    "type": "chunk",
                    "audio": base64.b64encode(chunk.audio_bytes).decode("utf-8"),
                    "char_times": chunk.char_start_times_ms,
                    "char_durations": chunk.char_durations_ms,
                    "chars": chunk.chars,
                    "chunk_index": self.global_chunk_index,
                })
                
                self.global_chunk_index += 1
                chunk_count += 1
            
            # Log alignment
            if self.alignment_logger:
                self.alignment_logger.save_alignment(
                    text=self.accumulated_text,
                    chars=self.accumulated_chars,
                    times=self.accumulated_times,
                    durations=self.accumulated_durations,
                )
            
            # Send complete
            await self.websocket.send_json({
                "type": "complete",
                "total_chars": len(self.accumulated_chars),
                "total_duration_ms": self.time_offset_ms,
                "text": self.accumulated_text,
            })
            
            logger.info(f"Session {self.session_id}: Single-shot complete. Duration: {self.time_offset_ms}ms")
            
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error in single-shot TTS: {e}")
            await self.websocket.send_json({
                "type": "error",
                "message": str(e),
            })


# Active sessions tracking
active_sessions: dict[str, TTSSession] = {}
session_counter = 0


@app.websocket("/stream_tts")
async def websocket_tts_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for TTS streaming.
    
    Supports two modes:
    1. Single text: {"action": "start_tts", "text": "..."} - Flutter direct TTS
    2. Streaming: {"action": "append_tts", "text": "..."} then {"action": "finish_tts"} - Agent streaming
    """
    global session_counter
    
    await websocket.accept()
    
    session_counter += 1
    session_id = f"session_{session_counter}"
    
    logger.info(f"Client connected: {session_id}")
    
    session = TTSSession(websocket, session_id)
    active_sessions[session_id] = session
    
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except json.JSONDecodeError as e:
                logger.error(f"Session {session_id}: Invalid JSON: {e}")
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue
            
            action = data.get("action")
            logger.debug(f"Session {session_id}: Action: {action}")
            
            if action == "start_tts":
                # Single-shot TTS from Flutter
                text = data.get("text", "")
                if text:
                    await session.single_shot_tts(text)
                else:
                    await websocket.send_json({"type": "error", "message": "No text provided"})
            
            elif action == "append_tts":
                # Streaming text from agent
                text = data.get("text", "")
                if text:
                    await session.append_text(text)
            
            elif action == "finish_tts":
                # End of streaming from agent
                await session.finish_streaming()
            
            elif action == "stop":
                await session._cleanup_session()
                await websocket.send_json({"type": "stopped"})
            
            elif action == "ping":
                await websocket.send_json({"type": "pong"})
            
            else:
                logger.warning(f"Session {session_id}: Unknown action: {action}")
                await websocket.send_json({"type": "error", "message": f"Unknown action: {action}"})
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Session {session_id}: WebSocket error: {e}")
    finally:
        await session._cleanup_session()
        if session_id in active_sessions:
            del active_sessions[session_id]


@app.get("/health")
async def health():
    return {"status": "healthy", "active_sessions": len(active_sessions)}


if __name__ == "__main__":
    import uvicorn
    
    if not VOICE_ID:
        logger.error("ELEVEN_VOICE_ID not set!")
        exit(1)
    
    port = int(os.getenv("TTS_WS_PORT", 8081))
    logger.info(f"Starting TTS server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
