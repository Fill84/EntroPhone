"""Flask app factory with SocketIO for the ClaudePhone dashboard."""

import logging
import threading
from typing import Optional

from flask import Flask
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

socketio = SocketIO()

# Shared references (set during init)
_agent = None
_config = None
_callback_queue = None
_call_logger = None
_db = None

# Event signaled when setup is completed via the wizard
_setup_event = threading.Event()


def get_agent():
    return _agent


def get_config():
    return _config


def get_callback_queue():
    return _callback_queue


def get_call_logger():
    return _call_logger


def get_db():
    """Get the database instance (available even before agent exists)."""
    if _db:
        return _db
    agent = get_agent()
    if agent:
        return getattr(agent, '_db', None)
    return None


def set_agent(agent):
    """Set the agent reference after components are initialized."""
    global _agent, _config, _callback_queue, _call_logger
    _agent = agent
    _config = getattr(agent, 'config', _config)
    _callback_queue = getattr(agent, 'callback_queue', _callback_queue)
    _call_logger = getattr(agent, 'call_logger', _call_logger)
    logger.info("Dashboard agent reference updated")


def signal_setup_complete():
    """Signal that setup has been completed (unblocks the main thread)."""
    _setup_event.set()


def get_setup_event() -> threading.Event:
    """Get the setup event for waiting."""
    return _setup_event


def _create_app_with_blueprints() -> Flask:
    """Create Flask app and register all blueprints."""
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "claudephone-dashboard"

    socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")

    from .routes import main_bp
    from .api_config import config_bp
    from .api_tests import tests_bp
    from .api_models import models_bp
    from .api_calls import calls_bp
    from .api_system import system_bp
    from .api_data import data_bp
    from .api_plugins import plugins_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(config_bp, url_prefix="/api/config")
    app.register_blueprint(tests_bp, url_prefix="/api/test")
    app.register_blueprint(models_bp, url_prefix="/api/models")
    app.register_blueprint(calls_bp, url_prefix="/api/calls")
    app.register_blueprint(system_bp, url_prefix="/api/system")
    app.register_blueprint(data_bp, url_prefix="/api/data")
    app.register_blueprint(plugins_bp, url_prefix="/api/plugins")

    from .audio_streamer import register_socket_events
    register_socket_events(socketio)

    return app


def create_app(agent, config, callback_queue, call_logger=None) -> Flask:
    """Create and configure the Flask application (legacy, with agent)."""
    global _agent, _config, _callback_queue, _call_logger

    _agent = agent
    _config = config
    _callback_queue = callback_queue
    _call_logger = call_logger

    return _create_app_with_blueprints()


def init_dashboard_early(db, port=8080):
    """Start the dashboard BEFORE agent is available (setup mode).

    All blueprints are registered. Routes that need the agent will
    gracefully return 503 when agent is None.
    """
    global _db
    _db = db

    app = _create_app_with_blueprints()

    thread = threading.Thread(
        target=lambda: socketio.run(
            app, host="0.0.0.0", port=port,
            debug=False, use_reloader=False, allow_unsafe_werkzeug=True,
        ),
        daemon=True,
        name="dashboard",
    )
    thread.start()
    logger.info("Dashboard started on port %d (setup mode)", port)


def init_dashboard(agent, config, callback_queue, call_logger=None, port=8080):
    """Initialize and start the dashboard in a background thread."""
    global _agent, _config, _callback_queue, _call_logger

    _agent = agent
    _config = config
    _callback_queue = callback_queue
    _call_logger = call_logger

    # If dashboard is already running (from init_dashboard_early), just update refs
    if _db is not None:
        logger.info("Dashboard already running, agent reference updated")
        return

    app = create_app(agent, config, callback_queue, call_logger)

    thread = threading.Thread(
        target=lambda: socketio.run(
            app, host="0.0.0.0", port=port,
            debug=False, use_reloader=False, allow_unsafe_werkzeug=True,
        ),
        daemon=True,
        name="dashboard",
    )
    thread.start()
    logger.info("Dashboard started on port %d (with SocketIO)", port)
