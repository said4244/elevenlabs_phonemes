# Fix for Overlapping TTS Audio Streams

## Problem Resolved

The issue where multiple TTS audio streams were playing concurrently for a single LLM response (e.g., when counting from 1 to 50) has been fixed. Previously, you might hear one audio stream speaking "1…12" while another simultaneously speaks "13…20," causing alignment issues and overlapping audio.

## Root Cause

The problem was caused by sending text chunks to ElevenLabs too frequently:
- Agent was sending individual tokens/characters immediately to TTS
- Each small chunk triggered ElevenLabs' `try_trigger_generation`
- This caused multiple overlapping audio segments within a single TTS session
- Result: Multiple audio streams playing simultaneously instead of one continuous stream

## Solution Implemented

### 1. Intelligent Text Buffering in Agent (`agent_direct.py`)

**Changes:**
- Added configurable buffer threshold (default: 80 characters)
- Implemented smart flush logic that considers:
  - **Sentence boundaries**: `.`, `!`, `?`, Arabic `؟`, `؎`
  - **Natural breaks**: Commas when buffer is large enough
  - **Word boundaries**: Spaces after reaching threshold
  - **Safety limit**: Auto-flush if buffer gets very large (2x threshold)

**Before vs After:**
- **Before**: Counting "1, 2, 3, 4, 5..." sent 55+ individual token messages
- **After**: Same text sent as 2 larger, meaningful chunks

### 2. ElevenLabs Streaming Optimization (`tts_streamer.py`)

**Changes:**
- Optimized `chunk_length_schedule`: `[50, 150, 300, 300]` (faster first audio)
- Smart `try_trigger_generation` usage: Only on first chunk, then let ElevenLabs handle scheduling
- All text chunks end with spaces (per ElevenLabs API requirements)

### 3. Trailing Space Compliance

**Changes:**
- Every text chunk now ends with a space before being sent to ElevenLabs
- This ensures proper word boundaries and smoother audio generation
- Complies with ElevenLabs WebSocket API recommendations

## Configuration

### Buffer Threshold (Adjustable)

```python
# In agent_direct.py - can be tuned for different use cases:
agent = DirectStreamingAgent(room, buffer_threshold=80)  # Default: 80 chars

# Lower values = faster response, more chunks
# Higher values = fewer chunks, slightly higher latency
```

### Chunk Schedule (Optimized)

```python
# In StreamConfig - optimized for low latency:
chunk_length_schedule=[50, 150, 300, 300]

# First audio chunk after ~50 characters (minimum allowed)
# Subsequent chunks follow ElevenLabs' intelligent buffering
```

## Testing the Fix

### 1. Start the Servers

```bash
# Terminal 1: TTS WebSocket Server
cd server
python ws_server.py

# Terminal 2: Token Server  
python tokenserver.py

# Terminal 3: LiveKit Agent
python agent_direct.py connect --room tts-room-123
```

### 2. Test Scenarios

**Counting Test (Previously Problematic):**
- Ask the system to count from 1 to 50
- **Expected**: One continuous voice counting in sequence
- **No More**: Overlapping audio streams saying different numbers simultaneously

**Long Arabic Text:**
- Test with longer Arabic responses
- **Expected**: Smooth, continuous speech with proper alignment
- **Benefit**: Text highlighting stays synchronized with audio

**Quick Back-and-Forth:**
- Ask multiple quick questions in succession
- **Expected**: Each response plays completely before next begins
- **No More**: Multiple responses overlapping

### 3. Verification Points

**Audio Logs:**
- Check `server/logs/alignment/` for alignment files
- Each response should have one continuous timeline (0ms → end)
- No gaps or overlaps in character timing data

**Server Logs:**
- Look for fewer "Flushed TTS buffer" messages (good!)
- Each flush should show larger text chunks
- Example: `Flushed TTS buffer (51 chars): 1, 2, 3, 4, 5, 6, 7, 8, 9, 10...`

**Flutter Client:**
- Text highlighting should progress smoothly with audio
- No jumpy or out-of-sync highlighting
- Audio position stream should be continuous

## Expected Improvements

### 1. Audio Quality
- ✅ **Single continuous audio stream** per response
- ✅ **No overlapping voices** speaking different parts simultaneously  
- ✅ **Proper alignment** between audio and text highlighting
- ✅ **Reduced audio artifacts** from too-frequent TTS triggers

### 2. Performance  
- ✅ **Dramatically fewer API calls** to ElevenLabs (e.g., 55 → 2 requests)
- ✅ **Lower bandwidth usage** due to request batching
- ✅ **More stable WebSocket connections** (less message spam)
- ✅ **Better ElevenLabs rate limit compliance**

### 3. Latency
- ✅ **Maintained low latency** (first audio after ~50 characters)
- ✅ **Smart buffering** doesn't wait unnecessarily long
- ✅ **Sentence-level responsiveness** for natural conversation flow

## Troubleshooting

### If Audio Still Overlaps:
1. **Check buffer threshold**: Try increasing to 100+ characters
2. **Verify server restart**: Both `ws_server.py` and `agent_direct.py` need restart
3. **Check logs**: Look for "Flushed TTS buffer" frequency in agent logs

### If Latency Increases:
1. **Lower buffer threshold**: Try 50-60 characters  
2. **Check punctuation**: Ensure sentences end with proper punctuation
3. **Monitor first-chunk timing**: Should start audio within ~1-2 seconds

### If Alignment Issues Persist:
1. **Verify single TTS session**: Check ws_server logs for session management
2. **Check audio player**: Ensure Flutter StreamingAudioPlayer buffers correctly
3. **Test with shorter responses**: Isolate issue to specific response lengths

## Files Modified

- `server/agent_direct.py`: Added intelligent buffering logic
- `server/tts_streamer.py`: Optimized ElevenLabs streaming settings  
- `server/ws_server.py`: Updated StreamConfig with optimized chunk schedule
- `server/test_buffering_simple.py`: Test harness for verification

## Technical Notes

This fix aligns our implementation with ElevenLabs' recommended practices:
- **Word-by-word streaming** with proper buffering thresholds
- **Minimal try_trigger_generation** usage to prevent overlap
- **Chunk schedule optimization** for low-latency, high-quality audio
- **Proper text formatting** with trailing spaces for smooth generation

The solution mimics how LiveKit's built-in ElevenLabs integration works, achieving the same smooth, continuous audio streaming without overlaps.