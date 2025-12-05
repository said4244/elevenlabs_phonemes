import 'dart:async';
import 'dart:convert';
import 'package:livekit_client/livekit_client.dart';
import 'package:http/http.dart' as http;
import 'logger.dart';

class ConnectionManager {
  Room? _room;
  EventsListener<RoomEvent>? _listener;
  RemoteParticipant? _agent;
  
  final String tokenUrl;
  final String? livekitUrl;
  final void Function(String status)? onStatusChanged;
  final void Function(String event, Map<String, dynamic> data)? onEvent;

  ConnectionManager({
    required this.tokenUrl,
    this.livekitUrl,
    this.onStatusChanged,
    this.onEvent,
  });

  Future<Map<String, String>> _getConnectionInfo() async {
    VoiceAssistantLogger.info('Fetching connection token from: $tokenUrl');
    
    try {
      final response = await http.get(Uri.parse(tokenUrl));
      
      VoiceAssistantLogger.debug('Token server response status: ${response.statusCode}');
      VoiceAssistantLogger.debug('Token server response headers: ${response.headers}');
      
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        VoiceAssistantLogger.info('Successfully obtained connection token');
        VoiceAssistantLogger.debug('Token response: ${response.body}');
        
        // Check for required fields
        final token = data['accessToken'] ?? data['token'] ?? '';
        final url = data['url'] ?? livekitUrl ?? 'wss://cloud.livekit.io';
        
        if (token.isEmpty) {
          throw Exception('Token server returned empty token. Response: ${response.body}');
        }
        
        return {
          'token': token.toString(), // Ensure it's a string
          'url': url.toString(),
        };
      } else {
        throw Exception('Token server returned ${response.statusCode}: ${response.body}');
      }
    } catch (e, stackTrace) {
      VoiceAssistantLogger.error('Failed to get connection token', e, stackTrace);
      if (e.toString().contains('XMLHttpRequest error')) {
        VoiceAssistantLogger.error('This looks like a CORS error. Make sure:');
        VoiceAssistantLogger.error('1. Token server is running at $tokenUrl');
        VoiceAssistantLogger.error('2. Token server has CORS enabled');
        VoiceAssistantLogger.error('3. You\'re using --web-browser-flag "--disable-web-security" for local dev');
      }
      rethrow;
    }
  }

  Future<void> connect() async {
    VoiceAssistantLogger.info('Starting connection process');
    _notifyStatus('Connecting...');
    
    try {
      // Get connection info
      VoiceAssistantLogger.info('Getting connection token...');
      final connectionInfo = await _getConnectionInfo();
      final token = connectionInfo['token']!;
      final url = connectionInfo['url']!;
      
      VoiceAssistantLogger.info('Got token, connecting to LiveKit at: $url');
      VoiceAssistantLogger.debug('Token (first 20 chars): ${token.substring(0, token.length > 20 ? 20 : token.length)}...');
      
      // Create room
      _room = Room();
      
      // Set up event listeners before connecting
      _setupEventListeners();
      
      // Configure room options for optimal web performance
      const roomOptions = RoomOptions(
        adaptiveStream: true,
        dynacast: true,
        defaultAudioCaptureOptions: AudioCaptureOptions(
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        ),
        defaultVideoPublishOptions: VideoPublishOptions(
          simulcast: false,
        ),
        defaultAudioPublishOptions: AudioPublishOptions(
          dtx: true,
        ),
      );
      
      VoiceAssistantLogger.info('Attempting to connect to room...');
      VoiceAssistantLogger.debug('URL: $url');
      VoiceAssistantLogger.debug('Token length: ${token.length}');
      VoiceAssistantLogger.debug('Token (first 50 chars): ${token.substring(0, token.length > 50 ? 50 : token.length)}...');
      
      // Connect to room
      await _room!.connect(url, token, roomOptions: roomOptions);
      
      VoiceAssistantLogger.info('Successfully connected to LiveKit room');
      VoiceAssistantLogger.info('Local participant SID: ${_room!.localParticipant?.sid}');
      
      // Enable microphone
      await _room!.localParticipant?.setMicrophoneEnabled(true);
      VoiceAssistantLogger.info('Microphone enabled');
      
      _notifyStatus('Connected - Waiting for agent...');
      
    } catch (e, stackTrace) {
      VoiceAssistantLogger.error('Connection failed with error: ${e.runtimeType}', e, stackTrace);
      VoiceAssistantLogger.error('Error details: $e');
      _notifyStatus('Connection failed: ${e.toString()}');
      rethrow;
    }
  }

  void _setupEventListeners() {
    if (_room == null) return;
    
    _listener = _room!.createListener();
    
    VoiceAssistantLogger.info('Setting up event listeners');
    
    _listener!
      ..on<ParticipantConnectedEvent>((event) {
        VoiceAssistantLogger.info('Participant connected: ${event.participant.identity}');
        _notifyEvent('participant_connected', {
          'identity': event.participant.identity,
          'sid': event.participant.sid,
        });
        
        if (event.participant.identity?.toLowerCase().contains('agent') ?? false) {
          _agent = event.participant as RemoteParticipant;
          _notifyStatus('Agent connected - Ready to talk!');
          VoiceAssistantLogger.info('Agent identified and connected');
        }
      })
      ..on<ParticipantDisconnectedEvent>((event) {
        VoiceAssistantLogger.info('Participant disconnected: ${event.participant.identity}');
        _notifyEvent('participant_disconnected', {
          'identity': event.participant.identity,
        });
        
        if (event.participant == _agent) {
          _agent = null;
          _notifyStatus('Agent disconnected');
        }
      })
      ..on<TrackSubscribedEvent>((event) {
        VoiceAssistantLogger.info('Track subscribed: ${event.track.kind} from ${event.participant.identity}');
        _notifyEvent('track_subscribed', {
          'kind': event.track.kind.toString(),
          'participant': event.participant.identity,
        });
      })
      ..on<TrackUnsubscribedEvent>((event) {
        VoiceAssistantLogger.info('Track unsubscribed: ${event.track.kind} from ${event.participant.identity}');
      })
      ..on<DataReceivedEvent>((event) {
        try {
          final data = jsonDecode(utf8.decode(event.data));
          VoiceAssistantLogger.debug('Data received: $data');
          _notifyEvent('data_received', data);
        } catch (e) {
          VoiceAssistantLogger.debug('Received non-JSON data: ${event.data.length} bytes');
        }
      })
      ..on<RoomDisconnectedEvent>((event) {
        VoiceAssistantLogger.warning('Room disconnected: ${event.reason}');
        _notifyStatus('Disconnected');
        _notifyEvent('disconnected', {'reason': event.reason});
      })
      ..on<RoomReconnectingEvent>((event) {
        VoiceAssistantLogger.info('Room reconnecting...');
        _notifyStatus('Reconnecting...');
      })
      ..on<RoomReconnectedEvent>((event) {
        VoiceAssistantLogger.info('Room reconnected');
        _notifyStatus('Connected');
      });
    
    VoiceAssistantLogger.info('Event listeners configured');
  }

  Future<void> disconnect() async {
    VoiceAssistantLogger.info('Disconnecting from LiveKit');
    
    try {
      await _room?.localParticipant?.setMicrophoneEnabled(false);
      await _room?.disconnect();
      await _listener?.dispose();
      _room?.dispose();
      
      _room = null;
      _listener = null;
      _agent = null;
      
      _notifyStatus('Disconnected');
      VoiceAssistantLogger.info('Successfully disconnected');
    } catch (e) {
      VoiceAssistantLogger.error('Error during disconnect', e);
    }
  }

  bool get isConnected => _room != null && _room!.connectionState == ConnectionState.connected;
  bool get hasAgent => _agent != null;

  void _notifyStatus(String status) {
    VoiceAssistantLogger.debug('Status changed: $status');
    onStatusChanged?.call(status);
  }

  void _notifyEvent(String event, Map<String, dynamic> data) {
    VoiceAssistantLogger.trace('Event: $event - Data: $data');
    onEvent?.call(event, data);
  }

  void dispose() {
    disconnect();
  }
}