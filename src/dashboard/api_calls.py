"""Calls API - call history, recordings, and test calls."""

import logging
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger(__name__)

calls_bp = Blueprint("calls", __name__)


@calls_bp.route("/history")
def get_call_history():
    """Get call history."""
    from .app import get_call_logger

    cl = get_call_logger()
    if not cl:
        return jsonify([])

    days = request.args.get("days", 7, type=int)
    limit = request.args.get("limit", 50, type=int)
    return jsonify(cl.get_history(days=days, limit=limit))


@calls_bp.route("/<call_id>")
def get_call_detail(call_id):
    """Get details for a specific call."""
    from .app import get_call_logger

    cl = get_call_logger()
    if not cl:
        return jsonify({"error": "Call logger not available"}), 503

    call = cl.get_call(call_id)
    if not call:
        return jsonify({"error": "Call not found"}), 404

    return jsonify(call)


@calls_bp.route("/<call_id>/recording")
def get_call_recording(call_id):
    """Serve call recording audio file."""
    from .app import get_call_logger

    cl = get_call_logger()
    if not cl:
        return jsonify({"error": "Call logger not available"}), 503

    recording_path = cl.get_recording_path(call_id)
    if not recording_path:
        return jsonify({"error": "Recording not found"}), 404

    return send_file(recording_path, mimetype="audio/wav")


@calls_bp.route("/outgoing", methods=["POST"])
def make_test_call():
    """Trigger an outgoing test call."""
    from .app import get_agent

    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not available"}), 503

    if not agent.account or not agent.account.is_registered:
        return jsonify({"error": "SIP not registered"}), 503

    if agent.account.current_call is not None:
        return jsonify({"error": "Already in a call"}), 409

    data = request.json or {}
    number = data.get("number", "")
    message = data.get("message", "Dit is een testgesprek van ClaudeViool.")

    if not number:
        return jsonify({"error": "number parameter required"}), 400

    # Make call in background thread
    def do_call():
        try:
            import pjsua2 as pj
            pj.Endpoint.instance().libRegisterThread("test_call")
        except Exception:
            pass

        # Pre-generate TTS
        audio_file = None
        if agent.tts:
            audio_dir = Path("/app/audio/tmp")
            audio_dir.mkdir(parents=True, exist_ok=True)
            import time
            out = str(audio_dir / f"testcall_{int(time.time()*1000)}.wav")
            audio_file = agent.tts.speak(message, out)

        agent._make_outgoing_call(number, message, audio_file)

    t = threading.Thread(target=do_call, daemon=True, name="test_call")
    t.start()

    return jsonify({
        "success": True,
        "number": number,
        "message": message,
        "status": "Call initiated",
    })
