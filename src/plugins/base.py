"""Base class for all ClaudePhone plugins."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConfigField:
    """Describes one .env configuration key a plugin needs."""
    key: str
    label: str
    required: bool = False
    default: str = ""
    field_type: str = "text"  # text, password, toggle, textarea, select
    placeholder: str = ""
    options: List[str] = field(default_factory=list)
    sensitive: bool = False
    hot_reload: bool = False


@dataclass
class PluginMeta:
    """Plugin metadata."""
    name: str
    display_name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""


@dataclass
class DashboardPage:
    """A dashboard page that a plugin provides.

    The ``id`` becomes part of the URL: ``/plugins/<plugin_name>/<id>``.
    """
    id: str
    title: str
    icon: str = ""
    type: str = "config"  # config, wizard, custom


class PluginBase(ABC):
    """Abstract base class for ClaudePhone plugins.

    Every plugin must subclass this and implement:
    - meta (PluginMeta)
    - keywords (dict of language -> keyword list)
    - handle(text, language) -> str
    """

    # --- Required (abstract) ---

    @property
    @abstractmethod
    def meta(self) -> PluginMeta:
        ...

    @property
    @abstractmethod
    def keywords(self) -> Dict[str, List[str]]:
        """Keyword routes for intent matching.

        Returns: {"nl": ["lamp", "licht", ...], "en": ["light", "lamp", ...]}
        """
        ...

    @abstractmethod
    def handle(self, text: str, language: str = "en") -> str:
        """Handle user input and return a TTS-ready response string."""
        ...

    # --- Optional metadata ---

    @property
    def category_names(self) -> Dict[str, List[str]]:
        """Short names that trigger the category menu.

        Returns: {"nl": ["smart home"], "en": ["smart home"]}
        """
        return {}

    @property
    def category_options(self) -> Dict[str, Dict[str, Any]]:
        """Category menu options shown when user says just the category name.

        Returns: {"nl": {"name": "...", "options": [...]}, "en": {...}}
        """
        return {}

    @property
    def config_schema(self) -> List[ConfigField]:
        """Configuration fields this plugin needs (.env keys)."""
        return []

    @property
    def dashboard_pages(self) -> List["DashboardPage"]:
        """Dashboard pages this plugin provides.

        Each page gets its own URL: ``/plugins/<plugin_name>/<page_id>``.
        Override to add custom config pages, setup wizards, etc.
        """
        return []

    def render_page(self, page_id: str) -> str:
        """Return HTML content for a plugin dashboard page.

        Override this to provide custom page content. The returned HTML
        is inserted into the dashboard's plugin-page container.
        """
        return ""

    @property
    def enabled_env_key(self) -> Optional[str]:
        """The .env key that controls whether this plugin is enabled.

        If None, plugin is always enabled when installed.
        """
        return None

    # --- Lifecycle hooks ---

    def setup(self, context: "PluginContext") -> None:
        """Called once when the plugin is loaded. Receive core services."""
        self.context = context

    def on_enable(self) -> None:
        """Called when the plugin transitions to enabled state."""
        pass

    def on_disable(self) -> None:
        """Called when the plugin transitions to disabled state."""
        pass

    def create_tables(self, db) -> None:
        """Called during init to create any needed DB tables."""
        pass

    def test_connection(self) -> bool:
        """Test if the plugin's external service is reachable."""
        return True

    # --- Helpers ---

    def _msg(self, en: str, nl: str, language: str) -> str:
        """Bilingual message helper."""
        return nl if language == "nl" else en
