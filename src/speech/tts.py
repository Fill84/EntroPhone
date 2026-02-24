"""Text-to-Speech engine with bilingual support (Dutch + English).

Uses standalone Piper TTS binary for reliable synthesis.
The binary is self-contained (includes its own espeak-ng phonemizer)
so it works reliably in Docker without dependency issues.
Pre-generates common phrases at startup for instant playback.
"""

import hashlib
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Standalone Piper binary path (downloaded in Dockerfile)
PIPER_BIN = "/app/piper/piper"

# Common phrases pre-generated at startup (keyed by phrase_key)
COMMON_PHRASES = {
    # Greeting is now dynamic (uses caller name) — generated in call.py._speak_greeting()
    "anything_else": {
        "nl": "Kan ik je nog ergens mee helpen?",
        "en": "Can I help you with anything else?",
    },
    "goodbye": {
        "nl": "Oké, tot ziens! Fijne dag nog.",
        "en": "Okay, goodbye! Have a nice day.",
    },
    "no_input_prompt": {
        "nl": "Ben je er nog?",
        "en": "Are you still there?",
    },
    "no_input_goodbye": {
        "nl": "Ik hoor niets meer. Tot ziens!",
        "en": "I don't hear anything. Goodbye!",
    },
    "callback_notice": {
        "nl": "Ik heb je vraag genoteerd. Ik bel je zo terug met het antwoord.",
        "en": "I've noted your question. I'll call you back with the answer.",
    },
    "one_moment": {
        "nl": "Een moment alstublieft.",
        "en": "One moment please.",
    },
    "not_understood": {
        "nl": "Sorry, ik heb je niet goed verstaan. Kun je dat herhalen?",
        "en": "Sorry, I didn't catch that. Could you repeat that for me?",
    },
    "error": {
        "nl": "Sorry, er ging iets mis. Probeer het opnieuw.",
        "en": "Sorry, something went wrong. Please try again.",
    },
}


