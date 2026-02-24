# ClaudePhone2

A self-hosted, natural SIP voice assistant that answers phone calls with AI. Built with PJSIP, Whisper STT, Piper TTS, and Ollama.

## Features

- **Natural phone conversations** in Dutch and English with automatic language detection
- **Speech-to-Text** via faster-whisper with GPU acceleration (CUDA)
- **Text-to-Speech** via Piper with bilingual voice support
- **AI responses** via Ollama (any local LLM model)
- **Plugin system** for extending functionality (Home Assistant, monitoring, custom integrations)
- **Web dashboard** with real-time status, configuration, call history, and system monitoring
- **Built-in integrations**: Calendar, Notes, Media Player
- **Voice Activity Detection** with Silero VAD
- **Callback system** for deferred responses
- **100% self-hosted** — no cloud dependencies

## Architecture

```
Phone Call → FreePBX/SIP → PJSIP → VAD → Whisper STT → Ollama LLM → Piper TTS → Audio Response
                                                           ↕
                                              Plugins / Integrations
```

## Requirements

- Docker with NVIDIA GPU support (for CUDA-accelerated STT)
- A SIP server (e.g., FreePBX, Asterisk)
- Ollama running locally or on your network
- Piper TTS voice models

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/Fill84/ClaudePhone.git
cd ClaudePhone
cp .env.example .env
```

Edit `.env` with your SIP credentials, Ollama URL, and other settings.

### 2. Build and run

```bash
docker compose up --build -d
```

### 3. Access the dashboard

Open `http://localhost:8080` in your browser. The setup wizard will guide you through initial configuration.

## Configuration

All configuration is done via environment variables in `.env`. The dashboard provides a Config tab for editing these values at runtime.

### Core Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `SIP_SERVER` | SIP server hostname | *required* |
| `SIP_USERNAME` | SIP account username | *required* |
| `SIP_PASSWORD` | SIP account password | *required* |
| `OLLAMA_BASE_URL` | Ollama API URL | `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | LLM model name | `llama3.2:1b` |
| `STT_MODEL_SIZE` | Whisper model size | `medium` |
| `STT_DEVICE` | STT device (cuda/cpu) | `cuda` |
| `DASHBOARD_PORT` | Dashboard web port | `8080` |

See [.env.example](.env.example) for all available settings.

## Dashboard

The web dashboard (port 8080) provides:

- **Overview** — SIP status, components, active integrations
- **Tests** — Component connectivity tests
- **Config** — Edit configuration values with hot-reload support
- **Plugins** — Install, enable/disable, and configure plugins
- **Models** — Switch Ollama models and TTS voices
- **Calls** — Call history with transcripts and recordings
- **Integrations** — Built-in integrations (Notes, Calendar, Media)
- **System** — CPU, memory, disk, GPU monitoring, and logs

## Plugin System

ClaudePhone2 has a plugin system for adding new voice capabilities. Plugins can:

- Add custom voice commands (keywords) in Dutch and English
- Integrate with external services
- Have their own configuration fields
- Be installed from GitHub via the dashboard

### Installing Plugins

**Via dashboard:** Go to the Plugins tab, enter a GitHub URL, and click Install.

**Manually:** Place a `plugin_*.py` file in `src/plugins/` and restart.

### Creating Plugins

See [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) for the full plugin development guide.

### Built-in Plugins

- **Home Assistant** — Control lights, thermostat, curtains, and more via voice
- **Server Monitoring** — Check server status via ping

## Built-in Integrations

These are always available and don't require plugins:

- **Calendar** — Schedule events via voice ("plan een afspraak morgen om 14 uur")
- **Notes** — Save notes and reminders ("onthoud dat ik melk moet kopen")
- **Media** — Control media playback via Home Assistant ("speel muziek")

## Docker Volumes

| Mount | Purpose |
|-------|---------|
| `./logs:/app/logs` | Application logs and call queue |
| `./audio:/app/audio` | TTS cache, recordings |
| `./data:/app/data` | SQLite database |
| `./.env:/app/.env` | Configuration file |

## Tech Stack

- **Python 3.11** with PJSIP bindings
- **faster-whisper** for GPU-accelerated speech recognition
- **Piper TTS** (standalone binary) for text-to-speech
- **Silero VAD** for voice activity detection
- **Ollama** for local LLM inference
- **Flask + SocketIO** for the web dashboard
- **SQLite** for persistent data storage
- **Docker** with NVIDIA GPU passthrough

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
