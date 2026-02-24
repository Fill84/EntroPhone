"""Plugin manager - discovers, loads, validates, and manages plugins."""

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import PluginBase
from .context import PluginContext

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parent  # src/plugins/
SKIP_FILES = {"__init__.py", "base.py", "context.py", "manager.py"}


class PluginManager:
    """Discovers, loads, and manages plugins."""

    def __init__(self):
        self._plugins: Dict[str, PluginBase] = {}
        self._enabled: Dict[str, bool] = {}
        self._plugin_paths: Dict[str, Path] = {}
        self._context: Optional[PluginContext] = None
        self._db = None

    @property
    def plugins(self) -> Dict[str, PluginBase]:
        return self._plugins

    @property
    def context(self) -> Optional[PluginContext]:
        return self._context

    def init_context(self, db=None, ollama=None, tts=None,
                     callback_queue=None, call_logger=None,
                     config=None) -> PluginContext:
        """Create and store the PluginContext."""
        self._db = db
        self._context = PluginContext(
            db=db, ollama=ollama, tts=tts,
            callback_queue=callback_queue,
            call_logger=call_logger,
            config=config or {},
        )
        return self._context

    def discover_and_load(self) -> List[str]:
        """Scan plugins directory and load all valid plugin packages.

        Returns list of loaded plugin names.
        Only package directories (with __init__.py) are supported.
        """
        loaded = []

        # Warn about unsupported single .py plugin files
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            if path.name not in SKIP_FILES:
                logger.warning(
                    "Single-file plugin '%s' ignored. "
                    "Plugins must be in a package directory with __init__.py.",
                    path.name,
                )

        # Package directories
        for path in sorted(PLUGINS_DIR.iterdir()):
            if path.is_dir() and (path / "__init__.py").exists():
                if path.name.startswith("_"):
                    continue
                name = self._load_plugin_from_package(path)
                if name:
                    loaded.append(name)

        return loaded

    def _load_plugin_from_package(self, pkg_path: Path) -> Optional[str]:
        """Load a plugin from a package directory."""
        module_name = f"src.plugins.{pkg_path.name}"
        try:
            init_file = pkg_path / "__init__.py"
            spec = importlib.util.spec_from_file_location(
                module_name, init_file,
                submodule_search_locations=[str(pkg_path)],
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            plugin_cls = self._find_plugin_class(module)
            if plugin_cls is None:
                return None

            name = self._instantiate_plugin(plugin_cls)
            if name:
                self._plugin_paths[name] = pkg_path
            return name

        except Exception as e:
            logger.error("Failed to load plugin package %s: %s",
                         pkg_path.name, e, exc_info=True)
            return None

    def _find_plugin_class(self, module) -> Optional[type]:
        """Find the first PluginBase subclass in a module."""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type)
                    and issubclass(attr, PluginBase)
                    and attr is not PluginBase):
                return attr
        return None

    def _instantiate_plugin(self, plugin_cls: type) -> Optional[str]:
        """Create plugin instance, run setup, determine enabled state."""
        try:
            plugin = plugin_cls()
            name = plugin.meta.name

            if name in self._plugins:
                logger.warning("Duplicate plugin name '%s', skipping", name)
                return None

            if self._context:
                plugin.setup(self._context)

            if self._db and hasattr(plugin, "create_tables"):
                try:
                    plugin.create_tables(self._db)
                except Exception as e:
                    logger.error("Plugin %s create_tables failed: %s", name, e)

            enabled = self._check_enabled(plugin)

            if enabled:
                try:
                    if not plugin.test_connection():
                        logger.warning("Plugin %s connection test failed", name)
                        enabled = False
                except Exception as e:
                    logger.warning("Plugin %s test error: %s", name, e)
                    enabled = False

            self._plugins[name] = plugin
            self._enabled[name] = enabled

            if enabled:
                try:
                    plugin.on_enable()
                except Exception as e:
                    logger.error("Plugin %s on_enable failed: %s", name, e)

            logger.info("Plugin loaded: %s v%s [%s]",
                        plugin.meta.display_name, plugin.meta.version,
                        "enabled" if enabled else "disabled")
            return name

        except Exception as e:
            logger.error("Failed to instantiate plugin: %s", e, exc_info=True)
            return None

    def _check_enabled(self, plugin: PluginBase) -> bool:
        """Check if a plugin should be enabled."""
        # Check DB override first
        if self._db:
            try:
                db_val = self._db.get_setting(
                    f"plugin_enabled_{plugin.meta.name}")
                if db_val is not None:
                    return db_val == "true"
            except Exception:
                pass

        # Check .env key
        env_key = plugin.enabled_env_key
        if env_key:
            val = os.getenv(env_key, "false").lower()
            return val in ("true", "1", "yes", "on")

        # No env_key → always enabled
        return True

    def load_new_plugin(self, file_path: Path) -> Optional[str]:
        """Load a single new plugin package at runtime.

        Returns plugin name on success, None on failure.
        Only package directories (with __init__.py) are supported.
        """
        if file_path.is_dir() and (file_path / "__init__.py").exists():
            return self._load_plugin_from_package(file_path)
        logger.warning(
            "Plugin must be a package directory with __init__.py: %s",
            file_path,
        )
        return None

    def remove_plugin(self, name: str) -> bool:
        """Remove a plugin from the manager (does not delete files)."""
        if name not in self._plugins:
            return False

        plugin = self._plugins[name]
        if self._enabled.get(name):
            try:
                plugin.on_disable()
            except Exception:
                pass
            self._enabled[name] = False

        del self._plugins[name]
        if name in self._enabled:
            del self._enabled[name]
        self._plugin_paths.pop(name, None)

        logger.info("Plugin removed: %s", name)
        return True

    # --- Public interface ---

    def get_enabled_plugins(self) -> Dict[str, PluginBase]:
        return {n: p for n, p in self._plugins.items()
                if self._enabled.get(n, False)}

    def get_integrations_dict(self) -> Dict[str, PluginBase]:
        """Backward-compatible integrations dict for SIPVoiceAgent."""
        return self.get_enabled_plugins()

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def enable_plugin(self, name: str) -> bool:
        """Enable a plugin at runtime."""
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        if self._enabled.get(name):
            return True

        try:
            if not plugin.test_connection():
                logger.warning("Cannot enable %s: connection test failed", name)
                return False
        except Exception:
            pass

        self._enabled[name] = True
        if self._db:
            self._db.set_setting(f"plugin_enabled_{name}", "true")

        try:
            plugin.on_enable()
        except Exception as e:
            logger.error("Plugin %s on_enable failed: %s", name, e)

        logger.info("Plugin enabled: %s", name)
        return True

    def disable_plugin(self, name: str) -> bool:
        """Disable a plugin at runtime."""
        plugin = self._plugins.get(name)
        if not plugin:
            return False

        self._enabled[name] = False
        if self._db:
            self._db.set_setting(f"plugin_enabled_{name}", "false")

        try:
            plugin.on_disable()
        except Exception as e:
            logger.error("Plugin %s on_disable failed: %s", name, e)

        logger.info("Plugin disabled: %s", name)
        return True

    def get_all_keywords(self) -> Dict[str, Dict[str, List[str]]]:
        """Collect keywords from all enabled plugins."""
        return {n: p.keywords for n, p in self._plugins.items()
                if self._enabled.get(n)}

    def get_all_categories(self) -> Dict[str, Dict[str, Any]]:
        """Collect category options from all enabled plugins."""
        return {n: p.category_options for n, p in self._plugins.items()
                if self._enabled.get(n) and p.category_options}

    def get_all_category_names(self) -> Dict[str, Dict[str, List[str]]]:
        """Collect category names from all enabled plugins."""
        return {n: p.category_names for n, p in self._plugins.items()
                if self._enabled.get(n) and p.category_names}

    def get_all_config_schemas(self) -> Dict[str, List]:
        """Collect config schemas from all plugins."""
        return {n: p.config_schema for n, p in self._plugins.items()
                if p.config_schema}

    def get_integration_details(self, agent_config: dict = None) -> List[Dict]:
        """Build integration details for the status API."""
        details = []
        for name, plugin in self._plugins.items():
            config_keys = [f.key for f in plugin.config_schema]
            details.append({
                "key": name,
                "label": plugin.meta.display_name,
                "active": self._enabled.get(name, False),
                "configured": self._check_configured(plugin),
                "config_keys": config_keys,
                "type": "plugin",
                "tab": "plugins",
                "version": plugin.meta.version,
                "description": plugin.meta.description,
                "author": plugin.meta.author,
            })
        return details

    def _check_configured(self, plugin: PluginBase) -> bool:
        """Check if all required config fields have values."""
        for f in plugin.config_schema:
            if f.required and not os.getenv(f.key, ""):
                return False
        return True
