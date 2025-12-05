"""
agent_direct.py - LiveKit Agent with Direct LLM-to-TTS Streaming

This agent completely bypasses LiveKit's TTS pipeline:
1. Uses LiveKit's VAD/STT for user input
2. Calls OpenAI LLM directly (not through AgentSession)
3. Streams LLM output tokens directly to ONE ElevenLabs session via ws_server.py
4. Audio comes from ws_server.py to Flutter, NOT through LiveKit

KEY DIFFERENCE: LiveKit's AgentSession splits responses into sentences and calls
tts.synthesize() for each one - causing multiple overlapping TTS requests.
This agent streams the entire LLM response as ONE continuous TTS stream.
"""

import logging
import asyncio
import json
import os
from typing import Optional, List
import aiohttp
from openai import AsyncOpenAI
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents.vad import VADEventType
from livekit.plugins import silero
from livekit.plugins import openai as lk_openai

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('agent_direct_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TTS_WS_URL = os.getenv("TTS_WS_URL", "ws://localhost:8081/agent")

# System prompt
SYSTEM_PROMPT = """Talk like a normal Arab human,
Use the Syrian dialect exclusively. Keep responses concise. Keep answers as short as possible"""


class DirectTTSClient:
    """
    Client that connects to ws_server.py and streams text for TTS.
    Maintains ONE continuous connection per response.
    """
    
    def __init__(self, ws_url: str, buffer_threshold: int = 50):
        self.ws_url = ws_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._connected = False
        self._lock = asyncio.Lock()
        
        # Text buffering to prevent overlapping audio
        self._buffer = ""
        self._buffer_threshold = buffer_threshold  # Minimum chars before flushing
        self._first_chunk_sent = False
    
    async def connect(self):
        """Connect to the TTS WebSocket server."""
        async with self._lock:
            if self._connected:
                return
            
            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(self.ws_url)
                self._connected = True
                logger.info(f"Connected to TTS server: {self.ws_url}")
            except Exception as e:
                logger.error(f"Failed to connect to TTS server: {e}")
                raise
    
    async def ensure_connected(self):
        """Ensure connection is established."""
        if not self._connected:
            await self.connect()
    
    async def append_text(self, text: str):
        """
        Buffer text and send in larger chunks to prevent overlapping audio.
        This is the key method - we accumulate tokens and flush at strategic points.
        """
        await self.ensure_connected()
        
        # Add text to buffer
        self._buffer += text
        logger.debug(f"BUFFER: Added '{text}' -> buffer now: '{self._buffer}' (len={len(self._buffer)})")
        
        # Determine if we should flush the buffer
        should_flush = self._should_flush_buffer()
        
        if should_flush:
            logger.info(f"BUFFER: Flushing buffer with {len(self._buffer)} characters")
            await self._flush_buffer()
        else:
            logger.debug(f"BUFFER: Not flushing yet, waiting for more content")
    
    def _should_flush_buffer(self) -> bool:
        """
        Determine if buffer should be flushed based on content and length.
        Strategy: Flush on sentence endings or when buffer gets large enough.
        """
        if not self._buffer:
            return False
        
        # Always flush if buffer gets very large (safety)
        if len(self._buffer) >= self._buffer_threshold * 2:
            return True
        
        # Flush on strong punctuation (sentence endings)
        if len(self._buffer) >= 10:  # Minimum buffer before checking punctuation
            # Check for sentence endings
            if self._buffer.rstrip().endswith(('.', '!', '?', '\u061F', '\u061E')):
                return True
            
            # For Arabic question marks and other punctuation
            # ؟ is Arabic question mark (U+061F)
            # ؞ is Arabic triple dot punctuation (U+061E)
        
        # Flush when buffer reaches threshold AND we hit a natural break
        if len(self._buffer) >= self._buffer_threshold:
            # Look for natural break points near the end
            recent_text = self._buffer[-20:] if len(self._buffer) > 20 else self._buffer
            
            # Flush on commas only if we have enough text
            if ',' in recent_text or '،' in recent_text:  # Arabic comma ،
                return True
            
            # Flush on space after reaching threshold (word boundary)
            if self._buffer.endswith(' '):
                return True
        
        return False
    
    async def _flush_buffer(self):
        """
        Send the current buffer to TTS server with proper formatting.
        """
        if not self._buffer.strip():
            return
        
        # Ensure text ends with space as per ElevenLabs docs
        text_to_send = self._buffer
        if not text_to_send.endswith(' '):
            text_to_send += ' '
        
        try:
            await self._ws.send_json({
                "action": "append_tts",
                "text": text_to_send
            })
            logger.debug(f"Flushed TTS buffer ({len(text_to_send)} chars): {text_to_send[:50]}...")
            
            # Clear buffer and mark first chunk sent
            self._buffer = ""
            self._first_chunk_sent = True
            
        except Exception as e:
            logger.error(f"Failed to flush buffer: {e}")
            self._connected = False
            raise
    
    async def finish_stream(self):
        """Signal that the LLM response is complete - ElevenLabs should finish generating."""
        if not self._connected or not self._ws:
            return
        
        try:
            # Flush any remaining buffer first
            if self._buffer.strip():
                await self._flush_buffer()
            
            # Then send finish signal
            await self._ws.send_json({
                "action": "finish_tts"
            })
            logger.info("Sent finish_tts signal")
            
            # Reset buffer state for next response
            self._buffer = ""
            self._first_chunk_sent = False
            
        except Exception as e:
            logger.error(f"Failed to send finish signal: {e}")
    
    async def disconnect(self):
        """Disconnect from the TTS server."""
        async with self._lock:
            if self._ws:
                await self._ws.close()
            if self._session:
                await self._session.close()
            self._connected = False
            logger.info("Disconnected from TTS server")


class DirectStreamingAgent:
    """
    Agent that handles voice conversation with direct LLM-to-TTS streaming.
    
    Flow:
    1. VAD detects speech → STT transcribes
    2. User transcript sent to OpenAI LLM
    3. LLM response tokens streamed directly to ws_server.py
    4. ws_server.py maintains ONE ElevenLabs connection, streams audio to Flutter
    5. Flutter receives audio + alignment data for highlighting
    """
    
    def __init__(self, room: rtc.Room, buffer_threshold: int = 80):
        self.room = room
        self.openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.tts_client = DirectTTSClient(TTS_WS_URL, buffer_threshold=buffer_threshold)
        self.conversation_history: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        
        # VAD and STT
        self.vad = silero.VAD.load()
        self.stt = lk_openai.STT(model="gpt-4o-transcribe", language='ar')
        
        # Track current state
        self._is_processing = False
        self._processing_lock = asyncio.Lock()
        self._tracked_sids = set()
        
        logger.info("DirectStreamingAgent initialized")
    
    async def start(self):
        """Start the agent - set up event handlers and begin listening."""
        logger.info("Starting DirectStreamingAgent")
        
        # Connect to TTS server
        await self.tts_client.connect()
        
        # Set up track subscription handler
        @self.room.on("track_subscribed")
        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.TrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                if track.sid in self._tracked_sids:
                    logger.warning(f"Track {track.sid} already tracked, skipping")
                    return
                
                logger.info(f"Subscribed to audio track from {participant.identity}")
                self._tracked_sids.add(track.sid)
                asyncio.create_task(self._process_audio_track(track))
        
        # Check for existing tracks
        for participant in self.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                    if publication.track.sid in self._tracked_sids:
                        continue
                        
                    logger.info(f"Found existing audio track from {participant.identity}")
                    self._tracked_sids.add(publication.track.sid)
                    asyncio.create_task(self._process_audio_track(publication.track))
        
        logger.info("DirectStreamingAgent started and listening")
    
    async def _process_audio_track(self, track: rtc.Track):
        """Process incoming audio track with VAD and STT."""
        logger.info("Starting audio track processing")
        
        audio_stream = rtc.AudioStream(track)
        vad_stream = self.vad.stream()
        
        async def feed_vad():
            """Feed audio frames to VAD."""
            async for event in audio_stream:
                if event.frame:
                    vad_stream.push_frame(event.frame)
        
        # Start feeding VAD
        feed_task = asyncio.create_task(feed_vad())
        
        try:
            # Process VAD events
            async for event in vad_stream:
                if event.type == VADEventType.START_OF_SPEECH:
                    logger.info("Speech started")
                    await self._publish_event("user_speech_start", {})
                    
                elif event.type == VADEventType.END_OF_SPEECH:
                    logger.info("Speech ended, transcribing...")
                    await self._publish_event("user_speech_end", {})
                    
                    # Transcribe the speech - event.frames contains the audio
                    if event.frames:
                        await self._transcribe_and_respond(event.frames)
        
        except Exception as e:
            logger.error(f"Error in audio processing: {e}", exc_info=True)
        finally:
            feed_task.cancel()
            await vad_stream.aclose()
    
    async def _transcribe_and_respond(self, frames: list[rtc.AudioFrame]):
        """Transcribe audio frames and generate response."""
        async with self._processing_lock:
            if self._is_processing:
                logger.warning("Already processing, skipping")
                return
            self._is_processing = True
        
        try:
            # Combine frames into one buffer
            combined_frame = self._combine_frames(frames)
            
            # Transcribe with STT
            logger.info("Starting transcription...")
            
            result = await self.stt.recognize(buffer=combined_frame)
            
            transcript = ""
            if result.alternatives:
                transcript = result.alternatives[0].text
            
            if not transcript.strip():
                logger.info("Empty transcription, skipping")
                return
            
            logger.info(f"User said: {transcript}")
            
            # Send transcript to Flutter
            await self._publish_event("user_transcript", {
                "text": transcript,
                "is_final": True
            })
            
            # Add to conversation history
            self.conversation_history.append({
                "role": "user",
                "content": transcript
            })
            
            # Generate and stream response
            await self._stream_llm_response()
        
        except Exception as e:
            logger.error(f"Error in transcribe_and_respond: {e}", exc_info=True)
        finally:
            self._is_processing = False
    
    def _combine_frames(self, frames: list[rtc.AudioFrame]) -> rtc.AudioFrame:
        """Combine multiple audio frames into one."""
        if not frames:
            raise ValueError("No frames to combine")
        
        if len(frames) == 1:
            return frames[0]
        
        # Combine frame data
        sample_rate = frames[0].sample_rate
        num_channels = frames[0].num_channels
        
        all_data = b''.join(f.data.tobytes() if hasattr(f.data, 'tobytes') else bytes(f.data) for f in frames)
        total_samples = sum(f.samples_per_channel for f in frames)
        
        return rtc.AudioFrame(
            data=all_data,
            sample_rate=sample_rate,
            num_channels=num_channels,
            samples_per_channel=total_samples,
        )
    
    async def _stream_llm_response(self):
        """
        Stream LLM response directly to TTS.
        
        THIS IS THE KEY FUNCTION - it streams tokens directly to ONE TTS session.
        """
        logger.info("Starting LLM response streaming")
        
        full_response = ""
        
        try:
            # Notify Flutter that assistant is responding
            await self._publish_event("assistant_response_start", {})
            
            # Start OpenAI streaming
            stream = await self.openai_client.chat.completions.create(
                model="gpt-4.1",
                messages=self.conversation_history,
                stream=True,
                temperature=0.7,
            )
            
            # Process stream - send EACH token to TTS immediately
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    
                    # Send token to TTS server - builds ONE continuous stream
                    await self.tts_client.append_text(token)
                    
                    # Also send to Flutter for display (text highlighting)
                    await self._publish_event("assistant_text_chunk", {"text": token})
                    
                    logger.debug(f"Streamed token: {token}")
            
            logger.info("LLM stream finished, sending finish_tts")
            
            # Signal end of LLM response - TTS server will close ElevenLabs stream
            await self.tts_client.finish_stream()
            
            # Notify Flutter that response is complete
            await self._publish_event("assistant_response_end", {
                "full_text": full_response
            })
            
            # Add to conversation history
            self.conversation_history.append({
                "role": "assistant",
                "content": full_response
            })
            
            logger.info(f"LLM response complete: {full_response[:100]}...")
        
        except Exception as e:
            logger.error(f"Error streaming LLM response: {e}", exc_info=True)
            await self._publish_event("error", {"message": str(e)})
    
    async def _publish_event(self, event_type: str, data: dict):
        """Publish an event to Flutter via LiveKit data channel."""
        try:
            payload = json.dumps({
                "type": event_type,
                **data
            }).encode('utf-8')
            
            await self.room.local_participant.publish_data(
                payload=payload,
                reliable=True
            )
            logger.debug(f"Published event: {event_type}")
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
    
    async def stop(self):
        """Stop the agent and clean up."""
        logger.info("Stopping DirectStreamingAgent")
        await self.tts_client.disconnect()


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the direct streaming agent."""
    logger.info("Starting direct streaming agent")
    
    try:
        await ctx.connect()
        logger.info(f"Connected to LiveKit room: {ctx.room.name}")
        
        # Create and start our custom agent
        agent = DirectStreamingAgent(ctx.room)
        await agent.start()
        
        # Wait for disconnect
        disconnect_event = asyncio.Event()
        
        @ctx.room.on("disconnected")
        def on_disconnected():
            logger.info("Room disconnected")
            disconnect_event.set()
        
        await disconnect_event.wait()
        
        # Cleanup
        await agent.stop()
        logger.info("Agent stopped")
        
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logger.info("Starting direct streaming agent application")
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
