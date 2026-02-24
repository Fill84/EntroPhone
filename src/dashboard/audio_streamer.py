"""WebSocket event handlers for live log and audio streaming."""

import logging
import os
import threading
import time
from pathlib import Path

from flask_socketio import SocketIO, emit, join_room, leave_room

logger = logging.getLogger(__name__)

# Track active streaming rooms
_log_watchers = set()
_log_thread = None
_log_thread_lock = threading.Lock()


def register_socket_events(socketio: SocketIO) -> None:
    """Register SocketIO event handlers."""

    @socketio.on("join_logs")
    def handle_join_logs():
        """Client wants live log streaming."""
        join_room("logs")
        _log_watchers.add("logs")
        _ensure_log_thread(socketio)
        emit("log_status", {"status": "connected"})

    @socketio.on("leave_logs")
    def handle_leave_logs():
        leave_room("logs")

    @socketio.on("join_call_audio")
    def handle_join_audio():
        """Client wants live call audio (future feature)."""
        join_room("call_audio")
        emit("audio_status", {"status": "connected", "message": "Audio streaming ready"})

    @socketio.on("leave_call_audio")
    def handle_leave_audio():
        leave_room("call_audio")


def _ensure_log_thread(socketio: SocketIO) -> None:
    """Start the log tailing thread if not already running."""
    global _log_thread
    with _log_thread_lock:
        if _log_thread is not None and _log_thread.is_alive():
            return
        _log_thread = threading.Thread(
            target=_tail_logs, args=(socketio,), daemon=True, name="log_streamer"
        )
        _log_thread.start()


def _tail_logs(socketio: SocketIO) -> None:
    """Tail the log file and emit new lines via SocketIO."""
    log_file = Path("/app/logs/claudephone.log")

    if not log_file.exists():
        return

    # Start from end of file
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if line:
                socketio.emit("log_line", {"line": line.rstrip()}, room="logs")
            else:
                time.sleep(0.5)
