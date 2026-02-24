"""ClaudePhone - Natural SIP Voice Agent.

Entry point: starts dashboard first, waits for setup if needed,
then initializes all components and starts the SIP agent.
"""

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Setup logging: file gets everything, console shows our INFO + external WARNING+
_log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"


class _ConsoleFilter(logging.Filter):
    """Show INFO+ from our app, WARNING+ from external libraries."""
    def filter(self, record):
        if record.name.startswith("src."):
            return True
        return record.levelno >= logging.WARNING


# RotatingFileHandler: max 10MB per file, keep 5 backups (50MB total)
# Note: can't use get_path() here yet (module-level), but APP_ROOT is available
_log_dir = Path(os.environ.get("APP_ROOT", "/app")) / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    str(_log_dir / "claudephone.log"), encoding="utf-8",
    maxBytes=10 * 1024 * 1024, backupCount=5,
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(_log_format))

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(_log_format))
_console_handler.addFilter(_ConsoleFilter())

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])

logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("ClaudePhone - Natural SIP Voice Agent starting...")
    logger.info("=" * 60)

    # Step 1: Load .env for backward compatibility
    from .config import get_path
    env_path = get_path("env_file")
    if env_path.exists():
        load_dotenv(str(env_path))
    else:
        load_dotenv()

    # Step 2: Initialize database EARLY (before anything else)
    from .database import Database
    try:
        db = Database()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("FATAL: Database init failed: %s", e)
        sys.exit(1)

    # Step 3: Import .env values into DB (migration)
    from .config import import_env_to_db, check_required_settings

    imported = import_env_to_db(db)
    if imported:
        logger.info("Imported %d settings from .env to database", imported)

    # Step 4: Check if setup is complete
    setup_complete = db.is_setup_complete()
    if not setup_complete:
        # Auto-complete setup if all required settings are already in DB
        if check_required_settings(db):
            db.mark_setup_complete()
            setup_complete = True
            logger.info("All required settings found, setup auto-completed")

    # Step 5: Get dashboard port (from DB or env, with fallback)
    dashboard_port_str = db.get_setting("DASHBOARD_PORT") or os.getenv("DASHBOARD_PORT", "8080")
    try:
        dashboard_port = int(dashboard_port_str)
    except (ValueError, TypeError):
        dashboard_port = 8080

    # Step 6: Start dashboard ALWAYS (even without full config)
    _start_dashboard_early(db, dashboard_port)

    # Step 7: If setup not complete, wait for it
    if not setup_complete:
        logger.info("=" * 60)
        logger.info("SETUP REQUIRED!")
        logger.info("Open http://localhost:%d to complete setup", dashboard_port)
        logger.info("=" * 60)
        _wait_for_setup()
        logger.info("Setup completed! Starting components...")

    # Step 8: Load config from DB
    from .config import load_config_from_db, set_config, validate_config

    config = load_config_from_db(db)
    set_config(config)

    errors = validate_config(config)
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.error("Fix configuration via dashboard at http://localhost:%d", dashboard_port)
        _wait_for_valid_config(db)
        config = load_config_from_db(db)
        set_config(config)

    logger.info("Configuration loaded successfully from database")

    # Step 9: Initialize all components
    tts = _init_tts(config)
    stt = _init_stt(config)
    vad_recorder = _init_vad(config)
    ollama = _init_ollama(config)
    router = _init_router()
    callback_queue = _init_callback_queue()
    call_logger = _init_call_logger()
    conversation_factory = _init_conversation_factory(config)

    # Step 10: Initialize plugin system (using existing db)
    plugin_manager, _, integrations = _init_plugins(
        config, ollama, tts, callback_queue, call_logger, router, db)

    # Step 11: Create SIP agent
    from .sip.agent import SIPVoiceAgent

    agent = SIPVoiceAgent(
        tts=tts,
        stt=stt,
        vad_recorder=vad_recorder,
        router=router,
        conversation_factory=conversation_factory,
        ollama=ollama,
        callback_queue=callback_queue,
        integrations=integrations,
    )

    # Inject references for dashboard and plugins
    agent.call_logger = call_logger
    agent._db = db
    agent._plugin_manager = plugin_manager

    # Step 12: Update dashboard with agent reference
    from .dashboard.app import set_agent
    set_agent(agent)

    # Step 13: Register plugin routes on the Flask app
    _register_plugin_routes(plugin_manager)

    logger.info("All components initialized, starting SIP agent...")
    agent.start()  # Blocks here


def _register_plugin_routes(pm):
    """Register plugin-provided Flask routes on the running app."""
    try:
        from .dashboard.app import get_flask_app
        from .dashboard.api_plugins import register_plugin_routes
        app = get_flask_app()
        if app is None:
            logger.warning("Flask app not available for plugin route registration")
            return
        register_plugin_routes(app, pm)
        logger.info("Plugin route registration completed")
    except Exception as e:
        logger.error("Plugin route registration failed: %s", e, exc_info=True)


def _start_dashboard_early(db, port):
    """Start the dashboard before agent exists (setup mode)."""
    try:
        from .dashboard.app import init_dashboard_early
        init_dashboard_early(db=db, port=port)
    except Exception as e:
        logger.warning("Dashboard init failed: %s", e)


def _wait_for_setup():
    """Block until setup is completed via the dashboard."""
    from .dashboard.app import get_setup_event
    event = get_setup_event()
    event.wait()


def _wait_for_valid_config(db):
    """Block until valid config is present in DB."""
    from .config import load_config_from_db, validate_config
    while True:
        time.sleep(5)
        config = load_config_from_db(db)
        if not validate_config(config):
            return


