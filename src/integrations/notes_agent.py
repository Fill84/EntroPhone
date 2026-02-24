"""Notes and tasks integration - SQLite-based storage."""

import logging
from typing import Optional

from ..database import Database

logger = logging.getLogger(__name__)


class NotesHandler:
    """Notes and task management backed by SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def handle(self, text: str, language: str = "en") -> str:
        """Handle notes/task commands."""
        text_lower = text.lower()

        # Add a note/task
        if any(k in text_lower for k in [
            "onthoud", "remember", "noteer", "note", "schrijf op",
            "write down", "voeg toe", "add", "noteren",
        ]):
            content = self._extract_content(text)
            if content:
                self.db.add_note(content)
                return self._msg(
                    f"Noted: {content}",
                    f"Genoteerd: {content}",
                    language,
                )
            return self._msg("What should I note?", "Wat moet ik noteren?", language)

        # List notes
        if any(k in text_lower for k in [
            "notities", "notes", "taken", "tasks", "lijst", "list",
            "wat heb ik", "what do i have",
        ]):
            return self._list_notes(language)

        # Clear/delete notes
        if any(k in text_lower for k in ["wis", "clear", "verwijder", "delete"]):
            count = self.db.clear_completed_notes()
            # If no completed notes, clear all
            if count == 0:
                notes = self.db.get_notes()
                for n in notes:
                    self.db.delete_note(n["id"])
                count = len(notes)
            return self._msg(
                f"Cleared {count} notes.",
                f"{count} notities gewist.",
                language,
            )

        # Default: add as note
        content = self._extract_content(text)
        if content:
            self.db.add_note(content)
            return self._msg(f"Noted: {content}", f"Genoteerd: {content}", language)

        return self._msg(
            "You can say: remember, list notes, or clear notes.",
            "Je kunt zeggen: onthoud, notities, of wis notities.",
            language,
        )

    def _list_notes(self, language: str) -> str:
        notes = self.db.get_notes(limit=5)

        if not notes:
            return self._msg("No notes saved.", "Geen notities opgeslagen.", language)

        # Notes come newest first from DB, reverse for display
        notes = list(reversed(notes))
        items = [n["content"] for n in notes]
        notes_str = ". ".join(f"{i+1}: {item}" for i, item in enumerate(items))
        total_notes = self.db.get_notes(limit=100)
        total = len(total_notes)

        if language == "nl":
            return f"Je hebt {total} notities. {notes_str}."
        return f"You have {total} notes. {notes_str}."

    def _extract_content(self, text: str) -> str:
        """Extract the note content from the command text."""
        prefixes = [
            "onthoud dat", "onthoud", "remember that", "remember",
            "noteer", "note", "schrijf op", "write down",
            "voeg toe", "add", "noteren",
        ]
        text_lower = text.lower()
        for prefix in sorted(prefixes, key=len, reverse=True):
            if text_lower.startswith(prefix):
                return text[len(prefix):].strip().strip(".:,")
        return text.strip()

    def _msg(self, en: str, nl: str, language: str) -> str:
        return nl if language == "nl" else en
