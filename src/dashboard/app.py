"""Flask app factory with SocketIO for the ClaudePhone2 dashboard."""

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


def get_agent():
    return _agent


def get_config():
    return _config


def get_callback_queue():
    return _callback_queue


def get_call_logger():
    return _call_logger


def create_app(agent, config, callback_queue, call_logger=None) -> Flask:
    """Create and configure the Flask application."""
    global _agent, _config, _callback_queue, _call_logger

    _agent = agent
    _config = config
    _callback_queue = callback_queue
    _call_logger = call_logger

    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "claudephone2-dashboard"

    # Initialize SocketIO
    socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")

    # Register blueprints
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

    # Register SocketIO events
    from .audio_streamer import register_socket_events
    register_socket_events(socketio)

    return app


def init_dashboard(agent, config, callback_queue, call_logger=None, port=8080):
    """Initialize and start the dashboard in a background thread."""
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
