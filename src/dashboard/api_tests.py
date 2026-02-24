"""Component test API - TTS, STT, and Ollama tests from the dashboard."""

import logging
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger(__name__)

tests_bp = Blueprint("tests", __name__)


@tests_bp.route("/tts", methods=["POST"])
def test_tts():
    """Test TTS: text -> audio file."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.tts:
        return jsonify({"error": "TTS not available"}), 503

    data = request.json or {}
    text = data.get("text", "Dit is een test van het spraaksysteem.")
    language = data.get("language", "nl")

    audio_dir = Path("/app/audio/tmp")
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_file = str(audio_dir / f"test_tts_{int(time.time()*1000)}.wav")

    start = time.time()
    audio_file = agent.tts.speak(text, output_file, language=language)
    elapsed = time.time() - start

    if not audio_file:
        return jsonify({"error": "TTS synthesis failed"}), 500

    file_size = Path(audio_file).stat().st_size

    return jsonify({
        "success": True,
        "text": text,
        "language": language,
        "audio_url": f"/api/test/tts/audio?file={Path(audio_file).name}",
        "file_size": file_size,
        "duration_ms": round(elapsed * 1000),
    })


@tests_bp.route("/tts/audio")
def serve_tts_audio():
    """Serve a test TTS audio file."""
    filename = request.args.get("file", "")
    if not filename:
        return jsonify({"error": "file parameter required"}), 400

    # Security: only serve from tmp directory
    audio_path = Path("/app/audio/tmp") / Path(filename).name
    if not audio_path.exists():
        # Try cache directory
        audio_path = Path("/app/audio/cache") / Path(filename).name

    if not audio_path.exists():
        return jsonify({"error": "File not found"}), 404

    return send_file(str(audio_path), mimetype="audio/wav")


@tests_bp.route("/stt", methods=["POST"])
def test_stt():
    """Test STT: audio file -> text."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.stt:
        return jsonify({"error": "STT not available"}), 503

    # Accept file upload or use a test file
    if "audio" in request.files:
        audio = request.files["audio"]
        audio_path = Path("/app/audio/tmp") / f"test_stt_{int(time.time()*1000)}.wav"
        audio.save(str(audio_path))
    else:
        return jsonify({"error": "Audio file required (upload as 'audio')"}), 400

    start = time.time()
    text, language = agent.stt.transcribe(str(audio_path))
    elapsed = time.time() - start

    # Cleanup
    audio_path.unlink(missing_ok=True)

    return jsonify({
        "success": text is not None,
        "text": text,
        "language": language,
        "duration_ms": round(elapsed * 1000),
    })


@tests_bp.route("/ollama", methods=["POST"])
def test_ollama():
    """Test Ollama: prompt -> response."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.ollama:
        return jsonify({"error": "Ollama not available"}), 503

    data = request.json or {}
    prompt = data.get("prompt", "Zeg 'hallo' in het Nederlands.")
    system = data.get("system", "")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    response = agent.ollama.chat_sync(messages, timeout=30)
    elapsed = time.time() - start

    return jsonify({
        "success": response is not None,
        "prompt": prompt,
        "response": response,
        "model": agent.ollama.model,
        "duration_ms": round(elapsed * 1000),
    })
