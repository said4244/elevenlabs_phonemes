# TTS Streaming Server

Low-latency TTS streaming server using ElevenLabs v2 WebSocket API with character-level alignment for synchronized text highlighting.

## Features

- **Real-time Streaming**: Sub-500ms time-to-first-audio-byte
- **Character-level Alignment**: Precise timing data for karaoke-style highlighting
- **Arabic Support**: Full RTL text and pronunciation support
- **Alignment Logging**: Saves timing data to JSON files for debugging and future viseme processing

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Flutter App    │────▶│  ws_server.py    │────▶│  ElevenLabs     │
│  (WebSocket)    │◀────│  (TTS Relay)     │◀────│  WebSocket API  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  file_logger.py  │
                        │  (Alignment Data)│
                        └──────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `ws_server.py` | Client-facing WebSocket server (port 8081) |
| `tts_streamer.py` | ElevenLabs WebSocket handler |
| `file_logger.py` | Alignment and output file logger |
| `tokenserver.py` | LiveKit token server (port 8080) |
| `agent.py` | LiveKit voice agent (STT + LLM) |

## Quick Start

### 1. Setup Environment

```bash
cd server

# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\Activate.ps1

# Activate (Linux/Mac)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example config
cp .env.example .env

# Edit with your API keys
notepad .env  # or vim .env
```

Required environment variables:
- `ELEVEN_API_KEY` - ElevenLabs API key
- `ELEVEN_VOICE_ID` - Voice ID to use
- `LIVEKIT_API_KEY` - LiveKit API key
- `LIVEKIT_API_SECRET` - LiveKit API secret

### 3. Start Servers

```bash
# Terminal 1: Token Server (for LiveKit)
python tokenserver.py

# Terminal 2: TTS Streaming Server
python ws_server.py
```

### 4. Run Flutter App

```bash
cd ../example
flutter run -d chrome --web-browser-flag "--disable-web-security"
```

## API Reference

### WebSocket Endpoint: `/stream_tts`

Connect to `ws://localhost:8081/stream_tts`

#### Client Messages

**Start TTS:**
```json
{
  "action": "start_tts",
  "text": "مرحبا بالعالم"
}
```

**Stop TTS:**
```json
{
  "action": "stop"
}
```

**Ping:**
```json
{
  "action": "ping"
}
```

#### Server Messages

**Audio Chunk:**
```json
{
  "type": "chunk",
  "audio": "<base64 PCM audio>",
  "char_times": [0, 100, 200],
  "char_durations": [90, 95, 88],
  "chars": ["م", "ر", "ح"],
  "chunk_index": 0
}
```

**Complete:**
```json
{
  "type": "complete",
  "total_chars": 50,
  "total_duration_ms": 5000
}
```

**Error:**
```json
{
  "type": "error",
  "message": "Error description"
}
```

## Audio Format

- Format: PCM (raw audio)
- Sample Rate: 22050 Hz
- Channels: Mono (1)
- Bit Depth: 16-bit

## Alignment Data

Alignment files are saved to `logs/alignment/` with the following structure:

```json
{
  "timestamp": "20241204_120000_123456",
  "text": "مرحبا بالعالم",
  "text_length": 13,
  "total_duration_ms": 2500,
  "char_count": 13,
  "characters": [
    {
      "index": 0,
      "char": "م",
      "start_ms": 0,
      "duration_ms": 150
    },
    ...
  ]
}
```

## Voice Selection

For best Arabic support, use one of these ElevenLabs configurations:

1. **Model**: `eleven_flash_v2_5` (lowest latency)
2. **Voice**: Any voice that supports Arabic, or clone a native Arabic speaker

## Troubleshooting

### CORS Errors

If running Flutter web locally, use:
```bash
flutter run -d chrome --web-browser-flag "--disable-web-security"
```

### WebSocket Connection Failed

1. Check server is running on port 8081
2. Verify `ELEVEN_API_KEY` is set
3. Check `ELEVEN_VOICE_ID` is valid

### No Audio Playback

1. Check browser console for Web Audio API errors
2. Verify audio format matches expected PCM format
3. Try clicking the page first (browser autoplay policies)

## Performance Tuning

| Setting | Effect | Trade-off |
|---------|--------|-----------|
| `ELEVEN_OPTIMIZE_LATENCY=0` | Best quality | Higher latency |
| `ELEVEN_OPTIMIZE_LATENCY=4` | Lowest latency | Less normalization |
| Recommended: `1` | Good balance | ~300ms latency |
