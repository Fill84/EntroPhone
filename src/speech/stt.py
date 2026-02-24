"""Speech-to-Text engine using faster-whisper with GPU acceleration.

Features:
- Constrained to Dutch (nl) and English (en) only
- GPU-accelerated (CUDA)
- VAD filtering with telephony-optimized thresholds
- Confidence threshold to reject noise/silence
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from ..config import get_path

logger = logging.getLogger(__name__)

# Minimum language detection confidence to accept a result
MIN_LANGUAGE_PROBABILITY = 0.4

# Whisper sometimes hallucinates on silence/noise. Reject these common artifacts.
HALLUCINATION_PATTERNS = {
    "thank you", "thanks for watching", "subscribe",
    "you", "bye", "the end", "...", "♪",
    "bedankt voor het kijken", "ondertiteling",
}


class STTEngine:
    """GPU-accelerated speech-to-text, constrained to Dutch + English."""

    # Persistent cache dir matching the Docker volume mount
    DEFAULT_CACHE_DIR = str(get_path("hf_cache"))

    def __init__(
        self,
        model_size: str = "medium",
        device: str = "cuda",
        compute_type: str = "auto",
        cache_dir: str = DEFAULT_CACHE_DIR,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cache_dir = cache_dir
        self.model = None

    def warmup(self) -> None:
        """Load the Whisper model into memory (and GPU if available)."""
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        """Lazy-load the model on first use."""
        if self.model is not None:
            return

        try:
            from faster_whisper import WhisperModel

            logger.info(
                "Loading Whisper model: %s (device=%s, compute=%s)",
                self.model_size, self.device, self.compute_type,
            )
            self.model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.cache_dir,
            )
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error("Failed to load Whisper model: %s", e)
            if self.device == "cuda":
                logger.info("Falling back to CPU...")
                try:
                    from faster_whisper import WhisperModel

                    self.model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                        download_root=self.cache_dir,
                    )
                    self.device = "cpu"
                    self.compute_type = "int8"
                    logger.info("Whisper model loaded on CPU (fallback)")
                except Exception as e2:
                    logger.error("CPU fallback also failed: %s", e2)
                    raise

    def transcribe(self, audio_file: str) -> Tuple[Optional[str], Optional[str]]:
        """Transcribe audio file. Tries Dutch first, falls back to English.

        Returns:
            Tuple of (transcribed_text, detected_language) or (None, None)
        """
        self._ensure_loaded()

        if not Path(audio_file).exists():
            logger.warning("Audio file not found: %s", audio_file)
            return None, None

        file_size = Path(audio_file).stat().st_size
        if file_size < 1000:
            logger.info("Audio file too small (%d bytes), skipping STT", file_size)
            return None, None

        vad_params = dict(
            min_silence_duration_ms=500,
            speech_pad_ms=400,
            threshold=0.3,  # Low threshold for 8kHz telephony
        )

        # --- First pass: force Dutch ---
        try:
            segments, info = self.model.transcribe(
                audio_file,
                beam_size=5,
                language="nl",  # Force Dutch first
                vad_filter=True,
                vad_parameters=vad_params,
            )
            text = " ".join(segment.text for segment in segments).strip()

            if text and not self._is_hallucination(text):
                lang_prob = info.language_probability if info else 0
                logger.info("STT [nl, prob=%.2f]: %s", lang_prob, text[:100])
                return text, "nl"

            if not text:
                logger.info("STT Dutch pass: no speech detected (VAD filtered all)")
        except Exception as e:
            logger.warning("Dutch transcription pass failed: %s", e)

        # --- Second pass: try English if Dutch gave nothing useful ---
        try:
            segments, info = self.model.transcribe(
                audio_file,
                beam_size=5,
                language="en",
                vad_filter=True,
                vad_parameters=vad_params,
            )
            text = " ".join(segment.text for segment in segments).strip()

            if not text:
                logger.info("STT English pass: no speech detected (VAD filtered all)")
                return None, None

            if self._is_hallucination(text):
                logger.info("STT rejected hallucination: '%s'", text)
                return None, None

            lang_prob = info.language_probability if info else 0
            logger.info("STT [en, prob=%.2f]: %s", lang_prob, text[:100])
            return text, "en"

        except Exception as e:
            logger.error("Transcription failed: %s", e)
            return None, None

    @staticmethod
    def _is_hallucination(text: str) -> bool:
        """Check if transcription is a known Whisper hallucination."""
        cleaned = text.strip().lower().rstrip(".")
        if cleaned in HALLUCINATION_PATTERNS:
            return True
        if len(cleaned) < 3:
            return True
        return False
