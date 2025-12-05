/// audio_player_stream.dart
///
/// Handles real-time PCM audio playback from streaming chunks.
/// Uses Web Audio API on web platform.

import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'logger.dart';

// Web Audio API imports
import 'dart:js_interop';
import 'package:web/web.dart' as web;

/// Configuration for audio playback
class AudioConfig {
  final int sampleRate;
  final int channels;
  final int bitsPerSample;

  const AudioConfig({
    this.sampleRate = 22050,
    this.channels = 1,
    this.bitsPerSample = 16,
  });

  int get bytesPerSample => bitsPerSample ~/ 8;
  int get bytesPerSecond => sampleRate * channels * bytesPerSample;
  
  /// Calculate duration in milliseconds for given byte count
  int bytesToMs(int bytes) {
    return (bytes * 1000 / bytesPerSecond).round();
  }
  
  /// Calculate byte count for given duration in milliseconds
  int msToBytes(int ms) {
    return (ms * bytesPerSecond / 1000).round();
  }
}

/// Manages streaming audio playback
class StreamingAudioPlayer {
  final AudioConfig config;

  final _audioBuffer = <Uint8List>[];
  int _totalBytesQueued = 0;
  int _totalBytesPlayed = 0;
  bool _isPlaying = false;
  DateTime? _playbackStartTime;
  Timer? _positionTimer;
  
  // Web Audio API objects
  web.AudioContext? _audioContext;
  double _nextPlayTime = 0;
  bool _isProcessing = false;

  final _positionController = StreamController<int>.broadcast();
  final _stateController = StreamController<bool>.broadcast();
  final _completionController = StreamController<void>.broadcast();

  StreamingAudioPlayer({this.config = const AudioConfig()});

  /// Stream of current playback position in milliseconds
  Stream<int> get positionStream => _positionController.stream;

  /// Stream of playback state (true = playing)
  Stream<bool> get stateStream => _stateController.stream;

  /// Stream that emits when playback completes
  Stream<void> get completionStream => _completionController.stream;

  /// Whether audio is currently playing
  bool get isPlaying => _isPlaying;

  /// Current playback position in milliseconds
  int get currentPositionMs {
    if (_playbackStartTime == null || !_isPlaying) return 0;
    return DateTime.now().difference(_playbackStartTime!).inMilliseconds;
  }

  /// Total duration of queued audio in milliseconds
  int get totalDurationMs => config.bytesToMs(_totalBytesQueued);

  /// Initialize Web Audio context (must be called after user interaction)
  Future<void> initialize() async {
    if (!kIsWeb) return;
    
    try {
      _audioContext = web.AudioContext();
      VoiceAssistantLogger.info('Web Audio context initialized, state: ${_audioContext!.state}');
    } catch (e) {
      VoiceAssistantLogger.error('Failed to initialize Web Audio context', e);
    }
  }

  /// Add audio data to the playback buffer
  void addAudioData(Uint8List audioBytes) {
    if (audioBytes.isEmpty) {
      VoiceAssistantLogger.warning('Received empty audio data');
      return;
    }

    _audioBuffer.add(audioBytes);
    _totalBytesQueued += audioBytes.length;

    VoiceAssistantLogger.info(
        'AUDIO: Added ${audioBytes.length} bytes to buffer '
        '(total: $_totalBytesQueued bytes, ${totalDurationMs}ms, playing: $_isPlaying)');

    // Start playback if not already playing
    if (!_isPlaying) {
      _startPlayback();
    }
    
    // Process new audio data
    _processAudioBuffer();
  }

  void _startPlayback() {
    if (_isPlaying) return;

    _isPlaying = true;
    _playbackStartTime = DateTime.now();
    _stateController.add(true);

    VoiceAssistantLogger.info('Starting audio playback');

    // Start position tracking timer
    _positionTimer?.cancel();
    _positionTimer = Timer.periodic(const Duration(milliseconds: 50), (timer) {
      if (!_isPlaying) {
        timer.cancel();
        return;
      }
      _positionController.add(currentPositionMs);
    });

    if (kIsWeb) {
      _initWebAudio();
    }
  }

  void _initWebAudio() {
    if (_audioContext == null) {
      _audioContext = web.AudioContext();
      VoiceAssistantLogger.info('Created new AudioContext');
    }
    
    // Resume context if suspended (browser autoplay policy)
    if (_audioContext!.state == 'suspended') {
      _audioContext!.resume();
      VoiceAssistantLogger.info('Resumed suspended AudioContext');
    }
    
    _nextPlayTime = _audioContext!.currentTime;
  }

  void _processAudioBuffer() {
    if (!kIsWeb || _audioContext == null || _isProcessing) return;
    if (_audioBuffer.isEmpty) return;
    
    _isProcessing = true;
    
    try {
      // Process all buffered chunks
      while (_audioBuffer.isNotEmpty) {
        final chunk = _audioBuffer.removeAt(0);
        _scheduleChunk(chunk);
        _totalBytesPlayed += chunk.length;
      }
    } finally {
      _isProcessing = false;
    }
  }

