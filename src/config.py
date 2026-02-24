"""Configuration management - loads settings from database with .env fallback."""

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ── Centralized paths ──────────────────────────────────────────────
# All file-system paths used across the project. Override via environment
# variables for development outside Docker.

APP_ROOT = Path(os.environ.get("APP_ROOT", "/app"))

PATHS = {
    "app_root":       APP_ROOT,
    "logs_dir":       APP_ROOT / "logs",
    "log_file":       APP_ROOT / "logs" / "claudephone.log",
    "pjsip_log":      APP_ROOT / "logs" / "pjsip.log",
    "data_dir":       APP_ROOT / "data",
    "db_file":        APP_ROOT / "data" / "claudephone.db",
    "audio_dir":      APP_ROOT / "audio",
    "audio_cache":    APP_ROOT / "audio" / "cache",
    "audio_tmp":      APP_ROOT / "audio" / "tmp",
    "audio_recordings": APP_ROOT / "audio" / "recordings",
    "models_dir":     APP_ROOT / "models",
    "piper_models":   APP_ROOT / "models" / "piper",
    "piper_bin":      APP_ROOT / "piper" / "piper",
    "hf_cache":       APP_ROOT / "models" / "hf_cache",
    "env_file":       APP_ROOT / ".env",
    "callback_queue": APP_ROOT / "logs" / "callback_queue.json",
}


def get_path(key: str) -> Path:
    """Get a centralized path by key. Raises KeyError if unknown."""
    return PATHS[key]

_config: Dict[str, Any] = {}
_config_lock = threading.Lock()

# Map of ENV_KEY -> (config_section, config_key, type, default)
# Single source of truth for all configuration keys.
CONFIG_KEYS = {
    # -- Dashboard --
    "DASHBOARD_PORT":          ("dashboard", "port", int, 8080),
    # -- Assistant --
    "ASSISTANT_NAME":          ("assistant", "name", str, "ClaudePhone"),
    "GREETING_NL":             ("assistant", "greeting_nl", str, ""),
    "GREETING_EN":             ("assistant", "greeting_en", str, ""),
    # -- Ollama AI --
    "OLLAMA_BASE_URL":         ("ollama", "base_url", str, "http://localhost:11434"),
    "OLLAMA_MAX_TOKENS":       ("ollama", "max_tokens", int, 600),
    "OLLAMA_MODEL":            ("ollama", "model", str, "glm-4.7-flash:latest"),
    "OLLAMA_TEMPERATURE":      ("ollama", "temperature", float, 0.7),
    "OLLAMA_TIMEOUT":          ("ollama", "timeout", int, 30),
    # -- SIP Configuration --
    "SIP_CALLBACK_NUMBER":     ("sip", "callback_number", str, ""),
    "SIP_GREETING_DELAY":      ("sip", "greeting_delay", float, 1.0),
    "SIP_LOCAL_PORT":          ("sip", "local_port", int, 5061),
    "SIP_MAX_CALL_DURATION":   ("sip", "max_call_duration", int, 1800),
    "SIP_PASSWORD":            ("sip", "password", str, ""),
    "SIP_POST_ANSWER_DELAY":   ("sip", "post_answer_delay", float, 0.3),
    "SIP_PROXY":               ("sip", "proxy", str, ""),
    "SIP_PBX_LAN_IP":          ("sip", "pbx_lan_ip", str, ""),
    "SIP_PUBLIC_IP":           ("sip", "public_ip", str, ""),
    "SIP_PUBLIC_PORT":         ("sip", "public_port", int, 5061),
    "SIP_REGISTRATION_TIMEOUT": ("sip", "registration_timeout", int, 60),
    "SIP_RING_SECONDS":        ("sip", "ring_seconds", int, 2),
    "SIP_SERVER":              ("sip", "server", str, ""),
    "SIP_TRANSPORT":           ("sip", "transport", str, "UDP"),
    "SIP_USERNAME":            ("sip", "username", str, ""),
    # -- Speech-to-Text --
    "STT_COMPUTE_TYPE":        ("stt", "compute_type", str, "auto"),
    "STT_DEVICE":              ("stt", "device", str, "cuda"),
    "STT_LISTEN_MAX_DURATION": ("stt", "listen_max_duration", int, 30),
    "STT_MODEL_SIZE":          ("stt", "model_size", str, "medium"),
    # -- Text-to-Speech --
    "TTS_LENGTH_SCALE":        ("tts", "length_scale", float, 1.0),
    "TTS_NOISE_SCALE":         ("tts", "noise_scale", float, 0.333),
    "TTS_NOISE_W":             ("tts", "noise_w", float, 0.333),
    "TTS_QUALITY_EN":          ("tts", "quality_en", str, "medium"),
    "TTS_QUALITY_NL":          ("tts", "quality_nl", str, "medium"),
    "TTS_VOICE_EN":            ("tts", "voice_en", str, "amy"),
    "TTS_VOICE_NL":            ("tts", "voice_nl", str, "nathalie"),
    "TTS_VOLUME_GAIN_DB":      ("tts", "volume_gain_db", float, 3.0),
    # -- Voice Activity Detection --
    "VAD_MIN_SILENCE_MS":      ("vad", "min_silence_ms", int, 800),
    "VAD_MIN_SPEECH_MS":       ("vad", "min_speech_ms", int, 250),
    "VAD_SPEECH_PAD_MS":       ("vad", "speech_pad_ms", int, 300),
    "VAD_THRESHOLD":           ("vad", "threshold", float, 0.4),
}

