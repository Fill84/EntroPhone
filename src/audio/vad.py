"""Voice Activity Detection using Silero VAD.

Supports 8kHz audio (native telephony format) with ONNX Runtime
for lightweight operation without requiring PyTorch.

Auto-detects model version (v4 uses h/c states, v5 uses combined state).
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class SileroVAD:
    """Silero VAD wrapper for 8kHz telephony audio."""

    def __init__(
        self,
        threshold: float = 0.4,
        sample_rate: int = 8000,
        min_silence_ms: int = 800,
        speech_pad_ms: int = 300,
        min_speech_ms: int = 250,
    ):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.min_silence_ms = min_silence_ms
        self.speech_pad_ms = speech_pad_ms
        self.min_speech_ms = min_speech_ms

        # Silero VAD expects specific chunk sizes:
        # 8kHz: 256 samples (32ms) or 512 samples (64ms)
        self.chunk_size = 512  # 64ms at 8kHz
        self.chunk_duration_ms = (self.chunk_size / self.sample_rate) * 1000

        self._model = None
        self._use_torch = False
        self._use_split_state = False  # v4 uses h/c, v5 uses combined state

    def load(self) -> bool:
        """Load the Silero VAD model."""
        # Try ONNX Runtime first (lighter)
        if self._try_load_onnx():
            return True
        # Fallback to PyTorch
        if self._try_load_torch():
            return True
        logger.error("Failed to load Silero VAD (no ONNX or PyTorch available)")
        return False

    def _try_load_onnx(self) -> bool:
        """Try loading Silero VAD via ONNX Runtime."""
        try:
            import onnxruntime as ort

            # Look for pre-downloaded model
            model_paths = [
                Path("/app/models/silero_vad.onnx"),
                Path.home() / ".cache" / "silero_vad" / "silero_vad.onnx",
            ]

            model_path = None
            for p in model_paths:
                if p.exists():
                    model_path = str(p)
                    break

            if model_path is None:
                logger.debug("Silero VAD ONNX model not found")
                return False

            self._ort_session = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"],
            )

            # Auto-detect model version from input names
            input_names = {inp.name for inp in self._ort_session.get_inputs()}
            input_meta = {inp.name: inp for inp in self._ort_session.get_inputs()}

            logger.info("Silero VAD ONNX inputs: %s", input_names)

            if "h" in input_names and "c" in input_names:
                # v4 model: separate h (hidden) and c (cell) LSTM states
                self._use_split_state = True
                h_shape = self._resolve_shape(input_meta["h"].shape)
                c_shape = self._resolve_shape(input_meta["c"].shape)
                self._ort_h = np.zeros(h_shape, dtype=np.float32)
                self._ort_c = np.zeros(c_shape, dtype=np.float32)
                logger.info(
                    "Silero VAD loaded (ONNX v4, h=%s, c=%s)", h_shape, c_shape
                )
            elif "state" in input_names:
                # v5+ model: combined state tensor
                self._use_split_state = False
                state_shape = self._resolve_shape(input_meta["state"].shape)
                self._ort_state = np.zeros(state_shape, dtype=np.float32)
                logger.info(
                    "Silero VAD loaded (ONNX v5+, state=%s)", state_shape
                )
            else:
                logger.error("Unknown Silero VAD model format, inputs: %s", input_names)
                return False

            self._ort_sr = np.array(self.sample_rate, dtype=np.int64)
            self._use_torch = False
            return True

        except Exception as e:
            logger.debug("ONNX Runtime load failed: %s", e)
            return False

    def _resolve_shape(self, shape) -> tuple:
        """Convert ONNX shape (may contain dynamic dims) to concrete tuple."""
        return tuple(s if isinstance(s, int) and s > 0 else 1 for s in shape)

    def _try_load_torch(self) -> bool:
        """Try loading Silero VAD via PyTorch."""
        try:
            import torch

            model, utils = torch.hub.load(
                "snakers4/silero-vad",
                "silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            self._model = model
            self._use_torch = True
            logger.info("Silero VAD loaded (PyTorch)")
            return True

        except Exception as e:
            logger.debug("PyTorch load failed: %s", e)
            return False

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if an audio chunk contains speech.

        Args:
            audio_chunk: numpy array of float32 audio samples, 512 samples at 8kHz
        """
        if len(audio_chunk) != self.chunk_size:
            # Pad or truncate to expected size
            if len(audio_chunk) < self.chunk_size:
                audio_chunk = np.pad(audio_chunk, (0, self.chunk_size - len(audio_chunk)))
            else:
                audio_chunk = audio_chunk[: self.chunk_size]

        # Normalize to float32 [-1, 1]
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32)
        if np.max(np.abs(audio_chunk)) > 1.0:
            audio_chunk = audio_chunk / 32768.0

        if self._use_torch:
            return self._is_speech_torch(audio_chunk)
        else:
            return self._is_speech_onnx(audio_chunk)

    def _is_speech_torch(self, audio_chunk: np.ndarray) -> bool:
        """Check speech using PyTorch model."""
        import torch

        tensor = torch.from_numpy(audio_chunk)
        prob = self._model(tensor, self.sample_rate).item()
        return prob > self.threshold

    def _is_speech_onnx(self, audio_chunk: np.ndarray) -> bool:
        """Check speech using ONNX Runtime."""
        try:
            input_data = audio_chunk.reshape(1, -1)

            if self._use_split_state:
                # v4: separate h and c inputs
                ort_inputs = {
                    "input": input_data,
                    "h": self._ort_h,
                    "c": self._ort_c,
                    "sr": self._ort_sr,
                }
                ort_outputs = self._ort_session.run(None, ort_inputs)
                prob = ort_outputs[0].item()
                self._ort_h = ort_outputs[1]
                self._ort_c = ort_outputs[2]
            else:
                # v5+: combined state
                ort_inputs = {
                    "input": input_data,
                    "state": self._ort_state,
                    "sr": self._ort_sr,
                }
                ort_outputs = self._ort_session.run(None, ort_inputs)
                prob = ort_outputs[0].item()
                self._ort_state = ort_outputs[1]

            return prob > self.threshold
        except Exception as e:
            logger.error("ONNX VAD inference failed: %s", e)
            return False

    def reset(self) -> None:
        """Reset VAD state (call between utterances)."""
        if self._use_torch and self._model:
            self._model.reset_states()
        elif self._use_split_state:
            self._ort_h = np.zeros(self._ort_h.shape, dtype=np.float32)
            self._ort_c = np.zeros(self._ort_c.shape, dtype=np.float32)
        elif hasattr(self, "_ort_state"):
            self._ort_state = np.zeros(self._ort_state.shape, dtype=np.float32)
