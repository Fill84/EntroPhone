"""Tests for notes integration."""

import tempfile
import pytest
from src.integrations.notes_agent import NotesHandler


class TestNotesHandler:
    def _make_handler(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            return NotesHandler(persist_path=f.name)

    def test_add_note_english(self):
        handler = self._make_handler()
        result = handler.handle("Remember to buy milk", "en")
        assert "buy milk" in result.lower()

    def test_add_note_dutch(self):
        handler = self._make_handler()
        result = handler.handle("Onthoud dat ik melk moet kopen", "nl")
        assert "melk" in result.lower()

    def test_list_notes(self):
        handler = self._make_handler()
        handler.handle("Remember buy milk", "en")
        handler.handle("Remember call dentist", "en")
        result = handler.handle("Show my notes", "en")
        assert "2 notes" in result.lower() or "milk" in result.lower()

    def test_list_empty(self):
        handler = self._make_handler()
        result = handler.handle("Show my notes", "en")
        assert "no notes" in result.lower()

    def test_extract_content(self):
        handler = self._make_handler()
        assert handler._extract_content("remember to buy milk") == "to buy milk"
        assert handler._extract_content("onthoud dat het regent") == "dat het regent"
        assert handler._extract_content("just plain text") == "just plain text"
