"""Tests for callback queue."""

import json
import tempfile
import pytest
from src.callback.queue import CallbackQueue, CallbackItem


class TestCallbackQueue:
    def test_add_and_pop(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cq = CallbackQueue(persist_path=f.name)

        cq.add("1234", "Test message")
        assert cq.size() == 1

        item = cq.pop()
        assert item is not None
        assert item.number == "1234"
        assert item.message == "Test message"
        assert cq.size() == 0

    def test_pop_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cq = CallbackQueue(persist_path=f.name)
        assert cq.pop() is None

    def test_max_size(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cq = CallbackQueue(persist_path=f.name)

        for i in range(50):
            assert cq.add(str(i), f"Message {i}")
        assert not cq.add("51", "Should fail")
        assert cq.size() == 50

    def test_prepend(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cq = CallbackQueue(persist_path=f.name)

        cq.add("1", "First")
        cq.add("2", "Second")

        item = cq.pop()
        assert item.number == "1"

        # Put it back at the front
        cq.prepend(item)
        item2 = cq.pop()
        assert item2.number == "1"

    def test_list_all(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cq = CallbackQueue(persist_path=f.name)

        cq.add("1", "First")
        cq.add("2", "Second")

        items = cq.list_all()
        assert len(items) == 2
        assert items[0]["number"] == "1"

    def test_clear(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cq = CallbackQueue(persist_path=f.name)

        cq.add("1", "First")
        cq.add("2", "Second")
        count = cq.clear()
        assert count == 2
        assert cq.size() == 0

    def test_persistence(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name

        cq1 = CallbackQueue(persist_path=path)
        cq1.add("1234", "Persist test")

        # Load from same file
        cq2 = CallbackQueue(persist_path=path)
        assert cq2.size() == 1
        item = cq2.pop()
        assert item.number == "1234"

    def test_callback_item_serialization(self):
        item = CallbackItem(number="999", message="Hello")
        d = item.to_dict()
        assert d["number"] == "999"
        assert d["message"] == "Hello"

        item2 = CallbackItem.from_dict(d)
        assert item2.number == "999"
        assert item2.message == "Hello"
