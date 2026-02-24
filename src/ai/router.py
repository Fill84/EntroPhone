"""Intent routing - classifies user input and routes to the right handler.

Two-tier approach:
1. Fast keyword matching (<1ms) - covers 80% of cases
2. Ollama fallback for complex/ambiguous input

Keywords and categories are registered dynamically by the plugin system.
Built-in routes (goodbye, time) and built-in integrations (calendar, notes, media)
are registered at startup alongside plugin-provided routes.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class IntentRouter:
    """Classifies user intent and routes to appropriate handler."""

    # Action words that signal a direct command (not just a category mention)
    ACTION_WORDS = {
        "nl": [
            "doe", "zet", "schakel", "dim", "speel", "stop", "pauzeer",
            "onthoud", "noteer", "schrijf", "voeg toe", "verwijder", "wis",
            "hoe laat", "welke", "ping", "check", "controleer",
            "harder", "zachter", "volgend", "vorig", "open", "sluit",
        ],
        "en": [
            "turn", "set", "switch", "dim", "play", "stop", "pause",
            "remember", "note", "write", "add", "remove", "delete", "clear",
            "what time", "which", "ping", "check",
            "louder", "quieter", "next", "previous", "open", "close",
        ],
    }

    def __init__(self):
        # Instance-level keyword routes (dynamically populated)
        self._keyword_routes: Dict[str, Dict[str, List[str]]] = {
            "goodbye": {
                "nl": [
                    "doei", "dag", "tot ziens", "ophangen",
                    "bedankt", "dankjewel", "klaar", "nee dank je",
                    "nee bedankt", "tot later",
                ],
                "en": [
                    "bye", "goodbye", "hang up", "that's all",
                    "thanks", "thank you", "no thanks", "done",
                    "see you", "no thank you",
                ],
            },
            "time": {
                "nl": [
                    "hoe laat", "welke dag", "datum", "tijd",
                    "welke datum",
                ],
                "en": [
                    "what time", "what day", "what date", "the time",
                    "current time", "today's date",
                ],
            },
        }

        # Instance-level category names (dynamically populated)
        self._category_names: Dict[str, Dict[str, List[str]]] = {}

    # --- Dynamic registration ---

    def register_plugin_keywords(self, name: str,
                                  keywords_by_lang: Dict[str, List[str]]) -> None:
        """Register keywords from a plugin or built-in integration."""
        self._keyword_routes[name] = keywords_by_lang

    def register_category_names(self, name: str,
                                 names_by_lang: Dict[str, List[str]]) -> None:
        """Register category names from a plugin or built-in integration."""
        self._category_names[name] = names_by_lang

    def unregister(self, name: str) -> None:
        """Remove a plugin's keywords and category names."""
        self._keyword_routes.pop(name, None)
        self._category_names.pop(name, None)

    def register_from_plugin_manager(self, pm) -> None:
        """Bulk-register all keywords and categories from a PluginManager.

        Clears existing plugin routes (keeps built-in goodbye/time) and
        re-registers from the current plugin state.
        """
        builtin = {"goodbye", "time"}
        for key in list(self._keyword_routes.keys()):
            if key not in builtin:
                del self._keyword_routes[key]
        self._category_names.clear()

        for name, kw in pm.get_all_keywords().items():
            self.register_plugin_keywords(name, kw)
        for name, cn in pm.get_all_category_names().items():
            self.register_category_names(name, cn)

    # --- Intent classification ---

    def route(self, text: str, language: str = "en") -> str:
        """Classify user input intent.

        Returns intent category string or 'general'.
        """
        text_lower = text.lower().strip()
        if not text_lower:
            return "general"

        languages = [language, "en" if language != "en" else "nl"]

        for intent, keywords_by_lang in self._keyword_routes.items():
            for lang in languages:
                keywords = keywords_by_lang.get(lang, [])
                for keyword in keywords:
                    if keyword in text_lower:
                        logger.debug("Intent: %s (keyword='%s', lang=%s)",
                                     intent, keyword, lang)
                        return intent

        return "general"

    def is_category_only(self, text: str, language: str = "en") -> bool:
        """Check if the user only mentioned a category name without a command.

        Returns True if the input is just a category name (show menu).
        Returns False if it contains an action word (execute directly).
        """
        text_lower = text.lower().strip()
        words = text_lower.split()

        # Short input matching a category name → menu
        if len(words) <= 3:
            for names_by_lang in self._category_names.values():
                for lang in [language, "en" if language != "en" else "nl"]:
                    for name in names_by_lang.get(lang, []):
                        if name in text_lower:
                            return True

        # Action words → direct command
        for lang in [language, "en" if language != "en" else "nl"]:
            for action in self.ACTION_WORDS.get(lang, []):
                if action in text_lower:
                    return False

        # Short input without action words → likely a category mention
        if len(words) <= 2:
            return True

        return False
