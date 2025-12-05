"""
tts_streamer.py - ElevenLabs v2 WebSocket Streaming Handler

Connects to ElevenLabs streaming endpoint, sends text, and yields
audio chunks with character-level timestamps.
"""

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from typing import AsyncGenerator, Optional
import aiohttp
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@dataclass
class AudioChunk:
    """Represents a single audio chunk with timing data."""
    audio_bytes: bytes
    char_start_times_ms: list[int]  # Absolute times from utterance start
    char_durations_ms: list[int]
    chars: list[str]
    chunk_index: int
    is_final: bool = False


@dataclass
class StreamConfig:
    """Configuration for TTS streaming."""
    voice_id: str
    model_id: str = "eleven_flash_v2_5"  # Low-latency model
    optimize_streaming_latency: int = 4  # 0-4, higher = faster (4 for lowest latency)
    stability: float = 0.5
    similarity_boost: float = 0.75
    output_format: str = "pcm_22050"  # Raw PCM at 22050 Hz
    language_code: str = "ar"  # Arabic language code
    chunk_length_schedule: list[int] = None  # Will use [50, 150, 300, 300] by default


class ElevenLabsStreamer:
    """
    Handles streaming TTS from ElevenLabs v2 WebSocket API.
    
    Usage:
        streamer = ElevenLabsStreamer(config)
        async for chunk in streamer.stream_text("Hello world"):
            # Process chunk.audio_bytes and chunk.char_start_times_ms
    """
    
    BASE_URL = "wss://api.elevenlabs.io/v1/text-to-speech"
    
    def __init__(self, config: StreamConfig):
        self.config = config
        self.api_key = os.getenv("ELEVEN_API_KEY")
        if not self.api_key:
            raise ValueError("ELEVEN_API_KEY not set in environment")
    
    def _build_ws_url(self) -> str:
        """Construct the WebSocket URL with query parameters."""
        return (
            f"{self.BASE_URL}/{self.config.voice_id}/stream-input"
            f"?model_id={self.config.model_id}"
            f"&output_format={self.config.output_format}"
            f"&optimize_streaming_latency={self.config.optimize_streaming_latency}"
            f"&sync_alignment=true"  # Enable character-level timing
        )
    
    async def stream_text(self, text: str) -> AsyncGenerator[AudioChunk, None]:
        """
        Stream TTS for the given text, yielding AudioChunks.
        
        Each chunk contains:
        - audio_bytes: Raw PCM audio data
        - char_start_times_ms: Absolute start time for each character
        - char_durations_ms: Duration of each character
        - chars: The characters in this chunk
        """
        url = self._build_ws_url()
        offset_ms = 0  # Tracks cumulative audio duration
        chunk_index = 0
        
        logger.info(f"Connecting to ElevenLabs WebSocket: {url}")
        
        try:
            # Use aiohttp for WebSocket connection (handles headers properly)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    url,
                    headers={"xi-api-key": self.api_key}
                ) as ws:
                    # Send initial message with text and settings
                    init_message = {
                        "text": text,
                        "voice_settings": {
                            "stability": self.config.stability,
                            "similarity_boost": self.config.similarity_boost,
                        },
                        "generation_config": {
                            "chunk_length_schedule": [120, 160, 250, 290],
                        },
                        "xi_api_key": self.api_key,
                        "try_trigger_generation": True,
                    }
                    
                    logger.debug(f"Sending init message: {json.dumps(init_message)[:200]}...")
                    await ws.send_json(init_message)
                    
                    # Send end-of-input signal
                    await ws.send_json({"text": ""})
                    logger.debug("Sent end-of-input signal")
                    
                    # Receive streaming chunks
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to decode JSON: {msg.data[:100]}")
                                continue
                            
                            # Check for errors
                            if "error" in data:
                                logger.error(f"ElevenLabs error: {data['error']}")
                                raise Exception(f"ElevenLabs API error: {data['error']}")
                            
                            audio_b64 = data.get("audio")
                            is_final = data.get("isFinal", False)
                            
                            if is_final:
                                logger.info("Received final chunk marker")
                                break
                            
                            if not audio_b64:
                                # Could be a status message, continue
                                logger.debug(f"Non-audio message: {str(data)[:100]}")
                                continue
                            
                            # Decode audio
                            audio_bytes = base64.b64decode(audio_b64)
                            
                            # Extract alignment data
                            # ElevenLabs provides normalizedAlignment with character timings
                            alignment = data.get("normalizedAlignment") or data.get("alignment") or {}
                            if alignment is None:
                                alignment = {}
                            chunk_char_times = alignment.get("charStartTimesMs") or alignment.get("char_start_times_ms") or []
                            chunk_char_durations = alignment.get("charDurationsMs") or alignment.get("char_durations_ms") or []
                            chunk_chars = alignment.get("chars") or []
                            
                            # Convert to absolute times (add offset)
                            absolute_times = [t + offset_ms for t in chunk_char_times]
                            
                            # Calculate chunk duration for next offset
                            # Based on audio bytes: PCM 16-bit mono at 22050 Hz
                            # bytes / (2 bytes per sample * 22050 samples/sec) * 1000 ms/sec
                            audio_duration_ms = int(len(audio_bytes) / (2 * 22050) * 1000)
                            offset_ms += audio_duration_ms
                            
                            logger.debug(
                                f"Chunk {chunk_index}: {len(audio_bytes)} bytes, "
                                f"{len(chunk_chars)} chars, duration: {audio_duration_ms}ms"
                            )
                            
                            yield AudioChunk(
                                audio_bytes=audio_bytes,
                                char_start_times_ms=absolute_times,
                                char_durations_ms=chunk_char_durations,
                                chars=chunk_chars,
                                chunk_index=chunk_index,
                            )
                            chunk_index += 1
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"WebSocket error: {ws.exception()}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            logger.info("WebSocket closed by server")
                            break
                    
                    # Yield final marker
                    logger.info(f"Stream complete. Total chunks: {chunk_index}")
                    yield AudioChunk(
                        audio_bytes=b"",
                        char_start_times_ms=[],
                        char_durations_ms=[],
                        chars=[],
                        chunk_index=chunk_index,
                        is_final=True,
                    )
                
        except aiohttp.ClientError as e:
            logger.error(f"WebSocket connection error: {e}")
            raise
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise


