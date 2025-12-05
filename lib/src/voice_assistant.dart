/// voice_assistant.dart
///
/// Main voice assistant class with integrated TTS streaming.

import 'dart:async';
import 'package:flutter/foundation.dart';
import 'connection_manager.dart';
import 'tts_stream_handler.dart';
import 'audio_player_stream.dart';
import 'web_audio_manager.dart';
import 'logger.dart';

enum VoiceConnectionState {
  disconnected,
  connecting,
  connected,
  error,
}

class VoiceAssistantConfig {
  final String tokenUrl;
  final String? livekitUrl;
  final String? ttsServerUrl;  // TTS streaming server (optional)
  final bool enableLogging;
  final bool enableTTSStreaming;  // Whether to use TTS streaming

  const VoiceAssistantConfig({
    required this.tokenUrl,
    this.livekitUrl,
    this.ttsServerUrl,
    this.enableLogging = true,
    this.enableTTSStreaming = true,
  });
}

class VoiceAssistant {
  final VoiceAssistantConfig config;
  late final ConnectionManager _connectionManager;
  TTSStreamHandler? _ttsHandler;
  StreamingAudioPlayer? _audioPlayer;

  VoiceConnectionState _state = VoiceConnectionState.disconnected;
  String _statusMessage = 'Disconnected';
  
  // Stream controllers
  final _stateController = StreamController<VoiceConnectionState>.broadcast();
  final _statusController = StreamController<String>.broadcast();
  final _eventController = StreamController<Map<String, dynamic>>.broadcast();
  final _textController = StreamController<String>.broadcast();  // AI response text
  final _alignmentController = StreamController<TTSAlignment>.broadcast();
  
  // Subscriptions
  StreamSubscription? _ttsChunkSubscription;
  StreamSubscription? _ttsAlignmentSubscription;

  VoiceAssistant({required this.config}) {
    if (config.enableLogging) {
      VoiceAssistantLogger.info('Initializing VoiceAssistant');
      VoiceAssistantLogger.info('Token URL: ${config.tokenUrl}');
      VoiceAssistantLogger.info('LiveKit URL: ${config.livekitUrl ?? "default"}');
      VoiceAssistantLogger.info('TTS Server URL: ${config.ttsServerUrl ?? "disabled"}');
      VoiceAssistantLogger.info('Platform: ${kIsWeb ? "Web" : "Native"}');
    }

    // Initialize LiveKit connection (for STT/mic input)
    _connectionManager = ConnectionManager(
      tokenUrl: config.tokenUrl,
      livekitUrl: config.livekitUrl,
      onStatusChanged: _handleStatusChange,
      onEvent: _handleEvent,
    );

    // Initialize TTS streaming if configured
    if (config.enableTTSStreaming && config.ttsServerUrl != null) {
      _ttsHandler = TTSStreamHandler(serverUrl: config.ttsServerUrl!);
      _audioPlayer = StreamingAudioPlayer();
      _setupTTSListeners();
    }

    WebAudioManager.logWebAudioState();
  }

  void _setupTTSListeners() {
    if (_ttsHandler == null || _audioPlayer == null) return;

    // Handle TTS chunks (audio data)
    _ttsChunkSubscription = _ttsHandler!.chunkStream.listen(_handleTTSChunk);

    // Handle alignment data
    _ttsAlignmentSubscription = _ttsHandler!.alignmentStream.listen((alignment) {
      VoiceAssistantLogger.debug('Received alignment: $alignment');
      _alignmentController.add(alignment);
    });
  }

  void _handleTTSChunk(TTSChunk chunk) {
    if (chunk.isComplete) {
      VoiceAssistantLogger.info('TTS playback complete');
      _audioPlayer?.markComplete();
      return;
    }

    if (chunk.error != null) {
      VoiceAssistantLogger.error('TTS error: ${chunk.error}');
      return;
    }

    if (chunk.isControlMessage) {
      return; // Skip control messages
    }

    // Feed audio to player
    if (chunk.hasAudio) {
      VoiceAssistantLogger.info('Feeding ${chunk.audioBytes.length} bytes to audio player');
      _audioPlayer?.addAudioData(chunk.audioBytes);
    } else {
      VoiceAssistantLogger.debug('Received chunk without audio data');
    }
  }

