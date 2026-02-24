"""ClaudePhone2 - Natural SIP Voice Agent.

Entry point: initializes all components and starts the SIP agent.
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Setup logging first
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/claudephone2.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("ClaudePhone2 - Natural SIP Voice Agent starting...")
    logger.info("=" * 60)

    # Load environment
    env_path = Path("/app/.env")
    if env_path.exists():
        load_dotenv(str(env_path))
    else:
        load_dotenv()

    from .config import get_config, validate_config

    config = get_config()
    errors = validate_config(config)
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        sys.exit(1)

    logger.info("Configuration loaded successfully")

    # Initialize components
    tts = _init_tts(config)
    stt = _init_stt(config)
    vad_recorder = _init_vad(config)
    ollama = _init_ollama(config)
    router = _init_router()
    callback_queue = _init_callback_queue()
    call_logger = _init_call_logger()
    conversation_factory = _init_conversation_factory(config)

    # Initialize plugin system
    plugin_manager, db, integrations = _init_plugins(
        config, ollama, tts, callback_queue, call_logger, router)

    # Start SIP agent
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

    # Start dashboard
    try:
        from .dashboard.app import init_dashboard

        init_dashboard(agent, config, callback_queue, call_logger=call_logger,
                       port=config["dashboard"]["port"])
    except Exception as e:
        logger.warning("Dashboard init failed: %s", e)

    logger.info("All components initialized, starting SIP agent...")
    agent.start()  # Blocks here


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

        cq = CallbackQueue(persist_path="/app/logs/callback_queue.json")
        logger.info("Callback queue initialized (%d pending)", cq.size())
        return cq
    except Exception as e:
        logger.warning("Callback queue init failed: %s", e)
        return None


def _init_plugins(config, ollama, tts, callback_queue, call_logger, router):
    """Initialize plugin system: discover plugins, load built-ins, wire router."""
    from .plugins.manager import PluginManager
    from .ai.categories import register_builtin_categories, register_categories

    # Initialize shared database
    db = None
    try:
        from .database import Database
        db = Database()
        logger.info("SQLite database initialized")
    except Exception as e:
        logger.warning("Database init failed: %s", e)

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

    # Media (depends on HA plugin handler)
    ha_plugin = pm.plugins.get("homeassistant")
    if ha_plugin and pm.is_enabled("homeassistant") and hasattr(ha_plugin, "_handler") and ha_plugin._handler:
        try:
            from .integrations.media_agent import MediaHandler
            integrations["media"] = MediaHandler(ha_plugin._handler)
            logger.info("Media integration active (via HA plugin)")
        except Exception as e:
            logger.warning("Media init failed: %s", e)

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
        router.register_plugin_keywords("media", {
            "nl": ["muziek", "speel", "afspelen", "stop", "pauze",
                    "pauzeer", "volume", "harder", "zachter",
                    "volgend", "vorig", "liedje", "nummer"],
            "en": ["music", "play", "stop", "pause",
                    "volume", "louder", "quieter", "softer",
                    "next", "previous", "song", "track"],
        })
        router.register_category_names("calendar", {
            "nl": ["agenda", "kalender", "planning"],
            "en": ["calendar", "agenda", "schedule"],
        })
        router.register_category_names("notes", {
            "nl": ["notities", "taken"],
            "en": ["notes", "tasks"],
        })
        router.register_category_names("media", {
            "nl": ["media", "muziek"],
            "en": ["media", "music"],
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

        assistant_name = config.get("assistant", {}).get("name", "ClaudeViool")

        def factory():
            return ConversationManager(max_history=20, assistant_name=assistant_name)

        return factory
    except Exception as e:
        logger.warning("Conversation manager init failed: %s", e)
        return None


if __name__ == "__main__":
    main()
