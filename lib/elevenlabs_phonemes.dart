library elevenlabs_phonemes;

// Core voice assistant
export 'src/voice_assistant.dart' show VoiceAssistant, VoiceAssistantConfig, VoiceConnectionState;

// TTS streaming
export 'src/tts_stream_handler.dart' show TTSStreamHandler, TTSChunk, TTSAlignment, WordBoundary, TimeRange;

// Audio playback
export 'src/audio_player_stream.dart' show StreamingAudioPlayer, AudioConfig;

// Text highlighting widgets
export 'src/text_highlighter.dart' show SyncedTextHighlighter, TTSTextDisplay, ArabicText, HighlightMode;