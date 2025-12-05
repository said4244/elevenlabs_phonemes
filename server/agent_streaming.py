"""
agent_streaming.py - LiveKit Agent with Streaming TTS via WebSocket

This agent:
1. Uses LiveKit's AgentSession for VAD/STT/LLM
2. Intercepts LLM output and streams it to ws_server.py for TTS with alignment
3. Does NOT use LiveKit's TTS - audio comes from ws_server.py to Flutter directly
"""

import logging
import asyncio
import json
import os
from typing import Optional, List, Dict, Any, AsyncIterable
import aiohttp
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, tts as lk_tts
from livekit.plugins import openai as lk_openai, silero

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('agent_streaming_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
TTS_WS_URL = os.getenv("TTS_WS_URL", "ws://localhost:8081/stream_tts")

# System prompt
SYSTEM_PROMPT = """Talk like a normal Arab human, be conversational, very talkative, 
chatty outgoing, be an attention seeker and basically a motor mouth. 
Use the Syrian dialect exclusively."""


class TTSStreamingClient:
    """Client for streaming text to the TTS WebSocket server."""
    
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._connected = False
    
    async def connect(self):
        """Connect to the TTS WebSocket server."""
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
    
    async def append_text(self, text: str):
        """Append text chunk to the ongoing TTS stream."""
        if not self._connected or not self._ws:
            logger.warning("Not connected to TTS server, attempting reconnect...")
            await self.connect()
        
        try:
            await self._ws.send_json({
                "action": "append_tts",
                "text": text
            })
            logger.debug(f"Appended text to TTS: {text[:50]}...")
        except Exception as e:
            logger.error(f"Failed to append text to TTS server: {e}")
            self._connected = False
            raise
    
    async def finish_stream(self):
        """Signal end of text stream to TTS server."""
        if not self._connected or not self._ws:
            return
        
        try:
            await self._ws.send_json({
                "action": "finish_tts"
            })
            logger.info("Sent finish_tts to TTS server")
        except Exception as e:
            logger.error(f"Failed to send finish_tts: {e}")
    
    async def send_text(self, text: str):
        """Send complete text to TTS server (single shot)."""
        if not self._connected or not self._ws:
            logger.warning("Not connected to TTS server, attempting reconnect...")
            await self.connect()
        
        try:
            await self._ws.send_json({
                "action": "start_tts",
                "text": text
            })
            logger.info(f"Sent text to TTS: {text[:50]}...")
        except Exception as e:
            logger.error(f"Failed to send to TTS server: {e}")
            self._connected = False
            raise
    
    async def disconnect(self):
        """Disconnect from the TTS server."""
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        self._connected = False
        logger.info("Disconnected from TTS server")


class StreamingTTSProxy(lk_tts.TTS):
    """
    A TTS wrapper that streams text chunks to ws_server.py
    which forwards them to ElevenLabs in one continuous session.
    
    This maintains a WebSocket connection to ws_server.py and
    sends text as it arrives from the LLM.
    """
    
    def __init__(self, room: rtc.Room, tts_ws_url: str):
        super().__init__(
            capabilities=lk_tts.TTSCapabilities(streaming=False),
            sample_rate=22050,
            num_channels=1,
        )
        self.room = room
        self.tts_ws_url = tts_ws_url
        self._tts_client: Optional[TTSStreamingClient] = None
        self._is_streaming = False
        logger.info(f"Initialized StreamingTTSProxy with TTS URL: {tts_ws_url}")
    
    async def _ensure_connected(self):
        """Ensure we have a connection to the TTS server."""
        if self._tts_client is None:
            self._tts_client = TTSStreamingClient(self.tts_ws_url)
        if not self._tts_client._connected:
            await self._tts_client.connect()
    
    async def start_new_response(self):
        """Start a new TTS streaming session."""
        await self._ensure_connected()
        self._is_streaming = True
        logger.info("Started new TTS streaming session")
    
    async def append_text(self, text: str):
        """Append text chunk to the ongoing TTS stream."""
        if not self._is_streaming:
            await self.start_new_response()
        
        if self._tts_client and text.strip():
            await self._tts_client.append_text(text)
            # Also send to Flutter for display
            await self.room.local_participant.publish_data(
                payload=json.dumps({
                    "type": "assistant_text_chunk",
                    "text": text
                }).encode('utf-8'),
                reliable=True
            )
            logger.debug(f"Appended text: {text[:50]}...")
    
    async def finish_response(self):
        """Finish the current TTS stream."""
        if self._tts_client and self._is_streaming:
            await self._tts_client.finish_stream()
            self._is_streaming = False
            logger.info("Finished TTS streaming session")
    
    def synthesize(self, text: str, *, conn_options: Optional[Any] = None) -> "ChunkedSentenceStream":
        """Create a synthesis stream for the given text."""
        logger.info(f"StreamingTTSProxy.synthesize called with: {text[:50]}...")
        return ChunkedSentenceStream(self, text)
    
    def stream(self, *, conn_options: Optional[Any] = None) -> "ChunkedSentenceStream":
        """Create an empty synthesis stream for streaming input."""
        logger.info("StreamingTTSProxy.stream called")
        return ChunkedSentenceStream(self, "")


class ChunkedSentenceStream:
    """Async iterator that streams text to TTS server as it arrives."""
    
    def __init__(self, proxy: StreamingTTSProxy, text: str):
        self.proxy = proxy
        self._text = text
        self._sent = False
        self._done = False
        logger.debug(f"ChunkedSentenceStream created for: {text[:50] if text else '(empty)'}...")
    
    async def __aenter__(self):
        # Stream this text chunk to TTS server
        if self._text and not self._sent:
            try:
                await self.proxy.append_text(self._text)
                self._sent = True
            except Exception as e:
                logger.error(f"Error streaming text to TTS: {e}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._done = True
        return False
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        
        # Yield one minimal audio frame and then stop
        # The real audio goes directly from ws_server.py to Flutter
        self._done = True
        
        return lk_tts.SynthesizedAudio(
            request_id="stream_request",
            frame=rtc.AudioFrame(
                data=b'\x00\x00' * 100,  # Small silence buffer
                sample_rate=22050,
                num_channels=1,
                samples_per_channel=100,
            ),
        )


class StreamingAssistant(Agent):
    """Voice assistant that uses external TTS streaming."""
    
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        logger.info("Initialized StreamingAssistant")


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the streaming agent."""
    logger.info("Starting streaming agent entrypoint")
    
    try:
        await ctx.connect()
        logger.info(f"Connected to LiveKit room: {ctx.room.name}")
        
        # Initialize components
        vad = silero.VAD.load()
        stt = lk_openai.STT(model="gpt-4o-transcribe", language='ar')
        llm = lk_openai.LLM(model="gpt-4.1", temperature=0.7)
        
        # Use our custom TTS proxy that streams text to ws_server.py
        # ws_server.py forwards to ElevenLabs and sends audio to Flutter
        tts = StreamingTTSProxy(ctx.room, TTS_WS_URL)
        
        # Create agent session with our components
        session = AgentSession(
            stt=stt,
            llm=llm, 
            tts=tts,
            vad=vad
        )
        logger.info("Created agent session with streaming TTS")
        
        # Start the session - this blocks until session ends
        await session.start(
            room=ctx.room,
            agent=StreamingAssistant(),
        )
        logger.info("Agent session started successfully")
        
        # Keep the agent alive - wait for disconnect event
        disconnect_event = asyncio.Event()
        
        @ctx.room.on("disconnected")
        def on_disconnected():
            logger.info("Room disconnected event received")
            disconnect_event.set()
        
        await disconnect_event.wait()
        logger.info("Room closed, agent exiting")
        
    except Exception as e:
        logger.error(f"Critical error in entrypoint: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logger.info("Starting streaming agent application")
    try:
        agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
    except Exception as e:
        logger.error(f"Application failed to start: {e}", exc_info=True)
        raise
