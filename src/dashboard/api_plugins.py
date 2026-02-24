"""Plugins management API - list, enable, disable, configure, test, install."""

import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

plugins_bp = Blueprint("plugins", __name__)


def _get_pm():
    """Get PluginManager from agent."""
    from .app import get_agent
    agent = get_agent()
    if agent:
        return getattr(agent, '_plugin_manager', None)
    return None


@plugins_bp.route("/")
def list_plugins():
    """List all installed plugins with status and config fields."""
    pm = _get_pm()
    if not pm:
        return jsonify([])

    result = []
    for name, plugin in pm.plugins.items():
        meta = plugin.meta
        fields = []
        for f in plugin.config_schema:
            current = pm.context.get_env(f.key) if pm.context else ""
            fields.append({
                "key": f.key,
                "label": f.label,
                "field_type": f.field_type,
                "required": f.required,
                "placeholder": f.placeholder,
                "sensitive": f.sensitive,
                "options": f.options,
                "default": f.default,
                "hot_reload": f.hot_reload,
                "current_value": "" if f.sensitive else current,
            })
        result.append({
            "name": meta.name,
            "display_name": meta.display_name,
            "description": meta.description,
            "version": meta.version,
            "author": meta.author,
            "enabled": pm.is_enabled(name),
            "configured": pm._check_configured(plugin),
            "config_fields": fields,
        })
    return jsonify(result)


@plugins_bp.route("/<name>/enable", methods=["POST"])
def enable_plugin(name):
    """Enable a plugin and refresh router."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    success = pm.enable_plugin(name)
    if success:
        _refresh_router(pm)
    return jsonify({"success": success})


@plugins_bp.route("/<name>/disable", methods=["POST"])
def disable_plugin(name):
    """Disable a plugin and refresh router."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    success = pm.disable_plugin(name)
    if success:
        _refresh_router(pm)
    return jsonify({"success": success})


@plugins_bp.route("/<name>/test", methods=["POST"])
def test_plugin(name):
    """Run test_connection() for a plugin."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    plugin = pm.plugins.get(name)
    if not plugin:
        return jsonify({"error": "Plugin not found"}), 404

    try:
        result = plugin.test_connection()
        return jsonify({"success": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


@plugins_bp.route("/install", methods=["POST"])
def install_plugin():
    """Install a plugin from a GitHub repository URL.

    Accepts: {"url": "https://github.com/user/repo"}
    Downloads the repo, finds plugin files, copies to src/plugins/.
    """
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "url parameter required"}), 400

    # Parse GitHub URL → user/repo
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url
    )
    if not match:
        return jsonify({"error": "Invalid GitHub URL. Expected: https://github.com/user/repo"}), 400

    owner, repo = match.group(1), match.group(2)
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"

    try:
        # Download zip archive
        r = requests.get(zip_url, timeout=30, stream=True)
        if r.status_code == 404:
            # Try master branch
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
            r = requests.get(zip_url, timeout=30, stream=True)
        if r.status_code != 200:
            return jsonify({"error": f"Failed to download: HTTP {r.status_code}"}), 502

        # Extract to temp dir
        with tempfile.TemporaryDirectory() as tmp:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            z.extractall(tmp)

            # Find the extracted root directory (repo-main/ or repo-master/)
            extracted = [d for d in Path(tmp).iterdir() if d.is_dir()]
            if not extracted:
                return jsonify({"error": "Empty archive"}), 400
            root = extracted[0]

            # Find plugin files: plugin_*.py or directories with __init__.py
            installed = []
            for f in root.iterdir():
                if f.is_file() and f.name.startswith("plugin_") and f.suffix == ".py":
                    dest = PLUGINS_DIR / f.name
                    shutil.copy2(str(f), str(dest))
                    name = pm.load_new_plugin(dest)
                    if name:
                        installed.append(name)
                        _refresh_router(pm)
                elif f.is_dir() and (f / "__init__.py").exists() and not f.name.startswith("_"):
                    dest = PLUGINS_DIR / f.name
                    if dest.exists():
                        shutil.rmtree(str(dest))
                    shutil.copytree(str(f), str(dest))
                    name = pm.load_new_plugin(dest)
                    if name:
                        installed.append(name)
                        _refresh_router(pm)

            # If no plugin_*.py found at root, check for src/plugins/ in repo
            if not installed:
                src_plugins = root / "src" / "plugins"
                if src_plugins.is_dir():
                    for f in src_plugins.iterdir():
                        if f.is_file() and f.name.startswith("plugin_") and f.suffix == ".py":
                            dest = PLUGINS_DIR / f.name
                            shutil.copy2(str(f), str(dest))
                            name = pm.load_new_plugin(dest)
                            if name:
                                installed.append(name)
                                _refresh_router(pm)

            if not installed:
                return jsonify({"error": "No valid plugin files found in repository. Plugin files must be named plugin_*.py"}), 400

            return jsonify({
                "success": True,
                "installed": installed,
                "source": f"{owner}/{repo}",
            })

    except requests.Timeout:
        return jsonify({"error": "Download timed out"}), 504
    except Exception as e:
        logger.error("Plugin install failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@plugins_bp.route("/<name>/uninstall", methods=["POST"])
def uninstall_plugin(name):
    """Uninstall a plugin by removing its file and unloading it."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    plugin = pm.plugins.get(name)
    if not plugin:
        return jsonify({"error": "Plugin not found"}), 404

    # Remove from manager
    pm.remove_plugin(name)
    _refresh_router(pm)

    # Try to delete the plugin file
    deleted_file = False
    plugin_file = PLUGINS_DIR / f"plugin_{name}.py"
    plugin_dir = PLUGINS_DIR / name
    if plugin_file.exists():
        os.remove(str(plugin_file))
        deleted_file = True
    elif plugin_dir.is_dir():
        shutil.rmtree(str(plugin_dir))
        deleted_file = True

    return jsonify({"success": True, "file_deleted": deleted_file})


def _refresh_router(pm):
    """Re-register all plugin keywords in the router after enable/disable."""
    from .app import get_agent
    agent = get_agent()
    if agent and agent.router:
        agent.router.register_from_plugin_manager(pm)
        # Update integrations dict on the agent
        enabled = pm.get_integrations_dict()
        # Merge with built-in integrations (keep calendar, notes, media)
        for key in list(agent.integrations.keys()):
            if key not in enabled and key not in ("calendar", "notes", "media"):
                del agent.integrations[key]
        agent.integrations.update(enabled)
