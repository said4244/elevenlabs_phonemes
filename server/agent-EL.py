import logging
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, Agent
from livekit.plugins import openai, elevenlabs, silero
from livekit.plugins.openai import realtime
from livekit.plugins.openai.realtime import *
from openai.types.beta.realtime.session import  InputAudioTranscription, TurnDetection
import os

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('agent_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")
voice_id = os.getenv("ELEVEN_VOICE_ID")
model = os.getenv("ELEVEN_MODEL")
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

class Assistant(Agent):
    def __init__(self) -> None:
        logger.info("Initializing Assistant agent")
        super().__init__(instructions="Talk like a normal Arab human, be conversational,  very talkative,  chatty outgoing, be an attention seeker and basically a motor mouth. Use the syrian dialect exclusively. ")
    
    async def on_error(self, error: Exception):
        logger.error(f"Agent error: {str(error)}", exc_info=True)

async def entrypoint(ctx: agents.JobContext):
    logger.info("Starting agent entrypoint")
    try:
        await ctx.connect()
        logger.info("Successfully connected to LiveKit room")
        # Initialize the realtime model with detailed configuration

        vad = silero.VAD.load()
        stt = openai.STT(model="gpt-4o-transcribe",language='ar')
        llm = openai.LLM(model="gpt-5.1", temperature=0.7)
        tts = elevenlabs.TTS(voice_id=voice_id, model=model, api_key=ELEVEN_API_KEY, language="ar")
        session = AgentSession(stt=stt, llm=llm, tts=tts, vad=vad)
        logger.info("Created agent session")

        try:
            await session.start(
                room=ctx.room,
                agent=Assistant(),
            )
            logger.info("Successfully started agent session")
        except Exception as e:
            logger.error(f"Failed to start session: {str(e)}", exc_info=True)
            raise

    except Exception as e:
        logger.error(f"Critical error in entrypoint: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    logger.info("Starting agent application")
    try:
        agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
    except Exception as e:
        logger.error(f"Application failed to start: {str(e)}", exc_info=True)
        raise