class TTSEngine:
    """Bilingual Piper TTS engine using standalone binary + subprocess."""

    def __init__(self, config: dict):
        self.config = config
        self.models_dir = Path("/app/models/piper")
        self.cache_dir = Path("/app/audio/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()  # Serialize Piper calls
        self._model_paths: Dict[str, str] = {}
        self._cache: Dict[str, str] = {}  # cache_id -> audio_file_path

        # Voice configuration
        voice_nl = config.get("voice_nl", "nathalie")
        voice_en = config.get("voice_en", "amy")
        self.voice_config = {
            "nl": {
                "voice": voice_nl,
                "quality": config.get("quality_nl", "medium"),
                "locale": self._locale_for_voice(voice_nl, "nl"),
            },
            "en": {
                "voice": voice_en,
                "quality": config.get("quality_en", "medium"),
                "locale": self._locale_for_voice(voice_en, "en"),
            },
        }

        self.volume_gain_db = config.get("volume_gain_db", 1.5)
        self.length_scale = config.get("length_scale", 1.1)
        self.noise_scale = config.get("noise_scale", 0.333)
        self.noise_w = config.get("noise_w", 0.333)

    @staticmethod
    def _locale_for_voice(voice_name: str, lang: str) -> str:
        """Map a voice name to its Piper locale prefix."""
        if voice_name in ("nathalie", "rdh"):
            return "nl_BE"
        if voice_name in ("mls", "pim", "ronnie", "alex", "mls_5809", "mls_7432"):
            return "nl_NL"
        if voice_name in ("amy", "ryan", "lessac", "libritts", "arctic"):
            return "en_US"
        if voice_name in ("alan", "alba", "cori"):
            return "en_GB"
        return {"nl": "nl_BE", "en": "en_US"}.get(lang, f"{lang}_{lang.upper()}")

    def warmup(self) -> None:
        """Check binary, discover models, pre-generate common phrases."""
        self._check_piper_binary()
        self._discover_models()
        self._pregenerate_common_phrases()

    def _check_piper_binary(self) -> None:
        """Verify standalone Piper binary is available and working."""
        if not Path(PIPER_BIN).exists():
            logger.error("Piper binary NOT found at %s", PIPER_BIN)
            return

        try:
            result = subprocess.run(
                [PIPER_BIN, "--version"],
                capture_output=True,
                timeout=5,
                env=self._piper_env(),
            )
            version = result.stdout.decode(errors="replace").strip()
            logger.info("Piper binary found: %s (version: %s)", PIPER_BIN, version or "unknown")
        except Exception as e:
            logger.warning("Piper binary check: %s (may still work)", e)

    def _piper_env(self) -> dict:
        """Environment variables for Piper binary (LD_LIBRARY_PATH for bundled libs)."""
        env = os.environ.copy()
        piper_dir = str(Path(PIPER_BIN).parent)
        piper_lib = piper_dir + "/lib"
        env["LD_LIBRARY_PATH"] = (
            piper_dir + ":" + piper_lib + ":" + env.get("LD_LIBRARY_PATH", "")
        )
        return env

    def _discover_models(self) -> None:
        """Find available Piper voice models."""
        if not self.models_dir.exists():
            logger.warning("Piper models directory not found: %s", self.models_dir)
            return

        for lang, vc in self.voice_config.items():
            qualities = self._quality_fallback(vc["quality"])
            found = False
            for quality in qualities:
                model_name = f"{vc['locale']}-{vc['voice']}-{quality}.onnx"
                model_path = self.models_dir / model_name
                if model_path.exists():
                    self._model_paths[lang] = str(model_path)
                    logger.info("TTS voice [%s]: %s", lang, model_name)
                    found = True
                    break

            if not found:
                for f in self.models_dir.glob(f"{vc['locale']}-*.onnx"):
                    if not str(f).endswith(".json"):
                        self._model_paths[lang] = str(f)
                        logger.info("TTS voice [%s] (fallback): %s", lang, f.name)
                        found = True
                        break

            if not found:
                logger.warning("No TTS voice found for language: %s", lang)

    @staticmethod
    def _quality_fallback(preferred: str) -> list:
        """Return quality list starting from preferred, falling back."""
        all_qualities = ["high", "medium", "low", "x_low"]
        if preferred in all_qualities:
            idx = all_qualities.index(preferred)
            return all_qualities[idx:]
        return all_qualities

    def _pregenerate_common_phrases(self) -> None:
        """Pre-generate all common phrases for instant playback."""
        count = 0
        for key, phrases in COMMON_PHRASES.items():
            for lang, text in phrases.items():
                cache_key = self._cache_key(text, lang)
                cache_path = self.cache_dir / f"{cache_key}.wav"

                if cache_path.exists() and cache_path.stat().st_size > 100:
                    self._cache[f"{key}_{lang}"] = str(cache_path)
                    count += 1
                    continue

                audio_file = self._synthesize(text, str(cache_path), language=lang)
                if audio_file:
                    self._cache[f"{key}_{lang}"] = audio_file
                    count += 1

        logger.info("Pre-generated %d common phrases", count)

    def speak(self, text: str, output_file: str, language: str = "en") -> Optional[str]:
        """Generate speech audio. Returns path to WAV file or None."""
        if not text or not text.strip():
            return None

        # Check cache
        cache_key = self._cache_key(text, language)
        cache_path = self.cache_dir / f"{cache_key}.wav"
        if cache_path.exists() and cache_path.stat().st_size > 100:
            return str(cache_path)

        return self._synthesize(text, output_file, language)

    def _synthesize(self, text: str, output_file: str, language: str = "en") -> Optional[str]:
        """Generate speech using standalone Piper binary + sox resample."""
        model_path = self._model_paths.get(language)
        if not model_path:
            model_path = next(iter(self._model_paths.values()), None)
        if not model_path:
            logger.error("No TTS model available for language: %s", language)
            return None

        # Piper writes native sample rate WAV (e.g., 22050Hz)
        raw_file = output_file + ".piper.wav"

        with self._lock:
            try:
                cmd = [
                    PIPER_BIN,
                    "--model", model_path,
                    "--output_file", raw_file,
                    "--length_scale", str(self.length_scale),
                    "--noise_scale", str(self.noise_scale),
                    "--noise_w", str(self.noise_w),
                    "--sentence_silence", "0.2",
                ]

                result = subprocess.run(
                    cmd,
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    env=self._piper_env(),
                )

                if result.returncode != 0:
                    stderr = result.stderr.decode(errors="replace")[:500]
                    logger.error("Piper failed (rc=%d): %s", result.returncode, stderr)
                    return self._speak_espeak(text, output_file)

                raw_size = Path(raw_file).stat().st_size if Path(raw_file).exists() else 0
                if raw_size < 100:
                    stderr = result.stderr.decode(errors="replace")[:500]
                    logger.error(
                        "Piper produced empty file (%d bytes). stderr: %s",
                        raw_size, stderr,
                    )
                    return self._speak_espeak(text, output_file)

                logger.debug("Piper synthesized %d bytes for: %s", raw_size, text[:60])

            except subprocess.TimeoutExpired:
                logger.error("Piper timeout for: %s", text[:50])
                return self._speak_espeak(text, output_file)
            except FileNotFoundError:
                logger.error("Piper binary not found at %s", PIPER_BIN)
                return self._speak_espeak(text, output_file)
            except Exception as e:
                logger.error("Piper error: %s", e, exc_info=True)
                return self._speak_espeak(text, output_file)

        return self._resample(raw_file, output_file)

    def _speak_espeak(self, text: str, output_file: str) -> Optional[str]:
        """Last-resort fallback TTS using espeak-ng."""
        raw_file = output_file + ".espeak.wav"
        try:
            subprocess.run(
                ["espeak-ng", "-w", raw_file, text],
                capture_output=True,
                timeout=10,
            )
            return self._resample(raw_file, output_file)
        except Exception as e:
            logger.error("espeak-ng fallback failed: %s", e)
            return None

    def _resample(self, input_file: str, output_file: str) -> Optional[str]:
        """Resample audio to 8kHz mono 16-bit PCM WAV for PJSIP telephony."""
        try:
            input_size = Path(input_file).stat().st_size if Path(input_file).exists() else 0
            if input_size < 100:
                logger.warning("Resample input too small (%d bytes), skipping", input_size)
                Path(input_file).unlink(missing_ok=True)
                return None

            sox_cmd = [
                "sox", input_file,
                "-r", "8000", "-c", "1", "-b", "16",
                "-e", "signed-integer",
                output_file,
                "lowpass", "3400",
            ]
            if self.volume_gain_db > 0:
                sox_cmd.extend(["gain", "-l", str(self.volume_gain_db)])

            result = subprocess.run(sox_cmd, capture_output=True, timeout=10)

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")[:300]
                logger.error("Sox failed (rc=%d): %s", result.returncode, stderr)
                Path(input_file).unlink(missing_ok=True)
                return None

            # Cleanup raw file
            Path(input_file).unlink(missing_ok=True)

            output_size = Path(output_file).stat().st_size if Path(output_file).exists() else 0
            if output_size > 100:
                return output_file

            logger.warning("Resampled file too small (%d bytes from %d bytes input)", output_size, input_size)
            return None

        except Exception as e:
            logger.error("Sox resampling exception: %s", e)
            Path(input_file).unlink(missing_ok=True)
            return None

    def get_cached_phrase(self, phrase_key: str, language: str = "en") -> Optional[str]:
        """Get a pre-cached common phrase audio file."""
        cache_id = f"{phrase_key}_{language}"
        path = self._cache.get(cache_id)
        if path and Path(path).exists():
            return path
        # Fallback to English
        cache_id = f"{phrase_key}_en"
        path = self._cache.get(cache_id)
        if path and Path(path).exists():
            return path
        return None

    def _cache_key(self, text: str, language: str) -> str:
        """Generate a cache key for text+language+settings.

        Includes TTS settings so cache auto-invalidates when config changes.
        """
        vc = self.voice_config.get(language, {})
        voice_id = f"{vc.get('locale', '')}-{vc.get('voice', '')}"
        content = (
            f"{voice_id}:{self.volume_gain_db}:{self.length_scale}"
            f":{self.noise_scale}:{self.noise_w}:{text.strip()}"
        )
        return hashlib.md5(content.encode()).hexdigest()
