"""Plugin context - provides core services to plugins."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PluginContext:
    """Container for core services available to plugins.

    Passed to each plugin via plugin.setup(context).
    """
    db: Any = None
    ollama: Any = None
    tts: Any = None
    callback_queue: Any = None
    call_logger: Any = None
    config: Dict[str, Any] = field(default_factory=dict)

    def get_env(self, key: str, default: str = "") -> str:
        """Get a configuration value. Checks DB first, then environment."""
        if self.db:
            db_val = self.db.get_setting(key)
            if db_val is not None:
                return db_val
        return os.getenv(key, default)

    def get_env_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean environment variable."""
        val = self.get_env(key, str(default)).lower()
        return val in ("true", "1", "yes", "on")
