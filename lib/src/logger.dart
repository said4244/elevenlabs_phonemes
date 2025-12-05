import 'package:logger/logger.dart';

class VoiceAssistantLogger {
  static final Logger _logger = Logger(
    printer: PrettyPrinter(
      methodCount: 2,
      errorMethodCount: 8,
      lineLength: 120,
      colors: true,
      printEmojis: true,
      printTime: true,
    ),
    level: Level.debug,
  );

  static void debug(String message, [dynamic error, StackTrace? stackTrace]) {
    _logger.d('[VoiceAssistant] $message', error: error, stackTrace: stackTrace);
  }

  static void info(String message, [dynamic error, StackTrace? stackTrace]) {
    _logger.i('[VoiceAssistant] $message', error: error, stackTrace: stackTrace);
  }

  static void warning(String message, [dynamic error, StackTrace? stackTrace]) {
    _logger.w('[VoiceAssistant] $message', error: error, stackTrace: stackTrace);
  }

  static void error(String message, [dynamic error, StackTrace? stackTrace]) {
    _logger.e('[VoiceAssistant] $message', error: error, stackTrace: stackTrace);
  }

  static void trace(String message) {
    _logger.t('[VoiceAssistant] $message');
  }
}