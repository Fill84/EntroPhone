"""Conversation state management.

Maintains conversation history per call with:
- Bilingual system prompts (Dutch + English)
- Language-sticky detection (remembers detected language)
- History trimming to prevent context overflow
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

def _build_system_prompt(lang: str, assistant_name: str = "ClaudeViool") -> str:
    """Build language-specific system prompt with assistant identity."""
    lang_label = "Nederlands" if lang == "nl" else "English"
    if lang == "nl":
        return (
            f"Je bent {assistant_name}, een slimme AI-assistent die via de telefoon spreekt. "
            f"De gebruiker spreekt nu {lang_label}. Antwoord ALTIJD in dezelfde taal als de gebruiker. "
            "REGELS: "
            "1) Houd je antwoorden kort en duidelijk: maximaal 2-3 zinnen. Dit is een telefoongesprek. "
            "2) Geef altijd accurate en feitelijke antwoorden. Als je iets niet zeker weet, zeg dat eerlijk. "
            "3) Je kunt helpen met: smart home bediening (lampen, schakelaars, thermostaat), "
            "server monitoring, agenda/planning, notities, muziek, en algemene kennisvragen. "
            "4) Wees natuurlijk en conversationeel, alsof je een echte persoon bent aan de telefoon. "
            "5) Gebruik geen opsommingstekens, markdown of andere opmaak - je spreekt via audio."
        )
    return (
        f"You are {assistant_name}, a smart AI assistant speaking with the user via telephone. "
        f"The user is currently speaking {lang_label}. ALWAYS respond in the same language as the user. "
        "RULES: "
        "1) Keep answers short and clear: maximum 2-3 sentences. This is a phone call. "
        "2) Always give accurate, factual answers. If you're not sure, say so honestly. "
        "3) You can help with: smart home control (lights, switches, thermostat), "
        "server monitoring, calendar/planning, notes, music, and general knowledge questions. "
        "4) Be natural and conversational, as if you're a real person on the phone. "
        "5) Do not use bullet points, markdown or other formatting - you are speaking via audio."
    )


class ConversationManager:
    """Manages conversation history and context for a single call."""

    def __init__(self, max_history: int = 20, assistant_name: str = "ClaudeViool"):
        self.max_history = max_history
        self.assistant_name = assistant_name
        self.history: List[dict] = []
        self.detected_language: Optional[str] = None

    def get_messages_for_ollama(self) -> List[dict]:
        """Build the message list for Ollama including system prompt and history."""
        lang = self.detected_language or "en"

        system_content = _build_system_prompt(lang, self.assistant_name)

        messages = [{"role": "system", "content": system_content}]
        # Include recent history for context (last 8 messages = 4 exchanges)
        messages.extend(self.history[-8:])
        return messages

    def add_exchange(self, user_text: str, assistant_text: str, language: Optional[str] = None) -> None:
        """Add a user-assistant exchange to history."""
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": assistant_text})

        # Trim history
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        # Update detected language (sticky)
        if language:
            self.detected_language = language

    def clear(self) -> None:
        """Clear conversation history."""
        self.history.clear()
        self.detected_language = None