  /// Start the voice assistant
  Future<void> start() async {
    if (_state == VoiceConnectionState.connected ||
        _state == VoiceConnectionState.connecting) {
      VoiceAssistantLogger.warning('Already connected or connecting');
      return;
    }

    VoiceAssistantLogger.info('Starting voice assistant');
    _updateState(VoiceConnectionState.connecting);

    try {
      // Request permissions if on web
      if (kIsWeb) {
        await WebAudioManager.requestMicrophonePermission();
      }

      // Initialize audio player (must be on user interaction for Web Audio API)
      if (_audioPlayer != null) {
        await _audioPlayer!.initialize();
        VoiceAssistantLogger.info('Audio player initialized');
      }

      // Connect to LiveKit for mic input
      await _connectionManager.connect();

      // Connect to TTS server if configured
      if (_ttsHandler != null) {
        try {
          await _ttsHandler!.connect();
          VoiceAssistantLogger.info('Connected to TTS server');
        } catch (e) {
          VoiceAssistantLogger.warning('TTS server connection failed: $e');
          // Continue without TTS - not critical
        }
      }

      _updateState(VoiceConnectionState.connected);
      VoiceAssistantLogger.info('Voice assistant started successfully');
    } catch (e) {
      VoiceAssistantLogger.error('Failed to start voice assistant', e);
      _updateState(VoiceConnectionState.error);
      _statusMessage = 'Error: ${e.toString()}';
      rethrow;
    }
  }

  /// Stop the voice assistant
  Future<void> stop() async {
    if (_state == VoiceConnectionState.disconnected) {
      VoiceAssistantLogger.warning('Already disconnected');
      return;
    }

    VoiceAssistantLogger.info('Stopping voice assistant');

    try {
      // Stop audio playback
      _audioPlayer?.stop();

      // Disconnect from TTS server
      await _ttsHandler?.disconnect();

      // Disconnect from LiveKit
      await _connectionManager.disconnect();

      _updateState(VoiceConnectionState.disconnected);
      VoiceAssistantLogger.info('Voice assistant stopped');
    } catch (e) {
      VoiceAssistantLogger.error('Error stopping voice assistant', e);
    }
  }

  /// Speak the given text using streaming TTS
  /// Returns immediately - audio plays asynchronously
  Future<void> speak(String text) async {
    if (_state != VoiceConnectionState.connected) {
      throw StateError('Not connected');
    }

    if (_ttsHandler == null || !_ttsHandler!.isConnected) {
      throw StateError('TTS server not connected');
    }

    VoiceAssistantLogger.info('Speaking: ${text.substring(0, text.length > 50 ? 50 : text.length)}...');

    // Emit the text for UI display
    _textController.add(text);

    // Reset audio player for new utterance
    _audioPlayer?.reset();

    // Start TTS streaming
    await _ttsHandler!.startTTS(text);
  }

  /// Stop current speech
  void stopSpeaking() {
    _ttsHandler?.stopTTS();
    _audioPlayer?.stop();
  }

  void dispose() {
    VoiceAssistantLogger.info('Disposing voice assistant');

    // Cancel subscriptions
    _ttsChunkSubscription?.cancel();
    _ttsAlignmentSubscription?.cancel();

    stop();

    // Close stream controllers
    _stateController.close();
    _statusController.close();
    _eventController.close();
    _textController.close();
    _alignmentController.close();

    // Dispose managers
    _ttsHandler?.dispose();
    _audioPlayer?.dispose();
    _connectionManager.dispose();
  }

  // State and status getters
  VoiceConnectionState get state => _state;
  String get status => _statusMessage;
  bool get isConnected => _state == VoiceConnectionState.connected;
  bool get hasAgent => _connectionManager.hasAgent;
  bool get isTTSConnected => _ttsHandler?.isConnected ?? false;

