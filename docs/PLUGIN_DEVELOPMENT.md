# ClaudePhone Plugin Development Guide

This guide explains how to create plugins for ClaudePhone, the natural SIP voice assistant.

## Overview

Plugins extend ClaudePhone with new capabilities that users can trigger via voice commands during phone calls. Each plugin:

- Handles specific voice commands (keywords) in Dutch and English
- Can integrate with external services (APIs, smart home, etc.)
- Has its own configuration managed through the dashboard
- Can be enabled/disabled at runtime

## Quick Start

Create a plugin package directory in `src/plugins/`:

```
src/plugins/
  example/
    __init__.py
    example.py
```

**`__init__.py`** — exports your plugin class:

```python
from .example import ExamplePlugin

__all__ = ["ExamplePlugin"]
```

**`example.py`** — your plugin implementation:

```python
from ..base import PluginBase, PluginMeta


class ExamplePlugin(PluginBase):

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="example",
            display_name="Example Plugin",
            description="A simple example plugin",
            version="1.0.0",
            author="Your Name",
        )

    @property
    def keywords(self) -> dict:
        return {
            "nl": ["voorbeeld", "test"],
            "en": ["example", "test"],
        }

    def handle(self, text: str, language: str = "en") -> str:
        return self._msg(
            "This is an example response!",
            "Dit is een voorbeeld antwoord!",
            language,
        )
```

Restart the container and the plugin will be auto-discovered.

## Plugin Directory Structure

Every plugin must be a **package directory** inside `src/plugins/`:

```
src/plugins/
  myplugin/
    __init__.py      # Required — exports your PluginBase subclass
    myplugin.py      # Main plugin class
    README.md        # Optional documentation
```

Rules:
- Must contain an `__init__.py` that exports a `PluginBase` subclass
- Directories starting with `_` are ignored
- Single `.py` files in `src/plugins/` are **not supported** (they will be ignored with a warning)
- Core files (`base.py`, `manager.py`, `context.py`) are part of the plugin system and cannot be overridden

For a full reference implementation with multiple modules, dashboard widgets, and custom API routes, see the `homeassistant/` plugin.

## Plugin Interface

### Required: `meta` property

Every plugin must define metadata:

```python
@property
def meta(self) -> PluginMeta:
    return PluginMeta(
        name="myplugin",           # Unique identifier (lowercase, no spaces)
        display_name="My Plugin",  # Shown in the dashboard
        description="What it does",
        version="1.0.0",
        author="Author Name",
    )
```

### Required: `keywords` property

Keywords trigger your plugin when a user says them during a call:

```python
@property
def keywords(self) -> dict:
    return {
        "nl": ["weer", "temperatuur", "regen"],    # Dutch keywords
        "en": ["weather", "temperature", "rain"],   # English keywords
    }
```

The intent router matches these keywords against user speech. When a keyword matches, your plugin's `handle()` method is called.

### Required: `handle()` method

This is the main entry point. It receives the transcribed text and detected language, and must return a string that will be spoken back to the caller via TTS:

```python
def handle(self, text: str, language: str = "en") -> str:
    # Process the user's request
    # Return a TTS-ready response string
    return self._msg(
        "English response",
        "Nederlands antwoord",
        language,
    )
```

The `_msg(en, nl, language)` helper selects the correct language automatically.

## Optional Features

### Configuration Fields

Define configuration variables your plugin needs:

```python
from ..base import PluginBase, PluginMeta, ConfigField

# ...

@property
def config_schema(self) -> list:
    return [
        ConfigField(
            key="MYPLUGIN_API_KEY",
            label="API Key",
            required=True,
            field_type="password",  # text, password, toggle, textarea, select
            sensitive=True,
            placeholder="Enter your API key",
        ),
        ConfigField(
            key="MYPLUGIN_ENABLED",
            label="Enable Plugin",
            field_type="toggle",
            default="false",
        ),
        ConfigField(
            key="MYPLUGIN_MODE",
            label="Mode",
            field_type="select",
            options=["basic", "advanced"],
            default="basic",
        ),
    ]
```

