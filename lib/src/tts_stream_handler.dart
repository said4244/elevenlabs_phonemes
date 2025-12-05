/// tts_stream_handler.dart
///
/// WebSocket client for receiving streaming TTS audio and alignment data.

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'logger.dart';

/// Represents a word boundary with start and end indices
class WordBoundary {
  final int start;
  final int end;
  WordBoundary(this.start, this.end);
}

/// Represents a time range
class TimeRange {
  final int start;
  final int end;
  TimeRange(this.start, this.end);
}

/// Represents a single audio chunk with timing data
class TTSChunk {
  final Uint8List audioBytes;
  final List<int> charStartTimes;
  final List<int> charDurations;
  final List<String> chars;
  final int chunkIndex;
  final bool isComplete;
  final String? error;

  TTSChunk({
    required this.audioBytes,
    required this.charStartTimes,
    required this.charDurations,
    required this.chars,
    required this.chunkIndex,
    this.isComplete = false,
    this.error,
  });

  factory TTSChunk.fromJson(Map<String, dynamic> json) {
    final type = json['type'] as String?;

    if (type == 'error') {
      return TTSChunk(
        audioBytes: Uint8List(0),
        charStartTimes: [],
        charDurations: [],
        chars: [],
        chunkIndex: -1,
        error: json['message'] as String?,
      );
    }

    if (type == 'complete') {
      return TTSChunk(
        audioBytes: Uint8List(0),
        charStartTimes: [],
        charDurations: [],
        chars: [],
        chunkIndex: -1,
        isComplete: true,
      );
    }

    if (type == 'pong' || type == 'stopped') {
      // Control messages, return empty chunk
      return TTSChunk(
        audioBytes: Uint8List(0),
        charStartTimes: [],
        charDurations: [],
        chars: [],
        chunkIndex: -1,
      );
    }

    // Parse chunk data
    final audioB64 = json['audio'] as String? ?? '';
    final audioBytes =
        audioB64.isNotEmpty ? base64Decode(audioB64) : Uint8List(0);

    return TTSChunk(
      audioBytes: audioBytes,
      charStartTimes: (json['char_times'] as List<dynamic>?)
              ?.map((e) => (e as num).toInt())
              .toList() ??
          [],
      charDurations: (json['char_durations'] as List<dynamic>?)
              ?.map((e) => (e as num).toInt())
              .toList() ??
          [],
      chars: (json['chars'] as List<dynamic>?)
              ?.map((e) => e as String)
              .toList() ??
          [],
      chunkIndex: json['chunk_index'] as int? ?? 0,
    );
  }

  /// Whether this chunk contains audio data
  bool get hasAudio => audioBytes.isNotEmpty;

  /// Whether this is a control message (not audio data)
  bool get isControlMessage =>
      !hasAudio && !isComplete && error == null && chunkIndex == -1;
}

/// Accumulated alignment data for the full utterance
class TTSAlignment {
  final String fullText;
  final List<String> chars;
  final List<int> charStartTimes;
  final List<int> charDurations;

  TTSAlignment({
    required this.fullText,
    required this.chars,
    required this.charStartTimes,
    required this.charDurations,
  });

  /// Get the character index that should be highlighted at a given time
  int getCharIndexAtTime(int timeMs) {
    for (int i = charStartTimes.length - 1; i >= 0; i--) {
      if (timeMs >= charStartTimes[i]) {
        return i;
      }
    }
    return -1;
  }

  /// Get the time range for a specific character index
  TimeRange? getTimeRangeForChar(int index) {
    if (index < 0 || index >= charStartTimes.length) return null;

    final start = charStartTimes[index];
    final duration =
        index < charDurations.length ? charDurations[index] : 100; // default
    return TimeRange(start, start + duration);
  }

  /// Get word boundaries for word-level highlighting (optional)
  List<WordBoundary> getWordBoundaries() {
    final boundaries = <WordBoundary>[];
    int wordStart = 0;

    for (int i = 0; i < chars.length; i++) {
      if (chars[i] == ' ' || i == chars.length - 1) {
        final wordEnd = i == chars.length - 1 ? i + 1 : i;
        if (wordStart < wordEnd) {
          boundaries.add(WordBoundary(wordStart, wordEnd));
        }
        wordStart = i + 1;
      }
    }

    return boundaries;
  }

  /// Total duration in milliseconds
  int get totalDurationMs {
    if (charStartTimes.isEmpty) return 0;
    final lastStart = charStartTimes.last;
    final lastDuration = charDurations.isNotEmpty ? charDurations.last : 100;
    return lastStart + lastDuration;
  }

  @override
  String toString() {
    return 'TTSAlignment(text: "${fullText.substring(0, fullText.length > 20 ? 20 : fullText.length)}...", '
        'chars: ${chars.length}, duration: ${totalDurationMs}ms)';
  }
}

/// Handler for TTS WebSocket streaming
class TTSStreamHandler {
  final String serverUrl;
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  bool _isConnected = false;

  final _chunkController = StreamController<TTSChunk>.broadcast();
  final _alignmentController = StreamController<TTSAlignment>.broadcast();
  final _connectionStateController = StreamController<bool>.broadcast();

  // Accumulate alignment data across chunks
  final List<String> _accumulatedChars = [];
  final List<int> _accumulatedTimes = [];
  final List<int> _accumulatedDurations = [];
  String _currentText = '';

  TTSStreamHandler({required this.serverUrl});

  /// Stream of audio chunks as they arrive
  Stream<TTSChunk> get chunkStream => _chunkController.stream;

