/// text_highlighter.dart
///
/// Widget that displays text with real-time character/word highlighting
/// synchronized to audio playback.

import 'dart:async';
import 'package:flutter/material.dart';
import 'tts_stream_handler.dart';
import 'logger.dart';

/// Highlighting mode
enum HighlightMode {
  /// Highlight each character as it's spoken
  character,
  /// Highlight whole words as they're spoken
  word,
}

/// Widget that displays text with synchronized highlighting
class SyncedTextHighlighter extends StatefulWidget {
  /// The text to display
  final String text;
  
  /// Alignment data for synchronization (optional - if null, no highlighting)
  final TTSAlignment? alignment;
  
  /// Stream of playback position in milliseconds
  final Stream<int>? positionStream;
  
  /// Highlighting mode (character or word level)
  final HighlightMode mode;
  
  /// Style for unhighlighted text
  final TextStyle baseStyle;
  
  /// Style for highlighted text
  final TextStyle highlightStyle;
  
  /// Text direction (auto-detected if not specified)
  final TextDirection? textDirection;
  
  /// Text alignment
  final TextAlign textAlign;
  
  /// Whether to animate the highlighting transition
  final bool animate;
  
  /// Callback when highlighting index changes
  final void Function(int index)? onHighlightChanged;

  const SyncedTextHighlighter({
    super.key,
    required this.text,
    this.alignment,
    this.positionStream,
    this.mode = HighlightMode.character,
    this.baseStyle = const TextStyle(
      fontSize: 24,
      color: Colors.black87,
    ),
    this.highlightStyle = const TextStyle(
      fontSize: 24,
      color: Colors.white,
      backgroundColor: Colors.green,
    ),
    this.textDirection,
    this.textAlign = TextAlign.start,
    this.animate = false,
    this.onHighlightChanged,
  });

  @override
  State<SyncedTextHighlighter> createState() => _SyncedTextHighlighterState();
}

class _SyncedTextHighlighterState extends State<SyncedTextHighlighter> {
  int _highlightedIndex = -1;
  StreamSubscription<int>? _positionSubscription;

  @override
  void initState() {
    super.initState();
    _setupPositionListener();
  }

  @override
  void didUpdateWidget(SyncedTextHighlighter oldWidget) {
    super.didUpdateWidget(oldWidget);
    
    // Reset if position stream changed
    if (oldWidget.positionStream != widget.positionStream) {
      _positionSubscription?.cancel();
      _setupPositionListener();
    }
    
    // Reset if alignment changed
    if (oldWidget.alignment != widget.alignment) {
      _highlightedIndex = -1;
    }
  }

  void _setupPositionListener() {
    _positionSubscription = widget.positionStream?.listen((positionMs) {
      if (widget.alignment == null) return;

      final newIndex = widget.alignment!.getCharIndexAtTime(positionMs);
      if (newIndex != _highlightedIndex) {
        setState(() {
          _highlightedIndex = newIndex;
        });
        widget.onHighlightChanged?.call(newIndex);
      }
    });
  }

  @override
  void dispose() {
    _positionSubscription?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Detect RTL for Arabic
    final isRtl = widget.textDirection == TextDirection.rtl ||
        _containsArabic(widget.text);

    return Directionality(
      textDirection: isRtl ? TextDirection.rtl : TextDirection.ltr,
      child: RichText(
        textDirection: isRtl ? TextDirection.rtl : TextDirection.ltr,
        textAlign: widget.textAlign,
        text: _buildTextSpan(),
      ),
    );
  }

  TextSpan _buildTextSpan() {
    // No highlighting if no alignment or index
    if (_highlightedIndex < 0 || widget.alignment == null) {
      return TextSpan(text: widget.text, style: widget.baseStyle);
    }

    final text = widget.text;
    
    // Calculate highlight end based on mode
    int highlightEnd = _highlightedIndex + 1;
    
    if (widget.mode == HighlightMode.word && widget.alignment != null) {
      // Find word boundary
      final boundaries = widget.alignment!.getWordBoundaries();
      for (final boundary in boundaries) {
        if (_highlightedIndex >= boundary.start && _highlightedIndex < boundary.end) {
          highlightEnd = boundary.end;
          break;
        }
      }
    }

    highlightEnd = highlightEnd.clamp(0, text.length);

    return TextSpan(
      children: [
        TextSpan(
          text: text.substring(0, highlightEnd),
          style: widget.highlightStyle,
        ),
        TextSpan(
          text: text.substring(highlightEnd),
          style: widget.baseStyle,
        ),
      ],
    );
  }

