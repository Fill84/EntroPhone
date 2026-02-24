"""Configuration management - loads settings from .env file."""

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_config: Dict[str, Any] = {}
_config_lock = threading.Lock()


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


def load_config() -> Dict[str, Any]:
    """Load configuration from environment variables."""
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
            "name": os.getenv("ASSISTANT_NAME", "ClaudeViool"),
        },
    }


def get_config() -> Dict[str, Any]:
    """Get the singleton configuration dict."""
    global _config
    with _config_lock:
        if not _config:
            _config = load_config()
        return _config


def reload_config(env_path: str = None) -> Dict[str, Any]:
    """Reload configuration from .env file."""
    global _config
    if env_path and Path(env_path).exists():
        load_dotenv(env_path, override=True)
    with _config_lock:
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
