"""Category definitions with sub-commands for voice menu navigation.

When a user says just a category name (e.g. "smart home"), the assistant
reads out the available sub-commands. When they say a direct command
(e.g. "doe lampen uit"), it bypasses the menu and executes directly.

Categories are registered dynamically by plugins and built-in integrations.
"""

# Mutable registry — populated at startup by plugins and built-ins
_CATEGORIES = {}

# Built-in categories for core integrations (calendar, notes)
_BUILTIN_CATEGORIES = {
    "calendar": {
        "nl": {
            "name": "Agenda",
            "options": [
                "Afspraken van vandaag bekijken",
                "Afspraak toevoegen",
                "Afspraak verwijderen",
                "Afspraken van morgen bekijken",
            ],
        },
        "en": {
            "name": "Calendar",
            "options": [
                "View today's appointments",
                "Add an appointment",
                "Remove an appointment",
                "View tomorrow's appointments",
            ],
        },
    },
    "notes": {
        "nl": {
            "name": "Notities",
            "options": [
                "Iets onthouden of noteren",
                "Notities bekijken",
                "Notities wissen",
            ],
        },
        "en": {
            "name": "Notes",
            "options": [
                "Remember or note something",
                "View your notes",
                "Clear notes",
            ],
        },
    },
}


def register_categories(categories_dict: dict) -> None:
    """Register categories from plugin manager or other source."""
    _CATEGORIES.update(categories_dict)


def register_builtin_categories() -> None:
    """Register the built-in calendar and notes categories."""
    _CATEGORIES.update(_BUILTIN_CATEGORIES)


def get_category_menu(intent: str, language: str = "en") -> str:
    """Build a spoken menu of available sub-commands for a category."""
    cat = _CATEGORIES.get(intent)
    if not cat:
        return ""

    lang_data = cat.get(language, cat.get("en", {}))
    name = lang_data.get("name", intent)
    options = lang_data.get("options", [])

    if not options:
        return ""

    options_text = ", ".join(options[:-1])
    if len(options) > 1:
        if language == "nl":
            options_text += f", of {options[-1]}"
        else:
            options_text += f", or {options[-1]}"
    else:
        options_text = options[0]

    if language == "nl":
        return f"Bij {name} kun je kiezen uit: {options_text}. Wat wil je doen?"
    return f"For {name} you can choose: {options_text}. What would you like to do?"
