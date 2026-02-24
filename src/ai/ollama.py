"""Ollama streaming client for natural conversation.

Key improvement over old projects: streaming mode enabled.
Tokens are collected into sentences and pushed to TTS pipeline
as they complete, dramatically reducing perceived latency.
"""

import json
import logging
import re
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)


def clean_for_speech(text: str) -> str:
    """Strip markdown formatting from text before sending to TTS.

    Ollama often returns markdown despite system prompt instructions.
    Piper TTS would speak these literally ("sterretje sterretje" for **bold**).
    """
    if not text:
        return text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)           # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)                 # *italic*
    text = re.sub(r'__(.+?)__', r'\1', text)                 # __underline__
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.M)   # # headers
    text = re.sub(r'^\s*[\-\*]\s+', '', text, flags=re.M)   # - bullet lists
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.M)    # 1. numbered lists
    text = re.sub(r'`(.+?)`', r'\1', text)                   # `inline code`
    text = re.sub(r'```[\s\S]*?```', '', text)               # ```code blocks```
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)         # [link](url)
    text = re.sub(r'~~(.+?)~~', r'\1', text)                 # ~~strikethrough~~
    text = re.sub(r'\s{2,}', ' ', text)                      # collapse whitespace
    return text.strip()


class OllamaClient:
    """Streaming Ollama API client."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "glm-4.7-flash:latest",
        temperature: float = 0.7,
        max_tokens: int = 600,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def verify_and_preload(self) -> bool:
        """Verify Ollama is available and preload the model."""
        try:
            # Check connection
            r = requests.get(f"{self.base_url}/api/tags", timeout=10)
            if r.status_code != 200:
                logger.error("Ollama not available (status=%d)", r.status_code)
                return False

            models = r.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            logger.info("Available Ollama models: %s", model_names)

            # Preload model into memory without generating a response.
            # Large models (12B+) can take >90s to load from disk on first use.
            logger.info("Preloading model: %s", self.model)
            r = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": "hi",
                    "keep_alive": "10m",
                },
                timeout=300,
            )
            if r.status_code == 200:
                logger.info("Model %s preloaded successfully", self.model)
            else:
                logger.warning("Model preload returned status %d", r.status_code)
            return r.status_code == 200

        except requests.ConnectionError:
            logger.error("Cannot connect to Ollama at %s", self.base_url)
            return False
        except Exception as e:
            logger.error("Ollama verify failed: %s", e)
            return False

    def stream_chat(
        self,
        messages: List[dict],
        on_sentence: Callable[[str], None],
        timeout: Optional[float] = None,
    ) -> None:
        """Stream a chat response, calling on_sentence for each complete sentence.

        Args:
            messages: Chat messages (system + history + user)
            on_sentence: Callback called with each complete sentence
            timeout: Request timeout (raises TimeoutError if exceeded)
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        buffer = ""
        try:
            response = requests.post(
                url,
                json=payload,
                stream=True,
                timeout=timeout or self.timeout,
            )

            if response.status_code != 200:
                logger.error("Ollama stream failed (status=%d)", response.status_code)
                return
            for line in response.iter_lines():
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Check for completion
                if data.get("done", False):
                    break

                token = data.get("message", {}).get("content", "")
                if not token:
                    continue

                buffer += token

                # Split into sentences
                sentences = self._split_sentences(buffer)
                if len(sentences) > 1:
                    for complete_sentence in sentences[:-1]:
                        sentence = complete_sentence.strip()
                        if sentence:
                            on_sentence(sentence)
                    buffer = sentences[-1]

            # Flush remaining buffer
            if buffer.strip():
                on_sentence(buffer.strip())

        except requests.Timeout:
            logger.warning("Ollama stream timeout after %ds", timeout or self.timeout)
            # Flush what we have
            if buffer.strip():
                on_sentence(buffer.strip())
            raise TimeoutError("Ollama response timed out")

        except requests.ConnectionError:
            logger.error("Lost connection to Ollama")
        except Exception as e:
            logger.error("Ollama stream error: %s", e)

    def chat_sync(
        self,
        messages: List[dict],
        timeout: Optional[float] = None,
    ) -> Optional[str]:
        """Synchronous (non-streaming) chat. Used for callbacks.

        Default timeout is 2x the normal streaming timeout since callbacks
        are asynchronous and the caller isn't waiting on the line.
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        try:
            response = requests.post(
                # Callbacks are asynchronous (caller isn't waiting on the line),
                # so allow 4x the normal timeout for model processing.
                url, json=payload, timeout=timeout or (self.timeout * 4)
            )
            if response.status_code == 200:
                content = response.json().get("message", {}).get("content", "")
                return content.strip() if content else None
            return None

        except Exception as e:
            logger.error("Ollama sync chat error: %s", e)
            return None

    def _split_sentences(self, text: str) -> list:
        """Split text into sentences at natural boundaries.

        Splits on . ! ? but not on abbreviations, numbers, etc.
        Minimum sentence length of 10 chars to avoid one-word splits.
        """
        if not text:
            return [""]

        sentences = []
        current = ""

        for i, char in enumerate(text):
            current += char

            if char in ".!?":
                # Don't split on decimal numbers (1.5, 3.14)
                if char == "." and i > 0 and text[i - 1].isdigit():
                    continue
                # Don't split on abbreviations (Dr., Mr., etc.)
                if char == "." and len(current.strip()) < 4:
                    continue
                # Don't split on very short fragments
                if len(current.strip()) >= 10:
                    sentences.append(current)
                    current = ""

            # Also split on long sentences at commas (>100 chars)
            elif char == "," and len(current.strip()) > 100:
                sentences.append(current)
                current = ""

        # Always keep the remaining buffer
        sentences.append(current)
        return sentences
