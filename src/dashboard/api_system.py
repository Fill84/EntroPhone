"""System monitoring API - resources, logs, SIP control, cache management."""

import logging
import os
import shutil
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

system_bp = Blueprint("system", __name__)


@system_bp.route("/resources")
def get_resources():
    """Get system resource usage (CPU, memory, disk, GPU)."""
    try:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        disk = shutil.disk_usage("/app")

        result = {
            "cpu": {
                "percent": cpu_percent,
                "count": psutil.cpu_count(),
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 1),
                "used_gb": round(memory.used / (1024**3), 1),
                "percent": memory.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "percent": round(disk.used / disk.total * 100, 1),
            },
            "gpu": _get_gpu_info(),
        }
        return jsonify(result)

    except ImportError:
        return jsonify({"error": "psutil not installed"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@system_bp.route("/logs")
def get_logs():
    """Get recent log lines."""
    log_file = Path("/app/logs/claudephone.log")
    lines = request.args.get("lines", 100, type=int)
    search = request.args.get("search", "")

    if not log_file.exists():
        return jsonify({"lines": [], "total": 0})

    try:
        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()

        if search:
            all_lines = [l for l in all_lines if search.lower() in l.lower()]

        recent = all_lines[-lines:]
        return jsonify({
            "lines": recent,
            "total": len(all_lines),
            "showing": len(recent),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@system_bp.route("/sip/reregister", methods=["POST"])
def sip_reregister():
    """Trigger SIP re-registration."""
    from .app import get_agent

    agent = get_agent()
    if not agent or not agent.account:
        return jsonify({"error": "SIP agent not available"}), 503

    try:
        agent.account.setRegistration(True)
        return jsonify({"success": True, "message": "Re-registration triggered"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@system_bp.route("/cache")
def get_cache_info():
    """Get TTS cache information."""
    cache_dir = Path("/app/audio/cache")
    tmp_dir = Path("/app/audio/tmp")

    cache_files = list(cache_dir.glob("*.wav")) if cache_dir.exists() else []
    tmp_files = list(tmp_dir.glob("*")) if tmp_dir.exists() else []

    cache_size = sum(f.stat().st_size for f in cache_files)
    tmp_size = sum(f.stat().st_size for f in tmp_files)

    return jsonify({
        "cache": {
            "files": len(cache_files),
            "size_mb": round(cache_size / (1024 * 1024), 2),
            "path": str(cache_dir),
        },
        "tmp": {
            "files": len(tmp_files),
            "size_mb": round(tmp_size / (1024 * 1024), 2),
            "path": str(tmp_dir),
        },
    })


@system_bp.route("/cache/clear", methods=["POST"])
def clear_cache():
    """Clear TTS cache and/or tmp files."""
    from .app import get_agent

    data = request.json or {}
    clear_cache = data.get("cache", True)
    clear_tmp = data.get("tmp", True)

    cleared = {"cache": 0, "tmp": 0}

    if clear_cache:
        cache_dir = Path("/app/audio/cache")
        if cache_dir.exists():
            for f in cache_dir.glob("*.wav"):
                try:
                    f.unlink()
                    cleared["cache"] += 1
                except Exception:
                    pass

        # Reset TTS cache dict
        agent = get_agent()
        if agent and agent.tts:
            agent.tts._cache.clear()

    if clear_tmp:
        tmp_dir = Path("/app/audio/tmp")
        if tmp_dir.exists():
            for f in tmp_dir.glob("*"):
                try:
                    f.unlink()
                    cleared["tmp"] += 1
                except Exception:
                    pass

    return jsonify({"success": True, "cleared": cleared})


def _get_gpu_info() -> dict:
    """Get GPU info via nvidia-smi if available."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.decode().strip().split(", ")
            if len(parts) >= 5:
                return {
                    "name": parts[0],
                    "memory_used_mb": int(parts[1]),
                    "memory_total_mb": int(parts[2]),
                    "utilization_percent": int(parts[3]),
                    "temperature_c": int(parts[4]),
                }
    except Exception:
        pass
    return {"available": False}