  /// Stream of complete alignment data (emits once per utterance)
  Stream<TTSAlignment> get alignmentStream => _alignmentController.stream;

  /// Stream of connection state changes
  Stream<bool> get connectionStateStream => _connectionStateController.stream;

  /// Whether currently connected to the server
  bool get isConnected => _isConnected;

  /// Connect to the TTS server
  Future<void> connect() async {
    if (_isConnected) {
      VoiceAssistantLogger.warning('Already connected to TTS server');
      return;
    }

    VoiceAssistantLogger.info('Connecting to TTS server: $serverUrl');

    try {
      _channel = WebSocketChannel.connect(Uri.parse(serverUrl));

      // Wait for the connection to be established
      await _channel!.ready;

      _subscription = _channel!.stream.listen(
        _handleMessage,
        onError: (error) {
          VoiceAssistantLogger.error('TTS WebSocket error', error);
          _chunkController.addError(error);
          _updateConnectionState(false);
        },
        onDone: () {
          VoiceAssistantLogger.info('TTS WebSocket closed');
          _updateConnectionState(false);
        },
      );

      _updateConnectionState(true);
      VoiceAssistantLogger.info('Connected to TTS server');
    } catch (e, st) {
      VoiceAssistantLogger.error('Failed to connect to TTS server', e, st);
      _updateConnectionState(false);
      rethrow;
    }
  }

  void _updateConnectionState(bool connected) {
    _isConnected = connected;
    _connectionStateController.add(connected);
  }

  void _handleMessage(dynamic message) {
    try {
      final data = jsonDecode(message as String) as Map<String, dynamic>;
      final chunk = TTSChunk.fromJson(data);

      // Skip control messages
      if (chunk.isControlMessage) {
        VoiceAssistantLogger.debug('Received control message');
        return;
      }

      if (chunk.error != null) {
        VoiceAssistantLogger.error('TTS error: ${chunk.error}');
        _chunkController.addError(Exception(chunk.error));
        return;
      }

      if (chunk.isComplete) {
        VoiceAssistantLogger.info(
            'TTS stream complete. Total chars: ${_accumulatedChars.length}');

        // Emit complete alignment
        final alignment = TTSAlignment(
          fullText: _currentText,
          chars: List.from(_accumulatedChars),
          charStartTimes: List.from(_accumulatedTimes),
          charDurations: List.from(_accumulatedDurations),
        );

        _alignmentController.add(alignment);

        VoiceAssistantLogger.debug('Emitted alignment: $alignment');

        // Clear accumulators for next utterance
        _accumulatedChars.clear();
        _accumulatedTimes.clear();
        _accumulatedDurations.clear();

        _chunkController.add(chunk);
        return;
      }

      // Accumulate alignment data
      _accumulatedChars.addAll(chunk.chars);
      _accumulatedTimes.addAll(chunk.charStartTimes);
      _accumulatedDurations.addAll(chunk.charDurations);

      VoiceAssistantLogger.debug(
          'Received chunk ${chunk.chunkIndex}: ${chunk.audioBytes.length} bytes, '
          '${chunk.chars.length} chars');

      _chunkController.add(chunk);
    } catch (e, st) {
      VoiceAssistantLogger.error('Error parsing TTS message', e, st);
    }
  }

  /// Request TTS for the given text (single-shot mode)
  Future<void> startTTS(String text) async {
    if (_channel == null || !_isConnected) {
      throw StateError('Not connected to TTS server');
    }

    _currentText = text;
    _accumulatedChars.clear();
    _accumulatedTimes.clear();
    _accumulatedDurations.clear();

    VoiceAssistantLogger.info(
        'Starting TTS for: ${text.substring(0, text.length > 50 ? 50 : text.length)}...');

    _channel!.sink.add(jsonEncode({
      'action': 'start_tts',
      'text': text,
    }));
  }

  /// Append text chunk for streaming TTS (used when LLM streams)
  void appendText(String text) {
    if (_channel == null || !_isConnected) {
      VoiceAssistantLogger.warning('Cannot append text - not connected');
      return;
    }

    _currentText += text;
    _channel!.sink.add(jsonEncode({
      'action': 'append_tts',
      'text': text,
    }));
    
    VoiceAssistantLogger.debug('Appended text chunk: ${text.substring(0, text.length > 30 ? 30 : text.length)}...');
  }

  /// Finish the streaming TTS session
  void finishTTS() {
    if (_channel == null || !_isConnected) return;

    VoiceAssistantLogger.info('Finishing TTS stream');

    _channel!.sink.add(jsonEncode({
      'action': 'finish_tts',
    }));
  }

  /// Stop current TTS streaming
  void stopTTS() {
    if (_channel == null || !_isConnected) return;

    VoiceAssistantLogger.info('Stopping TTS');

    _channel!.sink.add(jsonEncode({
      'action': 'stop',
    }));
  }

  /// Send a ping to check connection health
  Future<void> ping() async {
    if (_channel == null || !_isConnected) return;

    _channel!.sink.add(jsonEncode({
      'action': 'ping',
    }));
  }

  /// Disconnect from the server
  Future<void> disconnect() async {
    VoiceAssistantLogger.info('Disconnecting from TTS server');

    await _subscription?.cancel();
    _subscription = null;

    await _channel?.sink.close();
    _channel = null;

    _updateConnectionState(false);
  }

  void dispose() {
    disconnect();
    _chunkController.close();
    _alignmentController.close();
    _connectionStateController.close();
  }
}
