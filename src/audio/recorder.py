"""VAD-aware audio recorder for PJSIP.

Replaces the fixed-duration recording approach with intelligent
voice activity detection. Records only when the user is speaking
and stops automatically after silence is detected.
"""

import logging
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import pjsua2 as pj

from ..config import get_path
from .vad import SileroVAD

logger = logging.getLogger(__name__)


class VADRecorder:
    """VAD-based recorder that detects speech boundaries."""

    def __init__(self, config: dict):
        self.config = config
        self.vad = SileroVAD(
            threshold=config.get("threshold", 0.4),
            sample_rate=8000,
            min_silence_ms=config.get("min_silence_ms", 800),
            speech_pad_ms=config.get("speech_pad_ms", 300),
            min_speech_ms=config.get("min_speech_ms", 250),
        )
        self._vad_loaded = self.vad.load()

        if not self._vad_loaded:
            logger.warning("VAD not available, falling back to fixed-duration recording")

    def wait_for_utterance(
        self,
        call: pj.Call,
        disconnected_event: threading.Event,
        max_duration: float = 30.0,
        chunk_duration: float = 0.5,
    ) -> Optional[str]:
        """Wait for the user to speak and return the audio file.

        Uses short sequential recordings analyzed by VAD to detect
        speech start and end boundaries.

        Args:
            call: The active PJSIP call
            disconnected_event: Event set when call disconnects
            max_duration: Maximum recording time in seconds
            chunk_duration: Duration of each recording chunk in seconds

        Returns:
            Path to WAV file containing the utterance, or None if no speech detected
        """
        if not self._vad_loaded:
            return self._fixed_fallback(call, disconnected_event)

        self.vad.reset()

        speech_started = False
        speech_chunks = []
        silence_after_speech_ms = 0
        total_duration = 0
        min_silence_ms = self.config.get("min_silence_ms", 800)
        min_speech_ms = self.config.get("min_speech_ms", 250)
        speech_duration_ms = 0

        # Wait for speech, then record until silence
        while total_duration < max_duration:
            if disconnected_event.is_set():
                return None

            # Record a short chunk
            chunk_file = self._record_chunk(call, disconnected_event, chunk_duration)
            if chunk_file is None:
                return None

            total_duration += chunk_duration

            # Analyze chunk with VAD
            has_speech = self._analyze_chunk(chunk_file)

            if has_speech:
                speech_chunks.append(chunk_file)
                silence_after_speech_ms = 0

                if not speech_started:
                    speech_started = True
                    logger.debug("VAD: speech started (t=%.1fs)", total_duration)

                speech_duration_ms += chunk_duration * 1000

            elif speech_started:
                # Silence after speech
                speech_chunks.append(chunk_file)  # Keep for padding
                silence_after_speech_ms += chunk_duration * 1000

                if silence_after_speech_ms >= min_silence_ms:
                    logger.debug(
                        "VAD: speech ended (silence=%dms, speech=%dms)",
                        silence_after_speech_ms, speech_duration_ms,
                    )
                    break
            else:
                # No speech yet - discard chunk
                Path(chunk_file).unlink(missing_ok=True)

                # Timeout waiting for speech (10 seconds of pre-speech silence)
                if total_duration > 10.0:
                    logger.debug("VAD: no speech detected after %.1fs", total_duration)
                    return None

        if not speech_chunks or speech_duration_ms < min_speech_ms:
            # Too short - probably noise
            for f in speech_chunks:
                Path(f).unlink(missing_ok=True)
            return None

        # Concatenate speech chunks into one file
        output_file = str(get_path("audio_tmp") / f"utterance_{int(time.time()*1000)}.wav")
        self._concatenate_chunks(speech_chunks, output_file)

        # Cleanup chunk files
        for f in speech_chunks:
            Path(f).unlink(missing_ok=True)

        if Path(output_file).exists() and Path(output_file).stat().st_size > 1000:
            return output_file

        Path(output_file).unlink(missing_ok=True)
        return None

    def _record_chunk(
        self,
        call: pj.Call,
        disconnected_event: threading.Event,
        duration: float,
    ) -> Optional[str]:
        """Record a short audio chunk from the call."""
        try:
            ci = call.getInfo()
            aud_med = None
            for i, mi in enumerate(ci.media):
                if (
                    mi.type == pj.PJMEDIA_TYPE_AUDIO
                    and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE
                ):
                    aud_med = call.getAudioMedia(i)
                    break

            if aud_med is None:
                return None

            chunk_file = str(get_path("audio_tmp") / f"chunk_{int(time.time()*1000000)}.wav")
            recorder = pj.AudioMediaRecorder()
            recorder.createRecorder(chunk_file)
            time.sleep(0.05)  # Conference bridge settling

            transmitting = False
            try:
                aud_med.startTransmit(recorder)
                transmitting = True
            except Exception as e:
                logger.warning("startTransmit failed (call→recorder): %s", e)
                try:
                    del recorder
                except Exception:
                    pass
                Path(chunk_file).unlink(missing_ok=True)
                return None

            interrupted = disconnected_event.wait(timeout=duration)

            if transmitting:
                try:
                    aud_med.stopTransmit(recorder)
                except Exception:
                    pass

            # Let the recorder flush to disk
            time.sleep(0.05)
            del recorder

            if interrupted:
                Path(chunk_file).unlink(missing_ok=True)
                return None

            if Path(chunk_file).exists() and Path(chunk_file).stat().st_size > 100:
                return chunk_file

            Path(chunk_file).unlink(missing_ok=True)
            return None

        except Exception as e:
            logger.error("Chunk recording error: %s", e)
            return None

    def _read_pcm_from_wav(self, filename: str) -> bytes:
        """Read PCM data from a WAV file, handling PJSIP's non-standard format.

        PJSIP's AudioMediaRecorder may write WAVFORMATEXTENSIBLE headers that
        Python's wave module cannot parse. Fall back to manual chunk parsing.
        """
        # Try standard wave module first
        try:
            with wave.open(filename, "rb") as wf:
                return wf.readframes(wf.getnframes())
        except Exception:
            pass

        # Manual parsing: find the 'data' chunk in the RIFF file
        try:
            raw = Path(filename).read_bytes()
            idx = raw.find(b"data")
            if idx >= 0 and idx + 8 <= len(raw):
                data_size = struct.unpack_from("<I", raw, idx + 4)[0]
                data_start = idx + 8
                return raw[data_start : data_start + data_size]

            # Last resort: skip standard 44-byte WAV header
            if len(raw) > 44:
                return raw[44:]
        except Exception:
            pass

        return b""

    def _analyze_chunk(self, chunk_file: str) -> bool:
        """Analyze a WAV chunk with VAD. Returns True if speech detected."""
        try:
            raw_data = self._read_pcm_from_wav(chunk_file)
            if len(raw_data) < 1024:
                return False

            # Convert 16-bit PCM to float32
            samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0

            # Process in VAD chunk sizes (512 samples)
            chunk_size = self.vad.chunk_size
            speech_chunks = 0
            total_chunks = 0

            for i in range(0, len(samples) - chunk_size + 1, chunk_size):
                chunk = samples[i : i + chunk_size]
                total_chunks += 1
                if self.vad.is_speech(chunk):
                    speech_chunks += 1

            if total_chunks == 0:
                return False

            # Consider speech if >30% of chunks have speech
            speech_ratio = speech_chunks / total_chunks
            return speech_ratio > 0.3

        except Exception as e:
            logger.error("VAD analysis error: %s", e)
            return False

    def _concatenate_chunks(self, chunk_files: list, output_file: str) -> None:
        """Concatenate multiple WAV files into one."""
        try:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)

            # Read all chunks using our robust reader
            all_frames = b""
            for chunk_file in chunk_files:
                if not Path(chunk_file).exists():
                    continue
                pcm = self._read_pcm_from_wav(chunk_file)
                if pcm:
                    all_frames += pcm

            if not all_frames:
                return

            # Write as standard WAV: 8kHz, 16-bit, mono (PJSIP telephony format)
            with wave.open(output_file, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(8000)
                wf.writeframes(all_frames)

        except Exception as e:
            logger.error("Chunk concatenation error: %s", e)

    def _fixed_fallback(
        self,
        call: pj.Call,
        disconnected_event: threading.Event,
        duration: float = 6.0,
    ) -> Optional[str]:
        """Fallback: fixed-duration recording when VAD is unavailable."""
        return self._record_chunk(call, disconnected_event, duration)