  // Streams for UI updates
  Stream<VoiceConnectionState> get stateStream => _stateController.stream;
  Stream<String> get statusStream => _statusController.stream;
  Stream<Map<String, dynamic>> get eventStream => _eventController.stream;
  
  /// Stream of text being spoken (for display)
  Stream<String> get textStream => _textController.stream;
  
  /// Stream of alignment data for text highlighting
  Stream<TTSAlignment> get alignmentStream => _alignmentController.stream;
  
  /// Stream of audio playback position in milliseconds
  Stream<int> get audioPositionStream =>
      _audioPlayer?.positionStream ?? const Stream.empty();
  
  /// Stream of audio playback state (true = playing)
  Stream<bool> get audioStateStream =>
      _audioPlayer?.stateStream ?? const Stream.empty();

  // Private methods
  void _updateState(VoiceConnectionState newState) {
    _state = newState;
    _stateController.add(newState);
    VoiceAssistantLogger.debug('State updated: $newState');
  }

  void _handleStatusChange(String status) {
    _statusMessage = status;
    _statusController.add(status);

    // Update connection state based on status
    if (status.toLowerCase().contains('connected') &&
        !status.toLowerCase().contains('dis')) {
      if (status.toLowerCase().contains('ready')) {
        _updateState(VoiceConnectionState.connected);
      }
    } else if (status.toLowerCase().contains('connecting')) {
      _updateState(VoiceConnectionState.connecting);
    } else if (status.toLowerCase().contains('disconnected')) {
      _updateState(VoiceConnectionState.disconnected);
    } else if (status.toLowerCase().contains('error') ||
        status.toLowerCase().contains('failed')) {
      _updateState(VoiceConnectionState.error);
    }
  }

  // Accumulated text for UI display
  final StringBuffer _displayText = StringBuffer();
  bool _isReceivingResponse = false;

  void _handleEvent(String event, Map<String, dynamic> data) {
    _eventController.add({
      'type': event,
      'data': data,
      'timestamp': DateTime.now().toIso8601String(),
    });

    // Handle data received from agent
    if (event == 'data_received') {
      final type = data['type'] as String?;
      
      // Handle text chunks from agent - DISPLAY ONLY (agent handles TTS)
      if (type == 'assistant_text_chunk') {
        final text = data['text'] as String? ?? '';
        if (text.isNotEmpty) {
          // First chunk of a new response
          if (!_isReceivingResponse) {
            _isReceivingResponse = true;
            _displayText.clear();
            _audioPlayer?.reset();
            VoiceAssistantLogger.info('New response started - agent handles TTS');
          }
          
          // Accumulate for display
          _displayText.write(text);
          _textController.add(_displayText.toString());
          
          // NOTE: Do NOT forward to TTS here!
          // The agent sends text directly to ws_server.py for TTS.
          // Flutter only displays text and plays audio received from ws_server.
          
          VoiceAssistantLogger.debug('Display text chunk: ${text.substring(0, text.length > 30 ? 30 : text.length)}...');
        }
      }
      
      // Handle response end marker from agent
      else if (type == 'assistant_response_end') {
        if (_isReceivingResponse) {
          _isReceivingResponse = false;
          VoiceAssistantLogger.info('Response ended');
        }
      }
      
      // Legacy single message support
      else if (type == 'assistant_text') {
        final text = data['text'] as String?;
        if (text != null && text.isNotEmpty) {
          _displayText.clear();
          _displayText.write(text);
          _textController.add(text);
          _startSingleShotTTS(text);
          VoiceAssistantLogger.info('Received assistant text (legacy)');
        }
      }
    }
  }

  /// Start single-shot TTS (for legacy support)
  Future<void> _startSingleShotTTS(String text) async {
    if (_ttsHandler == null) return;
    
    try {
      if (!_ttsHandler!.isConnected) {
        await _ttsHandler!.connect();
      }
      _audioPlayer?.reset();
      await _ttsHandler!.startTTS(text);
    } catch (e) {
      VoiceAssistantLogger.error('Failed to start TTS', e);
    }
  }
}