def _init_tts(config):
    """Initialize TTS engine with bilingual support."""
    try:
        from .speech.tts import TTSEngine

        tts = TTSEngine(config["tts"])
        tts.warmup()
        logger.info("TTS engine initialized")
        return tts
    except Exception as e:
        logger.warning("TTS init failed: %s (will be unavailable)", e)
        return None


def _init_stt(config):
    """Initialize STT engine with GPU support."""
    try:
        from .speech.stt import STTEngine

        stt = STTEngine(
            model_size=config["stt"]["model_size"],
            device=config["stt"]["device"],
            compute_type=config["stt"]["compute_type"],
        )
        stt.warmup()
        logger.info("STT engine initialized")
        return stt
    except Exception as e:
        logger.warning("STT init failed: %s (will be unavailable)", e)
        return None


def _init_vad(config):
    """Initialize VAD-based recorder."""
    try:
        from .audio.recorder import VADRecorder

        recorder = VADRecorder(config["vad"])
        logger.info("VAD recorder initialized")
        return recorder
    except Exception as e:
        logger.warning("VAD init failed: %s (falling back to fixed recording)", e)
        return None


def _init_ollama(config):
    """Initialize Ollama client and preload model."""
    try:
        from .ai.ollama import OllamaClient

        client = OllamaClient(
            base_url=config["ollama"]["base_url"],
            model=config["ollama"]["model"],
            temperature=config["ollama"]["temperature"],
            max_tokens=config["ollama"]["max_tokens"],
            timeout=config["ollama"]["timeout"],
        )
        if client.verify_and_preload():
            logger.info("Ollama model preloaded: %s", config["ollama"]["model"])
        else:
            logger.warning("Ollama preload failed, will retry on first use")
        return client
    except Exception as e:
        logger.warning("Ollama init failed: %s (will be unavailable)", e)
        return None


def _init_router():
    """Initialize intent router."""
    try:
        from .ai.router import IntentRouter

        router = IntentRouter()
        logger.info("Intent router initialized")
        return router
    except Exception as e:
        logger.warning("Router init failed: %s", e)
        return None


def _init_callback_queue():
    """Initialize callback queue."""
    try:
        from .callback.queue import CallbackQueue

        from .config import get_path
        cq = CallbackQueue(persist_path=str(get_path("callback_queue")))
        logger.info("Callback queue initialized (%d pending)", cq.size())
        return cq
    except Exception as e:
        logger.warning("Callback queue init failed: %s", e)
        return None


def _init_plugins(config, ollama, tts, callback_queue, call_logger, router, db):
    """Initialize plugin system using the already-initialized database."""
    from .plugins.manager import PluginManager
    from .ai.categories import register_builtin_categories, register_categories

    # Create plugin manager and context
    pm = PluginManager()
    pm.init_context(
        db=db, ollama=ollama, tts=tts,
        callback_queue=callback_queue, call_logger=call_logger,
        config=config,
    )

    # Discover and load plugins from src/plugins/
    loaded = pm.discover_and_load()
    logger.info("Plugins loaded: %s", ", ".join(loaded) if loaded else "none")

    # Get plugin-provided integrations
    integrations = pm.get_integrations_dict()

    # Register plugin keywords and categories in router
    if router:
        router.register_from_plugin_manager(pm)

    # Register plugin categories
    register_categories(pm.get_all_categories())

    # --- Built-in integrations (not plugins, always available) ---

    # Register built-in categories
    register_builtin_categories()

    # Calendar (local SQLite)
    if db:
        try:
            from .integrations.calendar_agent import CalendarHandler
            integrations["calendar"] = CalendarHandler(db)
            logger.info("Calendar integration active (SQLite)")
        except Exception as e:
            logger.warning("Calendar init failed: %s", e)

    # Notes (SQLite)
    if db:
        try:
            from .integrations.notes_agent import NotesHandler
            integrations["notes"] = NotesHandler(db)
            logger.info("Notes integration active (SQLite)")
        except Exception as e:
            logger.warning("Notes init failed: %s", e)

    # Register built-in keywords in router
    if router:
        router.register_plugin_keywords("calendar", {
            "nl": ["agenda", "afspraak", "afspraken", "kalender",
                    "planning", "schema", "wanneer", "gepland"],
            "en": ["calendar", "appointment", "appointments", "schedule",
                    "agenda", "event", "events", "meeting", "planned"],
        })
        router.register_plugin_keywords("notes", {
            "nl": ["notitie", "notities", "onthoud", "onthouden",
                    "taak", "taken", "todo", "herinner", "herinnering",
                    "opschrijven", "noteren", "boodschappen"],
            "en": ["note", "notes", "remember", "task", "tasks",
                    "todo", "remind", "reminder", "write down",
                    "shopping list", "grocery"],
        })
        router.register_category_names("calendar", {
            "nl": ["agenda", "kalender", "planning"],
            "en": ["calendar", "agenda", "schedule"],
        })
        router.register_category_names("notes", {
            "nl": ["notities", "taken"],
            "en": ["notes", "tasks"],
        })
    return pm, db, integrations


def _init_call_logger():
    """Initialize call logger for recording call history."""
    try:
        from .dashboard.call_logger import CallLogger

        cl = CallLogger()
        logger.info("Call logger initialized")
        return cl
    except Exception as e:
        logger.warning("Call logger init failed: %s", e)
        return None


def _init_conversation_factory(config):
    """Return a factory function that creates ConversationManager instances."""
    try:
        from .ai.conversation import ConversationManager

        assistant_name = config.get("assistant", {}).get("name", "ClaudePhone")

        def factory():
            return ConversationManager(max_history=20, assistant_name=assistant_name)

        return factory
    except Exception as e:
        logger.warning("Conversation manager init failed: %s", e)
        return None


if __name__ == "__main__":
    main()