REQUIRED_KEYS = {"SIP_SERVER", "SIP_USERNAME", "SIP_PASSWORD"}


def _bool(value: str) -> bool:
    return value.lower() in ("true", "1", "yes", "on")


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _float(value: str, default: float) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _cast(raw: str, type_: type, default):
    """Cast a raw string value to the target type with fallback."""
    try:
        if type_ is int:
            return int(raw)
        elif type_ is float:
            return float(raw)
        elif type_ is bool:
            return raw.lower() in ("true", "1", "yes", "on")
        return str(raw)
    except (ValueError, TypeError):
        return default


def import_env_to_db(db) -> int:
    """Import environment variables into DB settings (only if key not already in DB).

    Returns the number of values imported.
    """
    imported = 0
    for env_key in CONFIG_KEYS:
        env_val = os.getenv(env_key, "")
        if env_val and db.get_setting(env_key) is None:
            db.set_setting(env_key, env_val)
            imported += 1
    return imported


def load_config_from_db(db) -> Dict[str, Any]:
    """Load configuration from database settings.

    Priority: DB > env var > default.
    """
    config: Dict[str, Any] = {}
    for env_key, (section, key, type_, default) in CONFIG_KEYS.items():
        db_val = db.get_setting(env_key) if db else None
        env_val = os.getenv(env_key, "")
        raw = db_val if db_val is not None else (env_val if env_val else None)

        if raw is not None:
            value = _cast(raw, type_, default)
        else:
            value = default

        if section not in config:
            config[section] = {}
        config[section][key] = value

    # Apply transport uppercasing
    if "sip" in config:
        config["sip"]["transport"] = config["sip"].get("transport", "UDP").upper()

    return config


def check_required_settings(db) -> bool:
    """Check if all required settings are present in the database."""
    for key in REQUIRED_KEYS:
        val = db.get_setting(key)
        if not val:
            return False
    return True