  /// Check if text contains Arabic characters
  bool _containsArabic(String text) {
    // Arabic Unicode range: 0x0600-0x06FF
    // Extended Arabic: 0x0750-0x077F (Arabic Supplement)
    // Arabic Extended-A: 0x08A0-0x08FF
    return text.runes.any((r) =>
        (r >= 0x0600 && r <= 0x06FF) ||
        (r >= 0x0750 && r <= 0x077F) ||
        (r >= 0x08A0 && r <= 0x08FF));
  }
}

/// Convenience widget that combines TTS handler with highlighting
/// Useful for standalone text-to-speech display
class TTSTextDisplay extends StatefulWidget {
  /// The text to speak and display
  final String text;
  
  /// TTS stream handler instance
  final TTSStreamHandler ttsHandler;
  
  /// Highlighting mode
  final HighlightMode highlightMode;
  
  /// Base text style
  final TextStyle? baseStyle;
  
  /// Highlighted text style
  final TextStyle? highlightStyle;
  
  /// Whether to start TTS automatically when widget mounts
  final bool autoPlay;
  
  /// Callback when TTS completes
  final VoidCallback? onComplete;

  const TTSTextDisplay({
    super.key,
    required this.text,
    required this.ttsHandler,
    this.highlightMode = HighlightMode.character,
    this.baseStyle,
    this.highlightStyle,
    this.autoPlay = true,
    this.onComplete,
  });

  @override
  State<TTSTextDisplay> createState() => _TTSTextDisplayState();
}

class _TTSTextDisplayState extends State<TTSTextDisplay> {
  TTSAlignment? _alignment;
  final _positionController = StreamController<int>.broadcast();
  StreamSubscription? _alignmentSubscription;
  StreamSubscription? _chunkSubscription;
  bool _isComplete = false;

  @override
  void initState() {
    super.initState();
    
    // Listen for complete alignment
    _alignmentSubscription =
        widget.ttsHandler.alignmentStream.listen((alignment) {
      setState(() {
        _alignment = alignment;
      });
      VoiceAssistantLogger.debug('Received alignment: $alignment');
    });
    
    // Listen for chunks to detect completion
    _chunkSubscription = widget.ttsHandler.chunkStream.listen((chunk) {
      if (chunk.isComplete && !_isComplete) {
        _isComplete = true;
        widget.onComplete?.call();
      }
    });

    if (widget.autoPlay) {
      _startTTS();
    }
  }

  Future<void> _startTTS() async {
    try {
      await widget.ttsHandler.startTTS(widget.text);
    } catch (e) {
      VoiceAssistantLogger.error('Failed to start TTS', e);
    }
  }

  @override
  void dispose() {
    _alignmentSubscription?.cancel();
    _chunkSubscription?.cancel();
    _positionController.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SyncedTextHighlighter(
      text: widget.text,
      alignment: _alignment,
      positionStream: _positionController.stream,
      mode: widget.highlightMode,
      baseStyle: widget.baseStyle ??
          const TextStyle(fontSize: 24, color: Colors.black87),
      highlightStyle: widget.highlightStyle ??
          const TextStyle(
            fontSize: 24,
            color: Colors.white,
            backgroundColor: Colors.green,
          ),
    );
  }
}

/// A stateless helper widget for displaying Arabic text with proper RTL handling
class ArabicText extends StatelessWidget {
  final String text;
  final TextStyle? style;
  final TextAlign textAlign;

  const ArabicText({
    super.key,
    required this.text,
    this.style,
    this.textAlign = TextAlign.right,
  });

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Text(
        text,
        style: style,
        textAlign: textAlign,
        textDirection: TextDirection.rtl,
      ),
    );
  }
}
