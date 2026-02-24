"""Main dashboard routes (Blueprint) - status, health, callbacks, index page."""

import logging
from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html")


@main_bp.route("/<path:path>")
def catch_all(path):
    """Serve index.html for all non-API routes (client-side routing)."""
    return render_template("index.html")


@main_bp.route("/health")
def health():
    from .app import get_agent, get_callback_queue, get_db
    agent = get_agent()
    cq = get_callback_queue()
    db = get_db()

    status = {
        "status": "ok" if agent else "setup",
        "sip_registered": False,
        "in_call": False,
        "callbacks_pending": 0,
        "setup_complete": db.is_setup_complete() if db else False,
    }

    if agent and agent.account:
        status["sip_registered"] = agent.account.is_registered
        status["in_call"] = agent.account.current_call is not None

    if cq:
        status["callbacks_pending"] = cq.size()

    return jsonify(status)


@main_bp.route("/api/status")
def api_status():
    from .app import get_agent, get_callback_queue, get_db
    agent = get_agent()
    cq = get_callback_queue()
    db = get_db()

    status = {
        "setup_complete": db.is_setup_complete() if db else False,
        "agent_ready": agent is not None,
        "sip": {
            "registered": False,
            "in_call": False,
            "caller": None,
            "caller_name": None,
        },
        "components": {
            "tts": agent.tts is not None if agent else False,
            "stt": agent.stt is not None if agent else False,
            "vad": agent.vad_recorder is not None if agent else False,
            "ollama": agent.ollama is not None if agent else False,
            "router": agent.router is not None if agent else False,
        },
        "integrations": _get_integration_details(agent) if agent else [],
        "callbacks_pending": cq.size() if cq else 0,
    }

    if agent and agent.account:
        status["sip"]["registered"] = agent.account.is_registered
        call = agent.account.current_call
        if call:
            status["sip"]["in_call"] = True
            status["sip"]["caller"] = call.caller_number
            status["sip"]["caller_name"] = call.caller_name

    return jsonify(status)


@main_bp.route("/api/callbacks")
def api_callbacks():
    from .app import get_callback_queue
    cq = get_callback_queue()
    if cq:
        return jsonify(cq.list_all())
    return jsonify([])


@main_bp.route("/api/callbacks", methods=["POST"])
def api_add_callback():
    from .app import get_callback_queue
    cq = get_callback_queue()
    data = request.json
    if not data or "number" not in data or "message" not in data:
        return jsonify({"error": "number and message required"}), 400
    if cq:
        success = cq.add(data["number"], data["message"])
        return jsonify({"success": success})
    return jsonify({"error": "callback queue not available"}), 500


@main_bp.route("/api/callbacks/clear", methods=["POST"])
def api_clear_callbacks():
    from .app import get_callback_queue
    cq = get_callback_queue()
    if cq:
        count = cq.clear()
        return jsonify({"cleared": count})
    return jsonify({"error": "callback queue not available"}), 500


@main_bp.route("/api/setup/status")
def setup_status():
    """Check if initial setup has been completed."""
    from .app import get_db
    db = get_db()
    if db:
        return jsonify({"setup_complete": db.is_setup_complete()})
    return jsonify({"setup_complete": False})


@main_bp.route("/api/setup/complete", methods=["POST"])
def setup_complete():
    """Mark setup as complete and save initial configuration to DB."""
    from .app import get_db, signal_setup_complete
    db = get_db()
    if not db:
        return jsonify({"error": "Database not available"}), 503

    data = request.json or {}

    # Save setup values to DB AND .env
    if data.get("config"):
        from .api_config import _update_env_file
        for key, value in data["config"].items():
            if value:
                db.set_setting(key, value)
                _update_env_file(key, value)

    db.mark_setup_complete()
    signal_setup_complete()
    return jsonify({"success": True})


def _get_integration_details(agent) -> list:
    """Build detailed integration info for the status API.

    Uses PluginManager when available for plugin-based integrations,
    and adds built-in integrations (calendar, notes, media) separately.
    """
    if not agent:
        return []

    details = []

    # Plugin-provided integrations
    pm = getattr(agent, '_plugin_manager', None)
    if pm:
        details.extend(pm.get_integration_details(agent.config))

    # Built-in integrations (not managed by plugin system)
    builtin_defs = {
        "calendar": {"label": "Calendar (Agenda)", "config_keys": []},
        "notes": {"label": "Notes (Notities)", "config_keys": []},
    }

    for key, defn in builtin_defs.items():
        # Skip if already provided by a plugin
        if any(d["key"] == key for d in details):
            continue
        details.append({
            "key": key,
            "label": defn["label"],
            "active": key in agent.integrations,
            "configured": True,
            "config_keys": defn["config_keys"],
            "type": "builtin",
            "tab": "data",
        })

    return details