class ElevenLabsStreamingSession:
    """
    Maintains an open WebSocket connection to ElevenLabs for streaming input.
    
    This allows sending text chunks as they arrive from the LLM,
    while audio chunks are streamed back continuously.
    
    Usage:
        session = ElevenLabsStreamingSession(config)
        await session.start()
        
        # In one task: send text chunks
        await session.send_text("Hello ")
        await session.send_text("world!")
        await session.finish()
        
        # In another task: receive audio
        async for chunk in session.receive_audio():
            # Process chunk
    """
    
    BASE_URL = "wss://api.elevenlabs.io/v1/text-to-speech"
    
    def __init__(self, config: StreamConfig):
        self.config = config
        self.api_key = os.getenv("ELEVEN_API_KEY")
        if not self.api_key:
            raise ValueError("ELEVEN_API_KEY not set in environment")
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._started = False
        self._finished = False
        self._offset_ms = 0
        self._chunk_index = 0
        self._first_text_sent = False
    
    def _build_ws_url(self) -> str:
        """Construct the WebSocket URL with query parameters."""
        return (
            f"{self.BASE_URL}/{self.config.voice_id}/stream-input"
            f"?model_id={self.config.model_id}"
            f"&output_format={self.config.output_format}"
            f"&optimize_streaming_latency={self.config.optimize_streaming_latency}"
            f"&sync_alignment=true"  # Enable character-level timing
        )
    
    async def start(self):
        """Open the WebSocket connection."""
        if self._started:
            return
        
        url = self._build_ws_url()
        logger.info(f"Starting streaming session to: {url}")
        
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            url,
            headers={"xi-api-key": self.api_key}
        )
        self._started = True
        logger.info("Streaming session started")
    
    async def send_text(self, text: str):
        """Send a text chunk to be synthesized."""
        if not self._started or self._ws is None:
            raise RuntimeError("Session not started")
        if self._finished:
            raise RuntimeError("Session already finished")
        
        # Smart try_trigger_generation usage:
        # - Always trigger on first chunk to start audio quickly
        # - Don't trigger on subsequent chunks unless necessary
        # - Let ElevenLabs handle scheduling based on chunk_length_schedule
        use_try_trigger = not self._first_text_sent
        
        message = {
            "text": text,
            "try_trigger_generation": use_try_trigger,
        }
        
        # Include voice settings and config on first message
        if not self._first_text_sent:
            message["voice_settings"] = {
                "stability": self.config.stability,
                "similarity_boost": self.config.similarity_boost,
            }
            message["generation_config"] = {
                "chunk_length_schedule": self.config.chunk_length_schedule,
            }
            message["xi_api_key"] = self.api_key
            self._first_text_sent = True
        
        logger.debug(f"Sending text chunk: {text[:50]}...")
        await self._ws.send_json(message)
    
    async def finish(self):
        """Signal end of text input."""
        if not self._started or self._ws is None:
            return
        if self._finished:
            return
        
        # Send empty string to signal end of input
        await self._ws.send_json({"text": ""})
        self._finished = True
        logger.info("Sent end-of-input signal")
    
    async def receive_audio(self) -> AsyncGenerator[AudioChunk, None]:
        """Receive audio chunks from the WebSocket."""
        if not self._started or self._ws is None:
            raise RuntimeError("Session not started")
        
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to decode JSON: {msg.data[:100]}")
                    continue
                
                # Check for errors
                if "error" in data:
                    logger.error(f"ElevenLabs error: {data['error']}")
                    raise Exception(f"ElevenLabs API error: {data['error']}")
                
                audio_b64 = data.get("audio")
                is_final = data.get("isFinal", False)
                
                if is_final:
                    logger.info("Received final chunk marker")
                    break
                
                if not audio_b64:
                    logger.debug(f"Non-audio message: {str(data)[:100]}")
                    continue
                
                # Decode audio
                audio_bytes = base64.b64decode(audio_b64)
                
                # Extract alignment data
                alignment = data.get("normalizedAlignment") or data.get("alignment") or {}
                if alignment is None:
                    alignment = {}
                chunk_char_times = alignment.get("charStartTimesMs") or alignment.get("char_start_times_ms") or []
                chunk_char_durations = alignment.get("charDurationsMs") or alignment.get("char_durations_ms") or []
                chunk_chars = alignment.get("chars") or []
                
                # Convert to absolute times
                absolute_times = [t + self._offset_ms for t in chunk_char_times]
                
                # Calculate chunk duration
                audio_duration_ms = int(len(audio_bytes) / (2 * 22050) * 1000)
                self._offset_ms += audio_duration_ms
                
                logger.debug(
                    f"Chunk {self._chunk_index}: {len(audio_bytes)} bytes, "
                    f"{len(chunk_chars)} chars, duration: {audio_duration_ms}ms"
                )
                
                yield AudioChunk(
                    audio_bytes=audio_bytes,
                    char_start_times_ms=absolute_times,
                    char_durations_ms=chunk_char_durations,
                    chars=chunk_chars,
                    chunk_index=self._chunk_index,
                )
                self._chunk_index += 1
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {self._ws.exception()}")
                break
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                logger.info("WebSocket closed by server")
                break
        
        # Yield final marker
        yield AudioChunk(
            audio_bytes=b"",
            char_start_times_ms=[],
            char_durations_ms=[],
            chars=[],
            chunk_index=self._chunk_index,
            is_final=True,
        )
    
    async def close(self):
        """Close the session."""
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        self._started = False
        logger.info("Streaming session closed")


