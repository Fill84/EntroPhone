"""Plugins management API - list, enable, disable, configure, test, install."""

import io
import logging
import os
import re
import shutil
import tempfile
import uuid
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
        pages = []
        for page in plugin.dashboard_pages:
            pages.append({
                "id": page.id,
                "title": page.title,
                "icon": page.icon,
                "type": page.type,
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
            "pages": pages,
        })
    return jsonify(result)


@plugins_bp.route("/<name>/enable", methods=["POST"])
def enable_plugin(name):
    """Enable a plugin and refresh router."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    plugin = pm.plugins.get(name)
    if not plugin:
        return jsonify({"error": "Plugin not found"}), 404

    success = pm.enable_plugin(name)
    if success:
        _refresh_router(pm)
        return jsonify({"success": True})

    # Determine why enabling failed
    if not pm._check_configured(plugin):
        required = [f.label for f in plugin.config_schema if f.required]
        return jsonify({
            "error": f"Configure required fields first: {', '.join(required)}",
        })
    return jsonify({"error": "Connection test failed. Check plugin settings."})


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


def _generate_init_py(plugin_filename: str) -> str:
    """Generate an __init__.py that imports all classes from a plugin file."""
    module_stem = plugin_filename.replace(".py", "")
    return f"from .{module_stem} import *\n"


def _install_from_dir(source_dir: Path, pm) -> tuple:
    """Install a plugin from a package directory into a random folder.

    Returns (installed_names, errors).
    """
    random_name = f"plugin_{uuid.uuid4().hex[:8]}"
    dest_dir = PLUGINS_DIR / random_name
    if dest_dir.exists():
        shutil.rmtree(str(dest_dir))
    shutil.copytree(str(source_dir), str(dest_dir))

    # Validate before loading
    valid, errors = pm.validate_plugin(dest_dir)
    if not valid:
        shutil.rmtree(str(dest_dir), ignore_errors=True)
        return [], errors

    name = pm.load_new_plugin(dest_dir)
    if name:
        _refresh_router(pm)
        return [name], []
    # Cleanup on failure
    shutil.rmtree(str(dest_dir), ignore_errors=True)
    return [], ["Plugin failed to load after validation"]


def _install_from_file(source_file: Path, pm) -> tuple:
    """Wrap a single .py plugin file in a package directory and install.

    Returns (installed_names, errors).
    """
    random_name = f"plugin_{uuid.uuid4().hex[:8]}"
    dest_dir = PLUGINS_DIR / random_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source_file), str(dest_dir / source_file.name))
    (dest_dir / "__init__.py").write_text(_generate_init_py(source_file.name))

    # Validate before loading
    valid, errors = pm.validate_plugin(dest_dir)
    if not valid:
        shutil.rmtree(str(dest_dir), ignore_errors=True)
        return [], errors

    name = pm.load_new_plugin(dest_dir)
    if name:
        _refresh_router(pm)
        return [name], []
    # Cleanup on failure
    shutil.rmtree(str(dest_dir), ignore_errors=True)
    return [], ["Plugin failed to load after validation"]


@plugins_bp.route("/install", methods=["POST"])
def install_plugin():
    """Install a plugin from a GitHub repository URL.

    Accepts: {"url": "https://github.com/user/repo"}
    Downloads the repo, finds plugin files, copies to src/plugins/<random>/.
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

            installed = []
            validation_errors = []

            # Check if root itself is a plugin package (has __init__.py)
            if (root / "__init__.py").exists():
                names, errors = _install_from_dir(root, pm)
                installed.extend(names)
                validation_errors.extend(errors)

            # Check root for plugin package directories or .py files
            if not installed and not validation_errors:
                for f in root.iterdir():
                    if f.is_dir() and (f / "__init__.py").exists() and not f.name.startswith("_"):
                        names, errors = _install_from_dir(f, pm)
                        installed.extend(names)
                        validation_errors.extend(errors)
                        if installed or errors:
                            break
                    elif f.is_file() and f.suffix == ".py" and not f.name.startswith("_"):
                        names, errors = _install_from_file(f, pm)
                        installed.extend(names)
                        validation_errors.extend(errors)
                        if installed or errors:
                            break

            # Fallback: check src/plugins/ in repo
            if not installed and not validation_errors:
                src_plugins = root / "src" / "plugins"
                if src_plugins.is_dir():
                    for f in src_plugins.iterdir():
                        if f.is_dir() and (f / "__init__.py").exists() and not f.name.startswith("_"):
                            names, errors = _install_from_dir(f, pm)
                            installed.extend(names)
                            validation_errors.extend(errors)
                            if installed or errors:
                                break
                        elif f.is_file() and f.suffix == ".py" and not f.name.startswith("_"):
                            names, errors = _install_from_file(f, pm)
                            installed.extend(names)
                            validation_errors.extend(errors)
                            if installed or errors:
                                break

            if validation_errors:
                return jsonify({
                    "error": "Plugin validation failed",
                    "warnings": validation_errors,
                }), 400

            if not installed:
                return jsonify({"error": "No valid plugin found in repository"}), 400

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
    """Uninstall a plugin by removing its directory and unloading it."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    plugin = pm.plugins.get(name)
    if not plugin:
        return jsonify({"error": "Plugin not found"}), 404

    # Get the plugin's directory path before removing
    plugin_path = pm._plugin_paths.get(name)

    # Remove from manager
    pm.remove_plugin(name)
    _refresh_router(pm)

    # Delete the plugin directory
    deleted_file = False
    if plugin_path and plugin_path.is_dir():
        shutil.rmtree(str(plugin_path))
        deleted_file = True

    return jsonify({"success": True, "file_deleted": deleted_file})


@plugins_bp.route("/<name>/pages")
def plugin_pages(name):
    """List dashboard pages a plugin provides."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    plugin = pm.plugins.get(name)
    if not plugin:
        return jsonify({"error": "Plugin not found"}), 404

    pages = []
    for page in plugin.dashboard_pages:
        pages.append({
            "id": page.id,
            "title": page.title,
            "icon": page.icon,
            "type": page.type,
        })
    return jsonify({"plugin": name, "pages": pages})


@plugins_bp.route("/<name>/pages/<page_id>")
def plugin_page_render(name, page_id):
    """Render a specific plugin dashboard page."""
    pm = _get_pm()
    if not pm:
        return jsonify({"error": "Plugin manager not available"}), 503

    plugin = pm.plugins.get(name)
    if not plugin:
        return jsonify({"error": "Plugin not found"}), 404

    # Verify page_id is valid
    valid_ids = [p.id for p in plugin.dashboard_pages]
    if page_id not in valid_ids:
        return jsonify({"error": "Page not found"}), 404

    try:
        html = plugin.render_page(page_id)
        page_meta = next(p for p in plugin.dashboard_pages if p.id == page_id)
        return jsonify({
            "plugin": name,
            "page_id": page_id,
            "title": page_meta.title,
            "type": page_meta.type,
            "html": html,
        })
    except Exception as e:
        logger.error("Plugin page render failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


def _refresh_router(pm):
    """Re-register all plugin keywords in the router after enable/disable."""
    from .app import get_agent
    agent = get_agent()
    if agent and agent.router:
        agent.router.register_from_plugin_manager(pm)
        # Update integrations dict on the agent
        enabled = pm.get_integrations_dict()
        # Merge with built-in integrations (keep calendar, notes)
        for key in list(agent.integrations.keys()):
            if key not in enabled and key not in ("calendar", "notes"):
                del agent.integrations[key]
        agent.integrations.update(enabled)
