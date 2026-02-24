"""Media control integration via Home Assistant media_player entities."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MediaHandler:
    """Control media players via Home Assistant."""

    def __init__(self, ha_handler):
        self.ha = ha_handler

    def handle(self, text: str, language: str = "en") -> str:
        """Handle media control commands."""
        text_lower = text.lower()

        if any(k in text_lower for k in ["play", "speel", "afspelen", "start"]):
            return self._play(text_lower, language)
        elif any(k in text_lower for k in ["stop", "stop"]):
            return self._stop(language)
        elif any(k in text_lower for k in ["pause", "pauze", "pauzeer"]):
            return self._pause(language)
        elif any(k in text_lower for k in ["volume", "harder", "louder"]):
            return self._volume_up(language)
        elif any(k in text_lower for k in ["zachter", "quieter", "softer"]):
            return self._volume_down(language)
        elif any(k in text_lower for k in ["next", "volgend", "volgende"]):
            return self._next(language)
        elif any(k in text_lower for k in ["previous", "vorig", "vorige"]):
            return self._previous(language)
        else:
            return self._msg(
                "I can play, stop, pause, change volume, or skip tracks.",
                "Ik kan afspelen, stoppen, pauzeren, volume wijzigen of nummers overslaan.",
                language,
            )

    def _get_player(self) -> Optional[str]:
        """Find the first available media player entity."""
        entities = self.ha._get_entities()
        for e in entities:
            if e["entity_id"].startswith("media_player."):
                return e["entity_id"]
        return None

    def _play(self, text: str, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "media_play", player)
        return self._msg("Playing.", "Afspelen gestart.", language)

    def _stop(self, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "media_stop", player)
        return self._msg("Stopped.", "Gestopt.", language)

    def _pause(self, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "media_pause", player)
        return self._msg("Paused.", "Gepauzeerd.", language)

    def _volume_up(self, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "volume_up", player)
        return self._msg("Volume up.", "Volume hoger.", language)

    def _volume_down(self, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "volume_down", player)
        return self._msg("Volume down.", "Volume lager.", language)

    def _next(self, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "media_next_track", player)
        return self._msg("Next track.", "Volgend nummer.", language)

    def _previous(self, language: str) -> str:
        player = self._get_player()
        if not player:
            return self._msg("No media player found.", "Geen mediaspeler gevonden.", language)
        self.ha._call_service("media_player", "media_previous_track", player)
        return self._msg("Previous track.", "Vorig nummer.", language)

    def _msg(self, en: str, nl: str, language: str) -> str:
        return nl if language == "nl" else en
