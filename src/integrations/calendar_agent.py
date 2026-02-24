"""Calendar integration - local SQLite-based agenda."""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from ..database import Database

logger = logging.getLogger(__name__)


class CalendarHandler:
    """Local calendar/agenda management backed by SQLite."""

    def __init__(self, db: Database):
        self.db = db

    def handle(self, text: str, language: str = "en") -> str:
        """Handle calendar/agenda commands."""
        text_lower = text.lower()

        # Add appointment
        if any(k in text_lower for k in [
            "voeg toe", "toevoegen", "nieuwe afspraak", "maak afspraak",
            "add", "new appointment", "create appointment", "schedule",
        ]):
            return self._add_event(text, language)

        # Delete appointment
        if any(k in text_lower for k in [
            "verwijder", "delete", "annuleer", "cancel", "schrap",
        ]):
            return self._delete_event(text, language)

        # Tomorrow's appointments
        if any(k in text_lower for k in ["morgen", "tomorrow"]):
            return self._list_events_tomorrow(language)

        # Default: today's appointments
        return self._list_events_today(language)

    def _list_events_today(self, language: str) -> str:
        events = self.db.get_events_today()
        if not events:
            return self._msg(
                "No appointments for today.",
                "Geen afspraken voor vandaag.",
                language,
            )
        return self._format_events(events, "today", "vandaag", language)

    def _list_events_tomorrow(self, language: str) -> str:
        events = self.db.get_events_tomorrow()
        if not events:
            return self._msg(
                "No appointments for tomorrow.",
                "Geen afspraken voor morgen.",
                language,
            )
        return self._format_events(events, "tomorrow", "morgen", language)

    def _format_events(self, events: list, en_day: str, nl_day: str, language: str) -> str:
        lines = []
        for e in events[:5]:
            title = e["title"]
            time_str = e.get("event_time") or ""
            if time_str:
                lines.append(f"{time_str} - {title}")
            else:
                lines.append(title)

        events_str = ". ".join(lines)
        count = len(events)

        if language == "nl":
            return f"Je hebt {count} afspraken {nl_day}: {events_str}."
        return f"You have {count} appointments {en_day}: {events_str}."

    def _add_event(self, text: str, language: str) -> str:
        title, event_date, event_time = self._parse_event(text, language)

        if not title:
            return self._msg(
                "What appointment should I add? Say the title, date and time.",
                "Welke afspraak moet ik toevoegen? Zeg de titel, datum en tijd.",
                language,
            )

        self.db.add_event(title, event_date, event_time)

        date_str = event_date
        if event_time:
            date_str += f" {event_time}"

        return self._msg(
            f"Appointment added: {title} on {date_str}.",
            f"Afspraak toegevoegd: {title} op {date_str}.",
            language,
        )

    def _delete_event(self, text: str, language: str) -> str:
        # Extract what to delete
        title = self._extract_title_for_delete(text)
        if not title:
            return self._msg(
                "Which appointment should I delete?",
                "Welke afspraak moet ik verwijderen?",
                language,
            )

        count = self.db.delete_event_by_title(title)
        if count > 0:
            return self._msg(
                f"Deleted {count} appointment(s) matching '{title}'.",
                f"{count} afspraak(en) met '{title}' verwijderd.",
                language,
            )
        return self._msg(
            f"No appointments found matching '{title}'.",
            f"Geen afspraken gevonden met '{title}'.",
            language,
        )

    def _parse_event(self, text: str, language: str) -> Tuple[str, str, Optional[str]]:
        """Parse title, date and time from natural language input.

        Returns (title, date_str, time_str) where date_str is YYYY-MM-DD format.
        """
        # Remove command prefixes
        prefixes = [
            "voeg afspraak toe", "nieuwe afspraak", "maak afspraak",
            "voeg toe", "toevoegen",
            "add appointment", "new appointment", "create appointment",
            "schedule", "add",
        ]
        cleaned = text
        text_lower = text.lower()
        for prefix in sorted(prefixes, key=len, reverse=True):
            if text_lower.startswith(prefix):
                cleaned = text[len(prefix):].strip().strip(".:,")
                break

        if not cleaned:
            return ("", date.today().isoformat(), None)

        # Try to find date
        event_date = date.today().isoformat()
        event_time = None

        # Dutch date words
        if "morgen" in cleaned.lower() or "tomorrow" in cleaned.lower():
            event_date = (date.today() + timedelta(days=1)).isoformat()
            cleaned = re.sub(r'\b(morgen|tomorrow)\b', '', cleaned, flags=re.IGNORECASE).strip()
        elif "overmorgen" in cleaned.lower():
            event_date = (date.today() + timedelta(days=2)).isoformat()
            cleaned = re.sub(r'\bovermorgen\b', '', cleaned, flags=re.IGNORECASE).strip()
        elif "vandaag" in cleaned.lower() or "today" in cleaned.lower():
            cleaned = re.sub(r'\b(vandaag|today)\b', '', cleaned, flags=re.IGNORECASE).strip()

        # Try to find explicit date (DD-MM, DD/MM, DD-MM-YYYY)
        date_match = re.search(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', cleaned)
        if date_match:
            day = int(date_match.group(1))
            month = int(date_match.group(2))
            year = int(date_match.group(3)) if date_match.group(3) else date.today().year
            if year < 100:
                year += 2000
            try:
                event_date = date(year, month, day).isoformat()
                cleaned = cleaned[:date_match.start()] + cleaned[date_match.end():]
            except ValueError:
                pass

        # Try to find time (HH:MM or "om HH uur" or "at HH")
        time_match = re.search(r'(\d{1,2})[:\.](\d{2})', cleaned)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                event_time = f"{hour:02d}:{minute:02d}"
                cleaned = cleaned[:time_match.start()] + cleaned[time_match.end():]
        else:
            # "om 14 uur" or "at 2 pm"
            hour_match = re.search(r'\b(?:om|at)\s+(\d{1,2})\s*(?:uur|u|pm|am)?\b', cleaned, re.IGNORECASE)
            if hour_match:
                hour = int(hour_match.group(1))
                if 0 <= hour <= 23:
                    event_time = f"{hour:02d}:00"
                    cleaned = cleaned[:hour_match.start()] + cleaned[hour_match.end():]

        # Clean up remaining prepositions and whitespace
        cleaned = re.sub(r'\b(op|om|at|on)\b', '', cleaned, flags=re.IGNORECASE)
        title = cleaned.strip().strip(".:,- ")

        return (title, event_date, event_time)

    def _extract_title_for_delete(self, text: str) -> str:
        """Extract the title to delete from the command."""
        prefixes = [
            "verwijder afspraak", "verwijder", "delete appointment",
            "delete", "annuleer", "cancel", "schrap",
        ]
        text_lower = text.lower()
        for prefix in sorted(prefixes, key=len, reverse=True):
            if text_lower.startswith(prefix):
                return text[len(prefix):].strip().strip(".:,")
        return text.strip()

    def _msg(self, en: str, nl: str, language: str) -> str:
        return nl if language == "nl" else en
