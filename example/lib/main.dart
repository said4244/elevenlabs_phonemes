/// main.dart
///
/// Demo app with streaming TTS and synchronized highlighting.

import 'package:flutter/material.dart';
import 'package:elevenlabs_phonemes/elevenlabs_phonemes.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Voice Assistant Demo',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.green),
        useMaterial3: true,
      ),
      home: const VoiceAssistantDemo(),
    );
  }
}

class VoiceAssistantDemo extends StatefulWidget {
  const VoiceAssistantDemo({super.key});

  @override
  State<VoiceAssistantDemo> createState() => _VoiceAssistantDemoState();
}

class _VoiceAssistantDemoState extends State<VoiceAssistantDemo> {
  late final VoiceAssistant assistant;
  bool isActive = false;
  String? currentText;
  TTSAlignment? currentAlignment;
  bool isSpeaking = false;

  // Test texts for demo
  final List<String> testTexts = [
    'مرحبا بالعالم، كيف حالك اليوم؟',  // Arabic
    'Hello, how are you today?',        // English
    'أهلاً وسهلاً بك في تطبيق المساعد الصوتي',  // Arabic - longer
  ];
  int currentTestIndex = 0;

  @override
  void initState() {
    super.initState();

    // Configure the assistant with both LiveKit and TTS streaming
    assistant = VoiceAssistant(
      config: VoiceAssistantConfig(
        tokenUrl: 'http://localhost:8080/token',
        ttsServerUrl: 'ws://localhost:8081/stream_tts',
        enableLogging: true,
        enableTTSStreaming: true,
      ),
    );

    // Listen to AI response text
    assistant.textStream.listen((text) {
      setState(() {
        currentText = text;
      });
    });

    // Listen to alignment data for highlighting
    assistant.alignmentStream.listen((alignment) {
      setState(() {
        currentAlignment = alignment;
      });
    });

    // Listen to audio state
    assistant.audioStateStream.listen((playing) {
      setState(() {
        isSpeaking = playing;
      });
    });

    // Listen to status updates
    assistant.statusStream.listen((status) {
      debugPrint('Status: $status');
    });
  }

  @override
  void dispose() {
    assistant.dispose();
    super.dispose();
  }

  void _toggleAssistant() async {
    if (!isActive) {
      try {
        await assistant.start();
        setState(() {
          isActive = true;
        });
      } catch (e) {
        _showError('Failed to start: $e');
      }
    } else {
      await assistant.stop();
      setState(() {
        isActive = false;
        currentText = null;
        currentAlignment = null;
      });
    }
  }

  void _testTTS() async {
    if (!isActive) {
      _showError('Please start the assistant first');
      return;
    }

    if (!assistant.isTTSConnected) {
      _showError('TTS server not connected');
      return;
    }

    final text = testTexts[currentTestIndex];
    currentTestIndex = (currentTestIndex + 1) % testTexts.length;

    try {
      await assistant.speak(text);
    } catch (e) {
      _showError('Failed to speak: $e');
    }
  }

  void _stopSpeaking() {
    assistant.stopSpeaking();
    setState(() {
      isSpeaking = false;
    });
  }

  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: Colors.red),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Voice Assistant Demo'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          // Connection status indicator
          Padding(
            padding: const EdgeInsets.all(8.0),
            child: Row(
              children: [
                Icon(
                  isActive ? Icons.cloud_done : Icons.cloud_off,
                  color: isActive ? Colors.green : Colors.grey,
                ),
                const SizedBox(width: 4),
                Text(
                  isActive ? 'Connected' : 'Disconnected',
                  style: TextStyle(
                    color: isActive ? Colors.green : Colors.grey,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            children: [
              // Status display
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Row(
                    children: [
                      Icon(
                        _getStatusIcon(),
                        color: _getStatusColor(),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          assistant.status,
                          style: Theme.of(context).textTheme.bodyLarge,
                        ),
                      ),
                      if (isSpeaking)
                        const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 24),

              // Highlighted text display
              Expanded(
                child: Card(
                  child: Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(16),
                    child: currentText != null
                        ? SyncedTextHighlighter(
                            text: currentText!,
                            alignment: currentAlignment,
                            positionStream: assistant.audioPositionStream,
                            mode: HighlightMode.character,
                            baseStyle: const TextStyle(
                              fontSize: 28,
                              color: Colors.black87,
                              height: 1.5,
                            ),
                            highlightStyle: TextStyle(
                              fontSize: 28,
                              color: Colors.white,
                              backgroundColor: Colors.green.shade600,
                              height: 1.5,
                            ),
                          )
                        : Center(
                            child: Text(
                              'Press "Test TTS" to speak some text',
                              style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                                    color: Colors.grey,
                                  ),
                              textAlign: TextAlign.center,
                            ),
                          ),
                  ),
                ),
              ),
              const SizedBox(height: 24),

              // Control buttons
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  // Test TTS button
                  ElevatedButton.icon(
                    onPressed: isActive && !isSpeaking ? _testTTS : null,
                    icon: const Icon(Icons.record_voice_over),
                    label: const Text('Test TTS'),
                  ),
                  const SizedBox(width: 16),

                  // Stop button
                  ElevatedButton.icon(
                    onPressed: isSpeaking ? _stopSpeaking : null,
                    icon: const Icon(Icons.stop),
                    label: const Text('Stop'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.orange,
                      foregroundColor: Colors.white,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 24),

              // Main control button
              GestureDetector(
                onTap: _toggleAssistant,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 300),
                  width: 140,
                  height: 140,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: isActive ? Colors.red : Colors.green,
                    boxShadow: [
                      BoxShadow(
                        color:
                            (isActive ? Colors.red : Colors.green).withOpacity(0.5),
                        blurRadius: isActive ? 30 : 10,
                        spreadRadius: isActive ? 10 : 5,
                      ),
                    ],
                  ),
                  child: Icon(
                    isActive ? Icons.stop : Icons.mic,
                    size: 56,
                    color: Colors.white,
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Text(
                isActive ? 'Tap to disconnect' : 'Tap to connect',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }

  IconData _getStatusIcon() {
    switch (assistant.state) {
      case VoiceConnectionState.connected:
        return Icons.check_circle;
      case VoiceConnectionState.connecting:
        return Icons.sync;
      case VoiceConnectionState.error:
        return Icons.error;
      case VoiceConnectionState.disconnected:
        return Icons.cloud_off;
    }
  }

  Color _getStatusColor() {
    switch (assistant.state) {
      case VoiceConnectionState.connected:
        return Colors.green;
      case VoiceConnectionState.connecting:
        return Colors.orange;
      case VoiceConnectionState.error:
        return Colors.red;
      case VoiceConnectionState.disconnected:
        return Colors.grey;
    }
  }
}