#!/usr/bin/env python3
"""
Test script for the new buffering logic.
This tests the DirectTTSClient buffering without actually connecting to servers.
"""

import asyncio
from agent_direct import DirectTTSClient

class MockWebSocket:
    """Mock WebSocket for testing"""
    def __init__(self):
        self.sent_messages = []
    
    async def send_json(self, message):
        self.sent_messages.append(message)
        print(f"SENT: {message}")

class TestTTSClient(DirectTTSClient):
    """Test version that doesn't actually connect"""
    
    def __init__(self, buffer_threshold=50):
        # Skip parent __init__ to avoid real connection
        self.ws_url = "test"
        self._session = None
        self._ws = MockWebSocket()
        self._connected = True  # Pretend we're connected
        self._lock = asyncio.Lock()
        
        # Buffering settings
        self._buffer = ""
        self._buffer_threshold = buffer_threshold
        self._first_chunk_sent = False

async def test_counting_scenario():
    """Test the problematic counting from 1 to 50 scenario"""
    print("=== Testing Counting 1-50 Scenario ===")
    
    client = TestTTSClient(buffer_threshold=50)
    
    # Simulate LLM streaming tokens for counting
    tokens = ["1", ",", " ", "2", ",", " ", "3", ",", " ", "4", ",", " ", "5", ",", " ", 
              "6", ",", " ", "7", ",", " ", "8", ",", " ", "9", ",", " ", "10", ",", " ",
              "11", ",", " ", "12", ",", " ", "13", ",", " ", "14", ",", " ", "15"]
    
    for token in tokens:
        await client.append_text(token)
    
    # Finish the stream
    await client.finish_stream()
    
    print(f"\nTotal messages sent: {len(client._ws.sent_messages)}")
    for i, msg in enumerate(client._ws.sent_messages):
        print(f"Message {i+1}: {repr(msg['text'][:100])}")

async def test_sentence_scenario():
    """Test normal sentence streaming"""
    print("\n=== Testing Sentence Scenario ===")
    
    client = TestTTSClient(buffer_threshold=50)
    
    # Simulate streaming a normal sentence
    tokens = ["مرحبا", " ", "بالعالم", "،", " ", "كيف", " ", "حالك", " ", "اليوم", "؟", " ",
              "أتمنى", " ", "أن", " ", "تكون", " ", "بخير", "."]
    
    for token in tokens:
        await client.append_text(token)
    
    await client.finish_stream()
    
    print(f"\nTotal messages sent: {len(client._ws.sent_messages)}")
    for i, msg in enumerate(client._ws.sent_messages):
        print(f"Message {i+1}: {repr(msg['text'][:100])}")

async def test_long_response():
    """Test a longer response that should be chunked appropriately"""
    print("\n=== Testing Long Response ===")
    
    client = TestTTSClient(buffer_threshold=80)
    
    # Simulate a longer response
    text = ("This is a much longer response that should demonstrate how the buffering "
            "works with larger chunks. It should flush at sentence boundaries and when "
            "the buffer gets too large. This helps prevent overlapping audio streams "
            "while maintaining reasonable latency for the user experience.")
    
    # Stream character by character to simulate LLM token streaming
    for char in text:
        await client.append_text(char)
    
    await client.finish_stream()
    
    print(f"\nTotal messages sent: {len(client._ws.sent_messages)}")
    for i, msg in enumerate(client._ws.sent_messages):
        print(f"Message {i+1}: {repr(msg['text'][:100])}")

async def main():
    """Run all tests"""
    await test_counting_scenario()
    await test_sentence_scenario() 
    await test_long_response()
    
    print("\n=== Summary ===")
    print("The new buffering logic should:")
    print("1. Send fewer, larger chunks (reduces overlapping audio)")
    print("2. Always include trailing spaces")
    print("3. Flush at sentence boundaries")
    print("4. Flush when buffer gets large enough")

if __name__ == "__main__":
    asyncio.run(main())