**ConfigField options:**
| Field | Type | Description |
|-------|------|-------------|
| `key` | str | The configuration variable name |
| `label` | str | Display label in dashboard |
| `required` | bool | Whether the field must have a value |
| `default` | str | Default value |
| `field_type` | str | `text`, `password`, `toggle`, `textarea`, `select` |
| `placeholder` | str | Placeholder text |
| `options` | list | Options for `select` type |
| `sensitive` | bool | Mask value in API responses |
| `hot_reload` | bool | Whether changes apply without restart |

### Enable/Disable via Environment Variable

```python
@property
def enabled_env_key(self) -> str:
    return "MYPLUGIN_ENABLED"
```

If set, the plugin is only enabled when this key is `true`/`1`/`yes`/`on`.
If not set (returns `None`), the plugin is always enabled when installed.

### Category Menus

When a user says just a category name (like "smart home"), show options:

```python
@property
def category_names(self) -> dict:
    return {
        "nl": ["weer", "weerbericht"],
        "en": ["weather", "forecast"],
    }

@property
def category_options(self) -> dict:
    return {
        "nl": {
            "name": "Weer",
            "options": [
                {"label": "Huidig weer", "command": "huidig weer"},
                {"label": "Voorspelling", "command": "weersvoorspelling"},
            ],
        },
        "en": {
            "name": "Weather",
            "options": [
                {"label": "Current weather", "command": "current weather"},
                {"label": "Forecast", "command": "weather forecast"},
            ],
        },
    }
```

### Connection Testing

Implement `test_connection()` for the dashboard "Test" button:

```python
def test_connection(self) -> bool:
    """Return True if the external service is reachable."""
    try:
        response = requests.get(f"{self.api_url}/health", timeout=5)
        return response.status_code == 200
    except Exception:
        return False
```

### Database Tables

If your plugin needs persistent storage:

```python
def create_tables(self, db) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS myplugin_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
```

### Lifecycle Hooks

```python
def setup(self, context) -> None:
    """Called once when plugin is loaded. Receive core services."""
    super().setup(context)  # Sets self.context
    # Access services: context.db, context.ollama, context.tts, etc.

def on_enable(self) -> None:
    """Called when plugin transitions to enabled state."""
    # Initialize connections, start background tasks, etc.

def on_disable(self) -> None:
    """Called when plugin transitions to disabled state."""
    # Clean up connections, stop background tasks, etc.
```

### Custom API Routes

Plugins can register custom Flask routes:

```python
def register_routes(self):
    from flask import Blueprint, jsonify

    bp = Blueprint(f"plugin_{self.meta.name}", __name__)

    @bp.route("/status")
    def status():
        return jsonify({"ok": True})

    return bp
```

Routes are mounted at `/api/plugins/<plugin_name>/`.

### Dashboard Widgets and Pages

Plugins can provide dashboard UI:

```python
from ..base import DashboardWidget, DashboardPage

@property
def dashboard_widgets(self) -> list:
    return [
        DashboardWidget(
            id="my-status",
            title="My Plugin",
            icon="...",
            size="small",   # small, medium, large
            order=50,       # lower = earlier
        ),
    ]

@property
def dashboard_pages(self) -> list:
    return [
        DashboardPage(id="settings", title="Settings", type="config"),
    ]

def render_widget(self, widget_id: str) -> str:
    if widget_id == "my-status":
        return "<div>Widget HTML here</div>"
    return ""

def render_page(self, page_id: str) -> str:
    if page_id == "settings":
        return "<div>Page HTML here</div>"
    return ""
```

## Plugin Context

The `PluginContext` provides access to core services:

```python
self.context.db           # SQLite database instance
self.context.ollama       # Ollama LLM client
self.context.tts          # Text-to-speech engine
self.context.callback_queue  # Queue for outgoing calls
self.context.call_logger  # Call history logger
self.context.config       # Full configuration dict
self.context.get_env("KEY")           # Get configuration value (DB > env)
self.context.get_env_bool("KEY")      # Get boolean config value
self.context.set_env("KEY", "value")  # Persist to DB and environment
```

## Installation Methods

### Manual
Copy your plugin directory to `src/plugins/` and restart the container.

