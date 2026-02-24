"""Tests for Ollama client."""

import json
import pytest
from unittest.mock import MagicMock, patch
from src.ai.ollama import OllamaClient


class TestOllamaClient:
    def test_init(self):
        client = OllamaClient(
            base_url="http://localhost:11434",
            model="test-model",
            temperature=0.5,
        )
        assert client.model == "test-model"
        assert client.temperature == 0.5

    def test_split_sentences_basic(self):
        client = OllamaClient()
        result = client._split_sentences("Hello world. How are you? I'm fine!")
        # Should have complete sentences + remainder
        assert len(result) >= 3

    def test_split_sentences_short(self):
        client = OllamaClient()
        result = client._split_sentences("Hi.")
        # Too short to split (< 10 chars)
        assert len(result) == 1

    def test_split_sentences_numbers(self):
        client = OllamaClient()
        result = client._split_sentences("The temperature is 23.5 degrees.")
        # Should NOT split on 23.5
        combined = "".join(result)
        assert "23.5" in combined

    def test_split_sentences_empty(self):
        client = OllamaClient()
        result = client._split_sentences("")
        assert result == [""]

    @patch("src.ai.ollama.requests.post")
    def test_chat_sync_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Hello there!"}
        }
        mock_post.return_value = mock_response

        client = OllamaClient()
        result = client.chat_sync([{"role": "user", "content": "Hi"}])
        assert result == "Hello there!"

    @patch("src.ai.ollama.requests.post")
    def test_chat_sync_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        client = OllamaClient()
        result = client.chat_sync([{"role": "user", "content": "Hi"}])
        assert result is None

    @patch("src.ai.ollama.requests.post")
    def test_stream_chat_collects_sentences(self, mock_post):
        # Simulate streaming response
        lines = [
            json.dumps({"message": {"content": "Hello "}, "done": False}).encode(),
            json.dumps({"message": {"content": "world. "}, "done": False}).encode(),
            json.dumps({"message": {"content": "How are "}, "done": False}).encode(),
            json.dumps({"message": {"content": "you?"}, "done": False}).encode(),
            json.dumps({"done": True}).encode(),
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = iter(lines)
        mock_post.return_value = mock_response

        client = OllamaClient()
        sentences = []
        client.stream_chat(
            [{"role": "user", "content": "Hi"}],
            on_sentence=lambda s: sentences.append(s),
        )
        # Should have collected at least 1 sentence
        assert len(sentences) >= 1
        combined = " ".join(sentences)
        assert "Hello" in combined

    @patch("src.ai.ollama.requests.get")
    def test_verify_connection_failure(self, mock_get):
        mock_get.side_effect = ConnectionError("No connection")
        client = OllamaClient()
        assert not client.verify_and_preload()
