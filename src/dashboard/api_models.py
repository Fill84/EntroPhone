"""Model switching API - switch Ollama models and Piper TTS voices."""

import logging
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

models_bp = Blueprint("models", __name__)


@models_bp.route("/ollama")
def get_ollama_models():
    """List available Ollama models."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.ollama:
        return jsonify({"error": "Ollama not available"}), 503

    try:
        r = requests.get(f"{agent.ollama.base_url}/api/tags", timeout=10)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return jsonify({
                "current": agent.ollama.model,
                "models": [
                    {
                        "name": m.get("name", ""),
                        "size": m.get("size", 0),
                        "modified": m.get("modified_at", ""),
                    }
                    for m in models
                ],
            })
        return jsonify({"error": f"Ollama returned {r.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@models_bp.route("/ollama", methods=["PUT"])
def switch_ollama_model():
    """Switch the active Ollama model."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.ollama:
        return jsonify({"error": "Ollama not available"}), 503

    data = request.json or {}
    model = data.get("model", "")
    if not model:
        return jsonify({"error": "model parameter required"}), 400

    old_model = agent.ollama.model
    agent.ollama.model = model
    logger.info("Ollama model switched: %s -> %s", old_model, model)

    return jsonify({
        "success": True,
        "old_model": old_model,
        "new_model": model,
    })


@models_bp.route("/ollama/pull", methods=["POST"])
def pull_ollama_model():
    """Pull (download) an Ollama model."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.ollama:
        return jsonify({"error": "Ollama not available"}), 503

    data = request.json or {}
    model = data.get("model", "")
    if not model:
        return jsonify({"error": "model parameter required"}), 400

    try:
        r = requests.post(
            f"{agent.ollama.base_url}/api/pull",
            json={"name": model, "stream": False},
            timeout=600,
        )
        if r.status_code == 200:
            logger.info("Ollama model pulled: %s", model)
            return jsonify({"success": True, "model": model})
        return jsonify({"error": f"Pull failed: {r.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@models_bp.route("/ollama/delete", methods=["POST"])
def delete_ollama_model():
    """Delete an Ollama model."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.ollama:
        return jsonify({"error": "Ollama not available"}), 503

    data = request.json or {}
    model = data.get("model", "")
    if not model:
        return jsonify({"error": "model parameter required"}), 400

    if model == agent.ollama.model:
        return jsonify({"error": "Cannot delete the currently active model"}), 400

    try:
        r = requests.delete(
            f"{agent.ollama.base_url}/api/delete",
            json={"name": model},
            timeout=30,
        )
        if r.status_code == 200:
            logger.info("Ollama model deleted: %s", model)
            return jsonify({"success": True, "model": model})
        return jsonify({"error": f"Delete failed: {r.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@models_bp.route("/tts")
def get_tts_voices():
    """List available Piper TTS voices."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.tts:
        return jsonify({"error": "TTS not available"}), 503

    models_dir = Path("/app/models/piper")
    voices = []
    if models_dir.exists():
        for f in sorted(models_dir.glob("*.onnx")):
            if not str(f).endswith(".json"):
                # Parse voice name from filename: locale-voice-quality.onnx
                parts = f.stem.split("-")
                voices.append({
                    "file": f.name,
                    "locale": parts[0] if parts else "",
                    "voice": parts[1] if len(parts) > 1 else "",
                    "quality": parts[2] if len(parts) > 2 else "",
                })

    current = {}
    for lang, model_path in agent.tts._model_paths.items():
        current[lang] = Path(model_path).stem

    return jsonify({
        "current": current,
        "voices": voices,
    })


@models_bp.route("/tts", methods=["PUT"])
def switch_tts_voice():
    """Switch the TTS voice for a language. Needs container restart to take full effect."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.tts:
        return jsonify({"error": "TTS not available"}), 503

    data = request.json or {}
    language = data.get("language", "nl")
    voice_file = data.get("voice_file", "")

    if not voice_file:
        return jsonify({"error": "voice_file parameter required"}), 400

    model_path = Path("/app/models/piper") / voice_file
    if not model_path.exists():
        return jsonify({"error": f"Voice file not found: {voice_file}"}), 404

    old = agent.tts._model_paths.get(language, "none")
    agent.tts._model_paths[language] = str(model_path)

    # Clear TTS cache so new voice is used
    agent.tts._cache.clear()

    logger.info("TTS voice [%s] switched: %s -> %s", language, old, voice_file)

    return jsonify({
        "success": True,
        "language": language,
        "old_voice": Path(old).stem if old != "none" else "none",
        "new_voice": model_path.stem,
        "note": "Cache cleared. Pre-generated phrases will use new voice on next playback.",
    })
