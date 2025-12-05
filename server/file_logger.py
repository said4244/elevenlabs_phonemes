"""
file_logger.py - Alignment and Output File Logger

Saves alignment.txt (timing data) and output.txt (raw LLM text)
for each utterance.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class AlignmentLogger:
    """Logs alignment data and LLM outputs to files."""
    
    def __init__(self, base_dir: str = "logs"):
        self.alignment_dir = Path(base_dir) / "alignment"
        self.output_dir = Path(base_dir) / "outputs"
        
        # Create directories
        self.alignment_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"AlignmentLogger initialized. Alignment dir: {self.alignment_dir}")
    
    def save_alignment(
        self,
        text: str,
        chars: list[str],
        times: list[int],
        durations: Optional[list[int]] = None,
    ) -> str:
        """
        Save alignment data to JSON file.
        
        Args:
            text: The original text that was synthesized
            chars: List of characters with timing data
            times: List of start times in milliseconds for each character
            durations: Optional list of durations for each character
        
        Returns:
            The filename used for the alignment file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        # Calculate total duration
        total_duration = 0
        if times:
            total_duration = times[-1]
            if durations and len(durations) > 0:
                total_duration += durations[-1]
        
        # Build alignment structure
        alignment_data = {
            "timestamp": timestamp,
            "text": text,
            "text_length": len(text),
            "total_duration_ms": total_duration,
            "char_count": len(chars),
            "characters": [
                {
                    "index": i,
                    "char": char,
                    "start_ms": times[i] if i < len(times) else None,
                    "duration_ms": durations[i] if durations and i < len(durations) else None,
                }
                for i, char in enumerate(chars)
            ]
        }
        
        # Write timestamped alignment file
        alignment_file = self.alignment_dir / f"alignment_{timestamp}.json"
        with open(alignment_file, "w", encoding="utf-8") as f:
            json.dump(alignment_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved alignment to: {alignment_file}")
        
        # Also write a simple alignment.txt (latest only, for quick access)
        latest_file = self.alignment_dir / "alignment.txt"
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(alignment_data, f, ensure_ascii=False, indent=2)
        
        # Save raw text output
        output_file = self.output_dir / f"output_{timestamp}.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        
        # Also maintain latest output.txt
        latest_output = self.output_dir / "output.txt"
        with open(latest_output, "w", encoding="utf-8") as f:
            f.write(text)
        
        logger.info(f"Saved output text to: {output_file}")
        
        return str(alignment_file)
    
    def save_raw_output(self, text: str, source: str = "llm") -> str:
        """
        Save raw text output (e.g., from LLM) without alignment data.
        
        Args:
            text: The text to save
            source: Source identifier (e.g., "llm", "user")
        
        Returns:
            The filename used
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        output_file = self.output_dir / f"{source}_{timestamp}.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        
        logger.debug(f"Saved raw output to: {output_file}")
        return str(output_file)
    
    def get_latest_alignment(self) -> Optional[dict]:
        """
        Read the latest alignment data.
        
        Returns:
            The alignment data as a dictionary, or None if not found
        """
        latest_file = self.alignment_dir / "alignment.txt"
        
        if not latest_file.exists():
            return None
        
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read latest alignment: {e}")
            return None
    
    def get_latest_output(self) -> Optional[str]:
        """
        Read the latest output text.
        
        Returns:
            The output text, or None if not found
        """
        latest_file = self.output_dir / "output.txt"
        
        if not latest_file.exists():
            return None
        
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read latest output: {e}")
            return None


# Singleton instance for convenience
_default_logger: Optional[AlignmentLogger] = None


def get_alignment_logger(base_dir: str = "logs") -> AlignmentLogger:
    """Get or create the default alignment logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = AlignmentLogger(base_dir)
    return _default_logger


if __name__ == "__main__":
    # Test the logger
    logging.basicConfig(level=logging.DEBUG)
    
    test_logger = AlignmentLogger()
    
    # Test saving alignment
    test_text = "مرحبا بالعالم"
    test_chars = list(test_text)
    test_times = [i * 100 for i in range(len(test_chars))]
    test_durations = [90 for _ in test_chars]
    
    filename = test_logger.save_alignment(
        text=test_text,
        chars=test_chars,
        times=test_times,
        durations=test_durations
    )
    
    print(f"Saved alignment to: {filename}")
    
    # Test reading back
    latest = test_logger.get_latest_alignment()
    print(f"Latest alignment: {latest}")