  void _scheduleChunk(Uint8List pcmData) {
    if (_audioContext == null) {
      VoiceAssistantLogger.error('AUDIO: Cannot schedule chunk - AudioContext is null');
      return;
    }
    
    VoiceAssistantLogger.info('AUDIO: Scheduling chunk of ${pcmData.length} bytes, context state: ${_audioContext!.state}');
    
    try {
      // Convert PCM bytes to samples
      final sampleCount = pcmData.length ~/ 2; // 16-bit = 2 bytes per sample
      
      // Create audio buffer
      final audioBuffer = _audioContext!.createBuffer(
        config.channels,
        sampleCount,
        config.sampleRate,
      );
      
      // Get channel data and fill with converted PCM samples
      final channelData = audioBuffer.getChannelData(0);
      final byteData = pcmData.buffer.asByteData(pcmData.offsetInBytes, pcmData.length);
      
      // Convert PCM to float samples and copy to Web Audio buffer
      // Use the toDart extension to get a Float32List view
      final floatList = channelData.toDart;
      for (int i = 0; i < sampleCount; i++) {
        // Read 16-bit signed PCM sample (little-endian)
        final sample = byteData.getInt16(i * 2, Endian.little);
        // Convert to float (-1.0 to 1.0)
        floatList[i] = sample / 32768.0;
      }
      
      // Create buffer source and connect to destination
      final source = _audioContext!.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(_audioContext!.destination);
      
      // Schedule playback
      final currentTime = _audioContext!.currentTime;
      if (_nextPlayTime < currentTime) {
        _nextPlayTime = currentTime;
      }
      
      source.start(_nextPlayTime);
      
      // Update next play time
      final chunkDuration = sampleCount / config.sampleRate;
      _nextPlayTime += chunkDuration;
      
      // Set up completion callback
      source.onended = ((web.Event event) {
        _checkPlaybackComplete();
      }).toJS;
      
      VoiceAssistantLogger.info(
        'AUDIO: Scheduled ${pcmData.length} bytes (${(chunkDuration * 1000).round()}ms) at ${_nextPlayTime.toStringAsFixed(3)}s'
      );
    } catch (e, st) {
      VoiceAssistantLogger.error('AUDIO: Error scheduling audio chunk', e, st);
    }
  }

  void _checkPlaybackComplete() {
    // Check if all audio has been played
    if (_audioBuffer.isEmpty && _totalBytesPlayed >= _totalBytesQueued) {
      // Add small delay to ensure last chunk finished
      Future.delayed(const Duration(milliseconds: 100), () {
        if (_audioBuffer.isEmpty) {
          _onPlaybackComplete();
        }
      });
    }
  }

  void _onPlaybackComplete() {
    VoiceAssistantLogger.info('Audio playback complete');
    _isPlaying = false;
    _positionTimer?.cancel();
    _stateController.add(false);
    _completionController.add(null);
  }

  /// Signal that no more audio data will be added (stream complete)
  void markComplete() {
    VoiceAssistantLogger.debug('Audio stream marked complete');
    // Process any remaining buffered audio
    if (kIsWeb && _audioBuffer.isNotEmpty) {
      _processAudioBuffer();
    }
  }

  /// Stop playback and clear buffer
  void stop() {
    _positionTimer?.cancel();
    _positionTimer = null;
    _isPlaying = false;
    _audioBuffer.clear();
    _totalBytesQueued = 0;
    _totalBytesPlayed = 0;
    _playbackStartTime = null;
    _nextPlayTime = 0;
    _stateController.add(false);

    VoiceAssistantLogger.info('Audio playback stopped');
  }

  /// Reset for new utterance (keeps AudioContext alive)
  void reset() {
    VoiceAssistantLogger.info('Resetting audio player for new utterance');
    _positionTimer?.cancel();
    _positionTimer = null;
    _isPlaying = false;
    _audioBuffer.clear();
    _totalBytesQueued = 0;
    _totalBytesPlayed = 0;
    _playbackStartTime = null;
    // Reset _nextPlayTime to current time so audio starts immediately
    if (_audioContext != null) {
      _nextPlayTime = _audioContext!.currentTime;
    } else {
      _nextPlayTime = 0;
    }
    _stateController.add(false);
  }

  /// Seek to a specific position (limited support in streaming mode)
  void seekTo(int positionMs) {
    if (positionMs < 0 || positionMs > totalDurationMs) return;
    
    // For streaming audio, seeking is limited
    // We can only adjust the position tracker
    _playbackStartTime = DateTime.now().subtract(Duration(milliseconds: positionMs));
    _positionController.add(positionMs);
    
    VoiceAssistantLogger.debug('Seeked to ${positionMs}ms');
  }

  void dispose() {
    stop();
    _positionController.close();
    _stateController.close();
    _completionController.close();
  }
}

/// Web Audio API helper for browser-based playback
/// This would be implemented using dart:js_interop in production
class WebAudioHelper {
  // AudioContext instance would be created here
  // This is a placeholder for the actual Web Audio API implementation
  
  static bool get isSupported => kIsWeb;
  
  /// Create and play audio from PCM data
  static Future<void> playPCM({
    required Uint8List pcmData,
    required int sampleRate,
    required int channels,
    required int bitsPerSample,
  }) async {
    if (!kIsWeb) {
      throw UnsupportedError('WebAudioHelper only works on web platform');
    }
    
    // In production, implement using:
    // 
    // import 'dart:html' as html;
    // 
    // final audioContext = html.AudioContext();
    // final sampleCount = pcmData.length ~/ (bitsPerSample ~/ 8);
    // final buffer = audioContext.createBuffer(channels, sampleCount, sampleRate);
    // 
    // // Convert PCM to float samples
    // final channelData = buffer.getChannelData(0);
    // for (int i = 0; i < sampleCount; i++) {
    //   final sample = pcmData.buffer.asByteData().getInt16(i * 2, Endian.little);
    //   channelData[i] = sample / 32768.0;
    // }
    // 
    // final source = audioContext.createBufferSource();
    // source.buffer = buffer;
    // source.connectNode(audioContext.destination!);
    // source.start();
    
    VoiceAssistantLogger.debug('WebAudioHelper.playPCM called with ${pcmData.length} bytes');
  }
}