# Convenience function for quick streaming
async def stream_tts(
    text: str, 
    voice_id: str, 
    model_id: str = "eleven_flash_v2_5",
    language_code: str = "ar"
) -> AsyncGenerator[AudioChunk, None]:
    """Simple wrapper for streaming TTS."""
    config = StreamConfig(
        voice_id=voice_id, 
        model_id=model_id,
        language_code=language_code,
        chunk_length_schedule=[50, 150, 300, 300]  # Optimized schedule
    )
    streamer = ElevenLabsStreamer(config)
    async for chunk in streamer.stream_text(text):
        yield chunk


# Test function
async def _test_stream():
    """Test the streamer with a sample text."""
    voice_id = os.getenv("ELEVEN_VOICE_ID")
    if not voice_id:
        print("ELEVEN_VOICE_ID not set")
        return
    
    test_text = "مرحبا بالعالم"  # "Hello world" in Arabic
    print(f"Testing with text: {test_text}")
    
    config = StreamConfig(voice_id=voice_id)
    streamer = ElevenLabsStreamer(config)
    
    total_bytes = 0
    total_chars = 0
    
    async for chunk in streamer.stream_text(test_text):
        if chunk.is_final:
            print(f"\nStream complete! Total: {total_bytes} bytes, {total_chars} chars")
            break
        
        total_bytes += len(chunk.audio_bytes)
        total_chars += len(chunk.chars)
        print(f"Chunk {chunk.chunk_index}: {len(chunk.audio_bytes)} bytes, chars: {''.join(chunk.chars)}")


if __name__ == "__main__":
    asyncio.run(_test_stream())
