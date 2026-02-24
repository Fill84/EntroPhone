"""Config editor API - read/write settings with DB as primary and .env as backup."""

import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

config_bp = Blueprint("config", __name__)

from ..config import get_path

ENV_FILE = str(get_path("env_file"))

# Settings that can be hot-reloaded (no restart needed)
HOT_RELOAD_KEYS = {
    "OLLAMA_TEMPERATURE", "OLLAMA_MAX_TOKENS", "OLLAMA_TIMEOUT",
    "TTS_VOLUME_GAIN_DB", "TTS_LENGTH_SCALE", "TTS_NOISE_SCALE", "TTS_NOISE_W",
    "VAD_THRESHOLD", "VAD_MIN_SILENCE_MS", "VAD_SPEECH_PAD_MS", "VAD_MIN_SPEECH_MS",
    "SIP_GREETING_DELAY", "ASSISTANT_NAME",
    "GREETING_NL", "GREETING_EN", "SIP_PBX_LAN_IP",
}

# Settings that need container restart
RESTART_KEYS = {
    "SIP_SERVER", "SIP_USERNAME", "SIP_PASSWORD", "SIP_PROXY",
    "SIP_TRANSPORT", "SIP_PUBLIC_IP", "SIP_PUBLIC_PORT", "SIP_LOCAL_PORT",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "STT_MODEL_SIZE", "STT_DEVICE", "STT_COMPUTE_TYPE",
    "TTS_VOICE_NL", "TTS_VOICE_EN", "TTS_QUALITY_NL", "TTS_QUALITY_EN",
}

# Sensitive keys (masked in output)
SENSITIVE_KEYS = {"SIP_PASSWORD"}

# Human-friendly descriptions shown as tooltips/placeholders
KEY_DESCRIPTIONS = {
    "GREETING_NL": "Dutch greeting template. Variables: {caller_name}, {assistant_name}, {plugins}",
    "GREETING_EN": "English greeting template. Variables: {caller_name}, {assistant_name}, {plugins}",
    "SIP_PBX_LAN_IP": "PBX LAN IP for NAT punch-through (fixes first-call audio issue)",
    "ASSISTANT_NAME": "Name the assistant introduces itself with",
}

# Only keys with these prefixes are shown in the Config tab.
# Everything else (plugin keys, etc.) is managed elsewhere.
CORE_PREFIXES = (
    "SIP_", "OLLAMA_", "STT_", "TTS_",
    "VAD_", "DASHBOARD_", "ASSISTANT_", "GREETING_",
)


@config_bp.route("/")
def get_all_config():
    """Get all core configuration variables from DB with .env fallback.

    Only shows keys matching CORE_PREFIXES. Plugin config is managed
    entirely by the plugins themselves via the Plugins tab.
    """
    from .app import get_db
    from ..config import CONFIG_KEYS

    db = get_db()
    db_settings = db.get_all_settings() if db else {}
    env_vars = _read_env_file()

    result = []
    for key, (section, cfg_key, type_, default) in CONFIG_KEYS.items():
        if not key.startswith(CORE_PREFIXES):
            continue

        # DB takes priority over .env
        value = db_settings.get(key) or env_vars.get(key, "")
        is_sensitive = key in SENSITIVE_KEYS
        default_str = str(default) if default != "" else ""

        result.append({
            "key": key,
            "value": "***" if is_sensitive else value,
            "default": default_str,
            "hot_reload": key in HOT_RELOAD_KEYS,
            "needs_restart": key in RESTART_KEYS,
            "sensitive": is_sensitive,
            "source": "db" if key in db_settings else ("env" if key in env_vars else "default"),
            "group": "ASSISTANT" if key.startswith("GREETING_") else key.split("_", 1)[0],
            "description": KEY_DESCRIPTIONS.get(key, ""),
        })
    return jsonify(result)


@config_bp.route("/", methods=["PUT"])
def update_config():
    """Update a configuration variable in DB and .env."""
    from .app import get_db

    data = request.json
    if not data or "key" not in data or "value" not in data:
        return jsonify({"error": "key and value required"}), 400

    key = data["key"]
    value = data["value"]

    # Write to DB (primary)
    db = get_db()
    if db:
        db.set_setting(key, value)

    # Write to .env file (secondary, backward compat)
    _update_env_file(key, value)

    # Hot reload if possible
    needs_restart = key in RESTART_KEYS
    if key in HOT_RELOAD_KEYS:
        _apply_hot_reload(key, value)

    return jsonify({
        "success": True,
        "key": key,
        "hot_reloaded": key in HOT_RELOAD_KEYS,
        "needs_restart": needs_restart,
    })


