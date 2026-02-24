"""Data API - notes, calendar events, and media control (built-in integrations)."""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

data_bp = Blueprint("data", __name__)


def _get_db():
    """Get the shared Database instance from the agent."""
    from .app import get_agent
    agent = get_agent()
    if not agent:
        return None
    return getattr(agent, '_db', None)


# ==================== Notes ====================

@data_bp.route("/notes")
def get_notes():
    """List all active notes."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    include_completed = request.args.get("all", "false").lower() == "true"
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.get_notes(include_completed=include_completed, limit=limit))


@data_bp.route("/notes", methods=["POST"])
def add_note():
    """Add a new note."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    data = request.json
    if not data or not data.get("content"):
        return jsonify({"error": "content required"}), 400
    note_id = db.add_note(data["content"])
    return jsonify({"success": True, "id": note_id})


@data_bp.route("/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    """Delete a note."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    success = db.delete_note(note_id)
    return jsonify({"success": success})


@data_bp.route("/notes/<int:note_id>/complete", methods=["POST"])
def complete_note(note_id):
    """Mark a note as completed."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    success = db.complete_note(note_id)
    return jsonify({"success": success})


# ==================== Calendar ====================

@data_bp.route("/events")
def get_events():
    """List upcoming events."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    date_filter = request.args.get("date")
    limit = request.args.get("limit", 30, type=int)
    return jsonify(db.get_events(event_date=date_filter, limit=limit))


@data_bp.route("/events", methods=["POST"])
def add_event():
    """Add a new calendar event."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    data = request.json
    if not data or not data.get("title") or not data.get("date"):
        return jsonify({"error": "title and date required"}), 400
    event_id = db.add_event(
        title=data["title"],
        event_date=data["date"],
        event_time=data.get("time"),
        description=data.get("description"),
    )
    return jsonify({"success": True, "id": event_id})


@data_bp.route("/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    """Delete a calendar event."""
    db = _get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503
    success = db.delete_event(event_id)
    return jsonify({"success": success})


# ==================== Media ====================

def _get_media_handler():
    """Get the media integration handler from the agent."""
    from .app import get_agent
    agent = get_agent()
    if not agent:
        return None
    return agent.integrations.get("media")


@data_bp.route("/media/status")
def media_status():
    """Get media player status."""
    handler = _get_media_handler()
    if not handler:
        return jsonify({"available": False, "reason": "Media integration not active (requires Home Assistant plugin)"})
    player = handler._get_player()
    return jsonify({
        "available": True,
        "player": player,
    })


@data_bp.route("/media/command", methods=["POST"])
def media_command():
    """Send a media control command."""
    handler = _get_media_handler()
    if not handler:
        return jsonify({"error": "Media integration not active"}), 503

    data = request.json or {}
    command = data.get("command", "")
    if command not in ("play", "stop", "pause", "volume_up", "volume_down", "next", "previous"):
        return jsonify({"error": "Invalid command"}), 400

    player = handler._get_player()
    if not player:
        return jsonify({"error": "No media player found in Home Assistant"}), 404

    service_map = {
        "play": "media_play",
        "stop": "media_stop",
        "pause": "media_pause",
        "volume_up": "volume_up",
        "volume_down": "volume_down",
        "next": "media_next_track",
        "previous": "media_previous_track",
    }
    try:
        handler.ha._call_service("media_player", service_map[command], player)
        return jsonify({"success": True, "command": command, "player": player})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