### Via Dashboard
Use the "Install from GitHub" feature on the Plugins tab:
1. Enter your GitHub repository URL
2. Click "Install"
3. The plugin is downloaded, validated, and loaded automatically

### GitHub Repository Structure

For the GitHub installer to find your plugin:

**Root is the plugin package (recommended):**
```
my-plugin-repo/
  __init__.py
  myplugin.py
  README.md
```

**Plugin in a subdirectory:**
```
my-plugin-repo/
  myplugin/
    __init__.py
    myplugin.py
  README.md
```

The GitHub installer can also wrap single `.py` files automatically, but for local development plugins must be package directories.

## Complete Example: Weather Plugin

### Directory structure

```
src/plugins/
  weather/
    __init__.py
    weather.py
```

### `__init__.py`

```python
from .weather import WeatherPlugin

__all__ = ["WeatherPlugin"]
```

### `weather.py`

```python
"""Weather plugin - provides weather information via OpenWeatherMap API."""

import logging

import requests

from ..base import PluginBase, PluginMeta, ConfigField

logger = logging.getLogger(__name__)


class WeatherPlugin(PluginBase):

    def __init__(self):
        self._api_key = ""
        self._city = "Amsterdam"

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="weather",
            display_name="Weather",
            description="Get weather information via OpenWeatherMap",
            version="1.0.0",
            author="ClaudePhone Community",
        )

    @property
    def keywords(self) -> dict:
        return {
            "nl": ["weer", "temperatuur", "regen", "zon", "wind",
                    "weerbericht", "graden"],
            "en": ["weather", "temperature", "rain", "sun", "wind",
                    "forecast", "degrees"],
        }

    @property
    def config_schema(self) -> list:
        return [
            ConfigField(
                key="WEATHER_ENABLED",
                label="Enable Weather",
                field_type="toggle",
                default="false",
            ),
            ConfigField(
                key="WEATHER_API_KEY",
                label="OpenWeatherMap API Key",
                required=True,
                field_type="password",
                sensitive=True,
                placeholder="Your API key from openweathermap.org",
            ),
            ConfigField(
                key="WEATHER_CITY",
                label="Default City",
                default="Amsterdam",
                placeholder="City name",
            ),
        ]

    @property
    def enabled_env_key(self) -> str:
        return "WEATHER_ENABLED"

    def on_enable(self) -> None:
        self._api_key = self.context.get_env("WEATHER_API_KEY")
        self._city = self.context.get_env("WEATHER_CITY") or "Amsterdam"

    def test_connection(self) -> bool:
        if not self._api_key:
            self._api_key = self.context.get_env("WEATHER_API_KEY")
        if not self._api_key:
            return False
        try:
            r = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": self._city, "appid": self._api_key},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            return False

    def handle(self, text: str, language: str = "en") -> str:
        if not self._api_key:
            return self._msg(
                "Weather API key not configured.",
                "Weer API key is niet geconfigureerd.",
                language,
            )

        try:
            r = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": self._city,
                    "appid": self._api_key,
                    "units": "metric",
                    "lang": language,
                },
                timeout=10,
            )
            data = r.json()
            temp = round(data["main"]["temp"])
            desc = data["weather"][0]["description"]

            if language == "nl":
                return f"Het is nu {temp} graden in {self._city}. {desc}."
            return f"It's currently {temp} degrees in {self._city}. {desc}."
        except Exception as e:
            logger.error("Weather API error: %s", e)
            return self._msg(
                "Sorry, I couldn't get the weather information.",
                "Sorry, ik kon de weersinformatie niet ophalen.",
                language,
            )
```

## Tips

- Keep responses short and natural — they will be spoken aloud via TTS
- Always support both Dutch (`nl`) and English (`en`)
- Use `self._msg(en, nl, language)` for bilingual responses
- Handle errors gracefully — return a friendly message, never crash
- Use `logger = logging.getLogger(__name__)` for logging
- Test your `test_connection()` — it's called during plugin load
- Access configuration via `self.context.get_env("KEY")` in lifecycle hooks
- Use relative imports (`from ..base import ...`) within your plugin package
- See the `homeassistant/` plugin for a complex real-world example with multiple modules, dashboard widgets, and custom API routes
