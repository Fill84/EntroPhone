"""Persistent callback queue for deferred responses.

When Ollama takes too long or monitoring detects issues,
items are queued for callback. The callback worker processes
them and makes outgoing calls.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CallbackItem:
    """A single callback queue item."""
    number: str
    message: str
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "message": self.message,
            "timestamp": self.timestamp,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CallbackItem":
        return cls(
            number=data["number"],
            message=data["message"],
            timestamp=data.get("timestamp", time.time()),
            retry_count=data.get("retry_count", 0),
        )


class CallbackQueue:
    """Thread-safe, persistent callback queue."""

    MAX_SIZE = 50

    def __init__(self, persist_path: str = "/app/logs/callback_queue.json"):
        self.persist_path = persist_path
        self._lock = threading.Lock()
        self._items: List[CallbackItem] = []
        self._load()

    def add(self, number: str, message: str) -> bool:
        """Add a callback to the queue. Returns True if added."""
        with self._lock:
            if len(self._items) >= self.MAX_SIZE:
                logger.warning("Callback queue full (%d items)", self.MAX_SIZE)
                return False

            item = CallbackItem(number=number, message=message)
            self._items.append(item)
            self._save()
            logger.info("Callback queued: %s -> %s", number, message[:50])
            return True

    def pop(self) -> Optional[CallbackItem]:
        """Pop the next callback from the queue."""
        with self._lock:
            if not self._items:
                return None
            item = self._items.pop(0)
            self._save()
            return item

    def prepend(self, item: CallbackItem) -> None:
        """Put an item back at the front of the queue (for retries)."""
        with self._lock:
            self._items.insert(0, item)
            self._save()

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def list_all(self) -> List[dict]:
        """List all pending callbacks (for dashboard)."""
        with self._lock:
            return [item.to_dict() for item in self._items]

    def clear(self) -> int:
        """Clear all callbacks. Returns count of cleared items."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            self._save()
            return count

    def _load(self) -> None:
        try:
            path = Path(self.persist_path)
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._items = [CallbackItem.from_dict(d) for d in data]
                logger.info("Loaded %d callbacks from disk", len(self._items))
        except Exception as e:
            logger.warning("Failed to load callback queue: %s", e)
            self._items = []

    def _save(self) -> None:
        try:
            path = Path(self.persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in self._items], f, indent=2)
        except Exception as e:
            logger.error("Failed to save callback queue: %s", e)