def load_config() -> Dict[str, Any]:
    """Load configuration from environment variables (legacy)."""
    return {
        "sip": {
            "server": os.getenv("SIP_SERVER", ""),
            "username": os.getenv("SIP_USERNAME", ""),
            "password": os.getenv("SIP_PASSWORD", ""),
            "proxy": os.getenv("SIP_PROXY", ""),
            "transport": os.getenv("SIP_TRANSPORT", "UDP").upper(),
            "public_ip": os.getenv("SIP_PUBLIC_IP", ""),
            "public_port": _int(os.getenv("SIP_PUBLIC_PORT", "5061"), 5061),
            "local_port": _int(os.getenv("SIP_LOCAL_PORT", "5061"), 5061),
            "ring_seconds": _int(os.getenv("SIP_RING_SECONDS", "2"), 2),
            "post_answer_delay": _float(os.getenv("SIP_POST_ANSWER_DELAY", "0.3"), 0.3),
            "greeting_delay": _float(os.getenv("SIP_GREETING_DELAY", "1.0"), 1.0),
            "max_call_duration": _int(os.getenv("SIP_MAX_CALL_DURATION", "1800"), 1800),
            "callback_number": os.getenv("SIP_CALLBACK_NUMBER", ""),
            "pbx_lan_ip": os.getenv("SIP_PBX_LAN_IP", ""),
            "registration_timeout": _int(os.getenv("SIP_REGISTRATION_TIMEOUT", "60"), 60),
        },
        "ollama": {
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            "model": os.getenv("OLLAMA_MODEL", "glm-4.7-flash:latest"),
            "temperature": _float(os.getenv("OLLAMA_TEMPERATURE", "0.7"), 0.7),
            "max_tokens": _int(os.getenv("OLLAMA_MAX_TOKENS", "600"), 600),
            "timeout": _int(os.getenv("OLLAMA_TIMEOUT", "30"), 30),
        },
        "stt": {
            "model_size": os.getenv("STT_MODEL_SIZE", "medium"),
            "device": os.getenv("STT_DEVICE", "cuda"),
            "compute_type": os.getenv("STT_COMPUTE_TYPE", "auto"),
            "listen_max_duration": _int(os.getenv("STT_LISTEN_MAX_DURATION", "30"), 30),
        },
        "tts": {
            "voice_nl": os.getenv("TTS_VOICE_NL", "nathalie"),
            "voice_en": os.getenv("TTS_VOICE_EN", "amy"),
            "quality_nl": os.getenv("TTS_QUALITY_NL", "medium"),
            "quality_en": os.getenv("TTS_QUALITY_EN", "medium"),
            "volume_gain_db": _float(os.getenv("TTS_VOLUME_GAIN_DB", "3"), 3.0),
            "length_scale": _float(os.getenv("TTS_LENGTH_SCALE", "1.0"), 1.0),
            "noise_scale": _float(os.getenv("TTS_NOISE_SCALE", "0.333"), 0.333),
            "noise_w": _float(os.getenv("TTS_NOISE_W", "0.333"), 0.333),
        },
        "vad": {
            "threshold": _float(os.getenv("VAD_THRESHOLD", "0.4"), 0.4),
            "min_silence_ms": _int(os.getenv("VAD_MIN_SILENCE_MS", "800"), 800),
            "speech_pad_ms": _int(os.getenv("VAD_SPEECH_PAD_MS", "300"), 300),
            "min_speech_ms": _int(os.getenv("VAD_MIN_SPEECH_MS", "250"), 250),
        },
        "dashboard": {
            "port": _int(os.getenv("DASHBOARD_PORT", "8080"), 8080),
        },
        "assistant": {
            "name": os.getenv("ASSISTANT_NAME", "ClaudePhone"),
            "greeting_nl": os.getenv("GREETING_NL", ""),
            "greeting_en": os.getenv("GREETING_EN", ""),
        },
    }


def get_config() -> Dict[str, Any]:
    """Get the singleton configuration dict."""
    global _config
    with _config_lock:
        if not _config:
            _config = load_config()
        return _config


def set_config(config: Dict[str, Any]) -> None:
    """Set the global config dict (used after DB-based loading)."""
    global _config
    with _config_lock:
        _config = config


def reload_config(env_path: str = None, db=None) -> Dict[str, Any]:
    """Reload configuration from DB (preferred) or .env file."""
    global _config
    if env_path and Path(env_path).exists():
        load_dotenv(env_path, override=True)
    with _config_lock:
        if db:
            _config = load_config_from_db(db)
        else:
            _config = load_config()
        return _config


def validate_config(config: Dict[str, Any]) -> list:
    """Validate configuration. Returns list of error messages."""
    errors = []
    sip = config.get("sip", {})
    if not sip.get("server"):
        errors.append("SIP_SERVER is required")
    if not sip.get("username"):
        errors.append("SIP_USERNAME is required")
    if not sip.get("password"):
        errors.append("SIP_PASSWORD is required")

    return errors
