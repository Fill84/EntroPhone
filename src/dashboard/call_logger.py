"""Call logging - records call history with transcripts and metadata.

Stores call data as JSON files, one per day, in /app/logs/calls/.
"""

import json
import logging
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config import get_path

logger = logging.getLogger(__name__)


class CallLogger:
    """Logs call events to daily JSON files."""

    def __init__(self, log_dir: str = None):
        if log_dir is None:
            log_dir = str(get_path("logs_dir") / "calls")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._active_calls: Dict[str, dict] = {}

    def start_call(self, caller_number: str, caller_name: str = "", direction: str = "incoming") -> str:
        """Record a new call starting. Returns call_id."""
        call_id = str(uuid.uuid4())[:8]
        self._active_calls[call_id] = {
            "id": call_id,
            "direction": direction,
            "caller_number": caller_number,
            "caller_name": caller_name,
            "start_time": time.time(),
            "end_time": None,
            "duration": None,
            "transcript": [],
            "recording_path": None,
        }
        logger.info("Call logged: %s (%s) from %s", call_id, direction, caller_number)
        return call_id

    def add_transcript(self, call_id: str, role: str, text: str, language: str = "nl") -> None:
        """Add a transcript entry to an active call."""
        if call_id not in self._active_calls:
            return
        self._active_calls[call_id]["transcript"].append({
            "role": role,
            "text": text,
            "language": language,
            "timestamp": time.time(),
        })

    def set_recording(self, call_id: str, recording_path: str) -> None:
        """Set the recording file path for a call."""
        if call_id not in self._active_calls:
            return
        self._active_calls[call_id]["recording_path"] = recording_path

    def end_call(self, call_id: str) -> None:
        """Finalize and save a call log."""
        if call_id not in self._active_calls:
            return

        call = self._active_calls.pop(call_id)
        call["end_time"] = time.time()
        call["duration"] = round(call["end_time"] - call["start_time"], 1)

        # Save to daily log file
        date_str = datetime.fromtimestamp(call["start_time"]).strftime("%Y-%m-%d")
        log_file = self.log_dir / f"{date_str}.json"

        entries = []
        if log_file.exists():
            try:
                entries = json.loads(log_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                entries = []

        entries.append(call)
        log_file.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Call %s saved (duration=%.1fs, %d transcript entries)",
                     call_id, call["duration"], len(call["transcript"]))

    def get_history(self, days: int = 7, limit: int = 50) -> List[dict]:
        """Get call history for the last N days."""
        all_calls = []
        today = datetime.now()

        for i in range(days):
            from datetime import timedelta
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            log_file = self.log_dir / f"{date_str}.json"

            if log_file.exists():
                try:
                    entries = json.loads(log_file.read_text(encoding="utf-8"))
                    all_calls.extend(entries)
                except (json.JSONDecodeError, Exception):
                    pass

        # Sort by start_time descending and limit
        all_calls.sort(key=lambda c: c.get("start_time", 0), reverse=True)
        return all_calls[:limit]

    def get_call(self, call_id: str) -> Optional[dict]:
        """Get a specific call by ID."""
        # Check active calls first
        if call_id in self._active_calls:
            return self._active_calls[call_id]

        # Search recent logs
        for entry in self.get_history(days=30, limit=500):
            if entry.get("id") == call_id:
                return entry
        return None

    def get_recording_path(self, call_id: str) -> Optional[str]:
        """Get the recording file path for a call."""
        call = self.get_call(call_id)
        if call and call.get("recording_path"):
            path = Path(call["recording_path"])
            if path.exists():
                return str(path)
        return None