@config_bp.route("/bulk", methods=["PUT"])
def update_config_bulk():
    """Save all configuration variables at once."""
    from .app import get_db

    data = request.json
    if not data or "items" not in data:
        return jsonify({"error": "items required"}), 400

    db = get_db()
    needs_restart = False
    hot_reloaded = []

    for item in data["items"]:
        key = item.get("key")
        value = item.get("value", "")
        if not key:
            continue

        if db:
            db.set_setting(key, value)
        _update_env_file(key, value)

        if key in RESTART_KEYS:
            needs_restart = True
        if key in HOT_RELOAD_KEYS:
            _apply_hot_reload(key, value)
            hot_reloaded.append(key)

    return jsonify({
        "success": True,
        "saved": len(data["items"]),
        "needs_restart": needs_restart,
        "hot_reloaded": hot_reloaded,
    })


@config_bp.route("/reload", methods=["POST"])
def reload_config():
    """Reload configuration from database (and .env)."""
    from .app import get_db
    from ..config import reload_config as do_reload

    db = get_db()
    config = do_reload(ENV_FILE, db=db)
    _apply_all_hot_reload(config)
    return jsonify({"success": True, "message": "Configuration reloaded"})


def _read_env_file() -> dict:
    """Read all key-value pairs from .env file."""
    env_vars = {}
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        return env_vars

    content = env_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Handle multi-line values (JSON)
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                env_vars[key] = value
    return env_vars


def _update_env_file(key: str, value: str) -> None:
    """Update a single key in the .env file."""
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    found = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "=" in stripped:
            line_key = stripped.split("=", 1)[0].strip()
            if line_key == key:
                lines[i] = f"{key}={value}"
                found = True
                break

    if not found:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Also update os.environ for immediate effect
    os.environ[key] = value


def _apply_hot_reload(key: str, value: str) -> None:
    """Apply a single setting change at runtime."""
    from .app import get_agent

    agent = get_agent()
    if not agent:
        return

    # Ollama settings
    if key == "OLLAMA_TEMPERATURE" and agent.ollama:
        agent.ollama.temperature = float(value)
    elif key == "OLLAMA_MAX_TOKENS" and agent.ollama:
        agent.ollama.max_tokens = int(value)
    elif key == "OLLAMA_TIMEOUT" and agent.ollama:
        agent.ollama.timeout = int(value)

    # TTS settings
    elif key == "TTS_VOLUME_GAIN_DB" and agent.tts:
        agent.tts.volume_gain_db = float(value)
    elif key == "TTS_LENGTH_SCALE" and agent.tts:
        agent.tts.length_scale = float(value)
    elif key == "TTS_NOISE_SCALE" and agent.tts:
        agent.tts.noise_scale = float(value)
    elif key == "TTS_NOISE_W" and agent.tts:
        agent.tts.noise_w = float(value)

    # VAD settings
    elif key == "VAD_THRESHOLD" and agent.vad_recorder:
        agent.vad_recorder.vad.threshold = float(value)
    elif key == "VAD_MIN_SILENCE_MS" and agent.vad_recorder:
        agent.vad_recorder.config["min_silence_ms"] = int(value)
    elif key == "VAD_SPEECH_PAD_MS" and agent.vad_recorder:
        agent.vad_recorder.config["speech_pad_ms"] = int(value)
    elif key == "VAD_MIN_SPEECH_MS" and agent.vad_recorder:
        agent.vad_recorder.config["min_speech_ms"] = int(value)

    # Assistant settings
    elif key == "ASSISTANT_NAME":
        agent.config["assistant"]["name"] = value
    elif key == "GREETING_NL":
        agent.config["assistant"]["greeting_nl"] = value
    elif key == "GREETING_EN":
        agent.config["assistant"]["greeting_en"] = value
    elif key == "SIP_GREETING_DELAY":
        agent.config["sip"]["greeting_delay"] = float(value)
    elif key == "SIP_PBX_LAN_IP":
        agent.config["sip"]["pbx_lan_ip"] = value

    logger.info("Hot-reloaded: %s = %s", key, value)


def _apply_all_hot_reload(config: dict) -> None:
    """Apply all hot-reloadable settings from a full config dict."""
    from .app import get_agent

    agent = get_agent()
    if not agent:
        return

    # Update agent's config reference
    agent.config = config

    # Update Ollama
    if agent.ollama:
        ollama = config.get("ollama", {})
        agent.ollama.temperature = ollama.get("temperature", 0.5)
        agent.ollama.max_tokens = ollama.get("max_tokens", 600)
        agent.ollama.timeout = ollama.get("timeout", 30)

    # Update TTS
    if agent.tts:
        tts = config.get("tts", {})
        agent.tts.volume_gain_db = tts.get("volume_gain_db", 1.5)
        agent.tts.length_scale = tts.get("length_scale", 1.1)
        agent.tts.noise_scale = tts.get("noise_scale", 0.333)
        agent.tts.noise_w = tts.get("noise_w", 0.333)

    logger.info("All hot-reloadable settings applied")
