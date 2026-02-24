"""Tests for configuration management."""

import os
import pytest


def test_load_config_defaults():
    """Test config loads with default values when env is empty."""
    # Clear SIP env vars for test
    for key in list(os.environ.keys()):
        if key.startswith(("SIP_", "OLLAMA_", "HA_", "MONITORING_", "STT_", "TTS_", "VAD_")):
            del os.environ[key]

    from src.config import load_config
    config = load_config()

    assert config["sip"]["transport"] == "UDP"
    assert config["sip"]["local_port"] == 5061
    assert config["sip"]["ring_seconds"] == 2
    assert config["ollama"]["temperature"] == 0.7
    assert config["stt"]["model_size"] == "medium"
    assert config["stt"]["device"] == "cuda"
    assert config["vad"]["threshold"] == 0.4
    assert config["vad"]["min_silence_ms"] == 800
    assert config["tts"]["voice_nl"] == "nathalie"
    assert config["tts"]["voice_en"] == "amy"


def test_validate_config_missing_sip():
    """Test validation catches missing SIP credentials."""
    from src.config import validate_config

    config = {"sip": {"server": "", "username": "", "password": ""}, "homeassistant": {"enabled": False}}
    errors = validate_config(config)
    assert len(errors) >= 3
    assert any("SIP_SERVER" in e for e in errors)
    assert any("SIP_USERNAME" in e for e in errors)
    assert any("SIP_PASSWORD" in e for e in errors)


def test_validate_config_valid():
    """Test validation passes with valid config."""
    from src.config import validate_config

    config = {
        "sip": {"server": "sip.test.com", "username": "user", "password": "pass"},
    }
    errors = validate_config(config)
    assert len(errors) == 0


def test_get_path():
    """Test centralized path helper."""
    from src.config import get_path
    import pytest

    path = get_path("db_file")
    assert "claudephone.db" in str(path)

    with pytest.raises(KeyError):
        get_path("nonexistent_key")
