"""Tests for intent router."""

import pytest
from src.ai.router import IntentRouter


@pytest.fixture
def router():
    return IntentRouter()


class TestIntentRouter:
    def test_homeassistant_dutch(self, router):
        assert router.route("Zet alle lampen aan", "nl") == "homeassistant"
        assert router.route("Doe het licht uit", "nl") == "homeassistant"
        assert router.route("Verander de kleur naar blauw", "nl") == "homeassistant"
        assert router.route("Wat is de temperatuur", "nl") == "homeassistant"

    def test_homeassistant_english(self, router):
        assert router.route("Turn on the lights", "en") == "homeassistant"
        assert router.route("Turn off the lamp", "en") == "homeassistant"
        assert router.route("Set the color to red", "en") == "homeassistant"
        assert router.route("What's the temperature?", "en") == "homeassistant"

    def test_monitoring(self, router):
        assert router.route("How are the servers?", "en") == "monitoring"
        assert router.route("Wat is de server status?", "nl") == "monitoring"
        assert router.route("Ping web-unit", "en") == "monitoring"

    def test_calendar(self, router):
        assert router.route("What's on my calendar?", "en") == "calendar"
        assert router.route("Wat staat er in mijn agenda?", "nl") == "calendar"
        assert router.route("Any appointments today?", "en") == "calendar"

    def test_notes(self, router):
        assert router.route("Remember to buy milk", "en") == "notes"
        assert router.route("Onthoud dat ik melk moet kopen", "nl") == "notes"
        assert router.route("Show my notes", "en") == "notes"
        assert router.route("Wat zijn mijn taken?", "nl") == "notes"

    def test_media(self, router):
        # Media keywords now route to "homeassistant" (media is part of HA plugin)
        assert router.route("Play some music", "en") == "homeassistant"
        assert router.route("Speel muziek", "nl") == "homeassistant"
        assert router.route("Stop the music", "en") == "homeassistant"
        assert router.route("Volume louder", "en") == "homeassistant"

    def test_goodbye(self, router):
        assert router.route("Goodbye", "en") == "goodbye"
        assert router.route("Bye", "en") == "goodbye"
        assert router.route("Doei", "nl") == "goodbye"
        assert router.route("Tot ziens", "nl") == "goodbye"
        assert router.route("No thanks", "en") == "goodbye"

    def test_time(self, router):
        assert router.route("What time is it?", "en") == "time"
        assert router.route("Hoe laat is het?", "nl") == "time"

    def test_general_fallback(self, router):
        assert router.route("Tell me about the Roman Empire", "en") == "general"
        assert router.route("Vertel me over het Romeinse Rijk", "nl") == "general"
        assert router.route("What is Python?", "en") == "general"

    def test_empty_input(self, router):
        assert router.route("", "en") == "general"
        assert router.route("   ", "en") == "general"

    def test_cross_language_detection(self, router):
        """Keywords should be found even if language doesn't match."""
        # Dutch keywords detected when language is "en"
        assert router.route("Zet de lamp aan", "en") == "homeassistant"
