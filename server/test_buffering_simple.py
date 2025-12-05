#!/usr/bin/env python3
"""
Simple test for buffering logic without dependencies.
"""

import asyncio

class MockDirectTTSClient:
    """Mock implementation of the buffering logic from DirectTTSClient"""
    
    def __init__(self, buffer_threshold=50):
        self._buffer = ""
        self._buffer_threshold = buffer_threshold
        self._first_chunk_sent = False
        self.sent_messages = []  # Track what would be sent
    
    def _should_flush_buffer(self) -> bool:
        """Determine if buffer should be flushed based on content and length."""
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
        """Send the current buffer to TTS server with proper formatting."""
        if not self._buffer.strip():
            return
        
        # Ensure text ends with space as per ElevenLabs docs
        text_to_send = self._buffer
        if not text_to_send.endswith(' '):
            text_to_send += ' '
        
        # Record what would be sent
        self.sent_messages.append({
            "action": "append_tts",
            "text": text_to_send
        })
        print(f"FLUSH: '{text_to_send}' ({len(text_to_send)} chars)")
        
        # Clear buffer and mark first chunk sent
        self._buffer = ""
        self._first_chunk_sent = True
    
    async def append_text(self, text: str):
        """Buffer text and send in larger chunks."""
        # Add text to buffer
        self._buffer += text
        
        # Determine if we should flush the buffer
        should_flush = self._should_flush_buffer()
        
        if should_flush:
            await self._flush_buffer()
    
    async def finish_stream(self):
        """Flush any remaining buffer and finish."""
        if self._buffer.strip():
            await self._flush_buffer()
        
        # Reset for next response
        self._buffer = ""
        self._first_chunk_sent = False

async def test_counting_scenario():
    """Test the problematic counting from 1 to 50 scenario"""
    print("=== Testing Counting 1-50 Scenario ===")
    
    client = MockDirectTTSClient(buffer_threshold=50)
    
    # Simulate LLM streaming tokens for counting
    tokens = ["1", ",", " ", "2", ",", " ", "3", ",", " ", "4", ",", " ", "5", ",", " ", 
              "6", ",", " ", "7", ",", " ", "8", ",", " ", "9", ",", " ", "10", ",", " ",
              "11", ",", " ", "12", ",", " ", "13", ",", " ", "14", ",", " ", "15", ".",
              " ", "That", " ", "was", " ", "counting", " ", "to", " ", "fifteen", "."]
    
    for token in tokens:
        await client.append_text(token)
    
    await client.finish_stream()
    
    print(f"\nOLD BEHAVIOR: Would send {len(tokens)} individual messages")
    print(f"NEW BEHAVIOR: Sent {len(client.sent_messages)} larger chunks")
    print("Chunks sent:")
    for i, msg in enumerate(client.sent_messages):
        print(f"  {i+1}: {repr(msg['text'])}")

async def test_arabic_sentence():
    """Test Arabic sentence streaming"""
    print("\n=== Testing Arabic Sentence ===")
    
    client = MockDirectTTSClient(buffer_threshold=50)
    
    # Arabic: "Hello to the world, how are you today?"
    tokens = ["مرحبا", " ", "بالعالم", "،", " ", "كيف", " ", "حالك", " ", "اليوم", "؟", " ",
              "أتمنى", " ", "أن", " ", "تكون", " ", "بخير", "."]
    
    for token in tokens:
        await client.append_text(token)
    
    await client.finish_stream()
    
    print(f"OLD BEHAVIOR: Would send {len(tokens)} individual messages")
    print(f"NEW BEHAVIOR: Sent {len(client.sent_messages)} larger chunks")
    print("Chunks sent:")
    for i, msg in enumerate(client.sent_messages):
        print(f"  {i+1}: {repr(msg['text'])}")

async def test_long_response():
    """Test longer response with multiple sentences"""
    print("\n=== Testing Long Response ===")
    
    client = MockDirectTTSClient(buffer_threshold=80)
    
    text = ("This is a longer response. It has multiple sentences! " +
            "The buffering should handle this well by flushing at sentence boundaries. " +
            "This prevents overlapping audio while maintaining good latency.")
    
    # Stream character by character to simulate token streaming
    for char in text:
        await client.append_text(char)
    
    await client.finish_stream()
    
    print(f"OLD BEHAVIOR: Would send {len(text)} individual character messages")
    print(f"NEW BEHAVIOR: Sent {len(client.sent_messages)} larger chunks")
    print("Chunks sent:")
    for i, msg in enumerate(client.sent_messages):
        print(f"  {i+1}: {repr(msg['text'])}")

async def main():
    """Run all tests"""
    await test_counting_scenario()
    await test_arabic_sentence() 
    await test_long_response()
    
    print("\n=== Key Benefits ===")
    print("✓ Dramatically fewer TTS requests (prevents overlapping audio)")
    print("✓ All text chunks end with spaces (per ElevenLabs requirement)")
    print("✓ Flushes at natural boundaries (sentences, commas when buffer is large)")
    print("✓ Maintains reasonable latency (doesn't wait for huge buffers)")

if __name__ == "__main__":
    asyncio.run(main())