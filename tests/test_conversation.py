"""Tests for conversation manager."""

import pytest
from src.ai.conversation import ConversationManager


class TestConversationManager:
    def test_initial_state(self):
        cm = ConversationManager()
        assert cm.history == []
        assert cm.detected_language is None

    def test_add_exchange(self):
        cm = ConversationManager()
        cm.add_exchange("Hello", "Hi there!", "en")
        assert len(cm.history) == 2
        assert cm.history[0]["role"] == "user"
        assert cm.history[1]["role"] == "assistant"
        assert cm.detected_language == "en"

    def test_language_sticky(self):
        cm = ConversationManager()
        cm.add_exchange("Hallo", "Hoi!", "nl")
        assert cm.detected_language == "nl"

        # Language stays when not provided
        cm.add_exchange("Nog iets", "Natuurlijk!")
        assert cm.detected_language == "nl"

        # Language updates when new one provided
        cm.add_exchange("Hello", "Hi!", "en")
        assert cm.detected_language == "en"

    def test_history_trimming(self):
        cm = ConversationManager(max_history=4)
        cm.add_exchange("Q1", "A1")
        cm.add_exchange("Q2", "A2")
        cm.add_exchange("Q3", "A3")
        assert len(cm.history) == 4  # Trimmed to max
        assert cm.history[0]["content"] == "Q2"  # Oldest removed

    def test_messages_for_ollama_dutch(self):
        cm = ConversationManager()
        cm.detected_language = "nl"
        cm.add_exchange("Hallo", "Hoi!")
        messages = cm.get_messages_for_ollama()
        assert messages[0]["role"] == "system"
        assert "Nederlands" in messages[0]["content"]
        assert len(messages) == 3  # system + 2 history

    def test_messages_for_ollama_english(self):
        cm = ConversationManager()
        cm.detected_language = "en"
        messages = cm.get_messages_for_ollama()
        assert messages[0]["role"] == "system"
        assert "English" in messages[0]["content"]

    def test_clear(self):
        cm = ConversationManager()
        cm.add_exchange("Q", "A", "nl")
        cm.clear()
        assert cm.history == []
        assert cm.detected_language is None

    def test_messages_history_limit(self):
        cm = ConversationManager()
        for i in range(10):
            cm.add_exchange(f"Q{i}", f"A{i}")
        messages = cm.get_messages_for_ollama()
        # System + last 8 messages (4 exchanges)
        assert len(messages) == 9
