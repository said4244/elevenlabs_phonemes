from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from datetime import timedelta
import os
from dotenv import load_dotenv
import logging
import subprocess
import psutil


load_dotenv()


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="Arabic TTS Token Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your domains
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://cloud.livekit.io")


if not all([LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
    logger.error("Missing LiveKit credentials in .env file")
else:
    logger.info("LiveKit credentials loaded successfully")
    logger.info(f"LiveKit URL: {LIVEKIT_URL}")

current_agent_process = None
current_room_name = None

def get_counter():
    try:
        with open('counter.txt', 'r') as f:
            return int(f.read().strip())
    except FileNotFoundError:
        # Start from 50 if file doesn't exist
        with open('counter.txt', 'w') as f:
            f.write('50')
        return 50

def increment_counter():
    counter = get_counter()
    counter += 1
    with open('counter.txt', 'w') as f:
        f.write(str(counter))
    return counter

async def start_new_agent(room_name: str):
    """Start a new streaming agent for a specific room in a new terminal window"""
    global current_agent_process, current_room_name
    
    # Kill existing agent if any (from memory)
    if current_agent_process:
        try:
            parent = psutil.Process(current_agent_process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
            logger.info(f"Killed previous agent in room {current_room_name}")
        except:
            pass
            
    # Also check for PID file to kill agents from previous server runs
    try:
        if os.path.exists('agent.pid'):
            with open('agent.pid', 'r') as f:
                old_pid = int(f.read().strip())
            try:
                parent = psutil.Process(old_pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
                logger.info(f"Killed orphaned agent from PID file: {old_pid}")
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                logger.error(f"Error killing orphaned agent: {e}")
    except Exception as e:
        logger.error(f"Error handling PID file: {e}")

    # Start new streaming agent with room name in new terminal
    # Uses agent_streaming.py which streams LLM responses to ws_server.py for TTS
    cmd = (
        f'start powershell.exe -NoExit -Command "'
        f'./venv/Scripts/activate; '
        f'python -u agent_direct.py connect --room {room_name}"'
    )
    
    current_agent_process = subprocess.Popen(
        cmd,
        shell=True
    )
    current_room_name = room_name
    
    # Save PID to file
    try:
        with open('agent.pid', 'w') as f:
            f.write(str(current_agent_process.pid))
    except Exception as e:
        logger.error(f"Failed to write PID file: {e}")
    
    logger.info(f"Started new TTS agent for room {room_name} in new terminal")

@app.get("/token")
async def create_token(
    identity: str = None,
    room: str = None,
):
    """Generate a token for connecting to LiveKit room for TTS"""
    # Get and increment counter from file
    counter = increment_counter()
    
    # Generate default values if none provided
    if identity is None:
        identity = f"realtime-{counter}"
    if room is None:
        room = f"realtime-{counter}"
    
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=500, 
            detail="LiveKit credentials not configured. Check .env file."
        )
    
    try:
        # Create access token
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        
        # Set token properties
        token.ttl = timedelta(hours=2)
        token.name = identity
        token.identity = identity
        
        # Grant permissions
        token.with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        
        # Generate JWT
        jwt_token = token.to_jwt()
        
        logger.info(f"Generated token for {identity} in room {room}")
        
        # Start new agent for this room
        await start_new_agent(room)  # Add this line!
        
        return {
            "accessToken": jwt_token,
            "url": LIVEKIT_URL,
            "room": room,
            "identity": identity
        }
        
    except Exception as e:
        logger.error(f"Failed to generate token: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Arabic TTS Token Server"}


@app.get("/debug")
async def debug_info():
    """Debug endpoint to check server status"""
    return {
        "status": "running",
        "default_room": "tts-reading-room",
        "livekit_url": LIVEKIT_URL,
        "credentials_loaded": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    logger.info(f"Starting token server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)