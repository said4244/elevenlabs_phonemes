// This file is not needed but included to prevent Flutter web plugin errors
// Our package uses livekit_client which already handles web support

class LiveKitVoiceAssistantWeb {
  static void registerWith(dynamic registrar) {
    // No-op - we don't need web-specific implementation
    // LiveKit Client SDK already handles web platform
  }
}