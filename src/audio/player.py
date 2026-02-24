"""Audio player with streaming and barge-in support.

Plays audio files from a queue (sentence-by-sentence) while
monitoring for barge-in (user speaking during playback).
"""

import logging
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import pjsua2 as pj

logger = logging.getLogger(__name__)


class StreamingPlayer:
    """Plays audio files from a queue with barge-in detection."""

    def play_stream(
        self,
        audio_queue: queue.Queue,
        call: pj.Call,
        disconnected_event: threading.Event,
    ) -> bool:
        """Play audio files from a queue.

        Args:
            audio_queue: Queue of audio file paths (None = sentinel/done)
            call: Active PJSIP call
            disconnected_event: Set when call disconnects

        Returns:
            True if barge-in was detected, False if played to completion
        """
        while not disconnected_event.is_set():
            try:
                audio_file = audio_queue.get(timeout=15)
            except queue.Empty:
                break

            if audio_file is None:
                break

            if not Path(audio_file).exists():
                continue

            played = self._play_one(audio_file, call, disconnected_event)
            if not played:
                # Call ended or error during playback
                self._drain_queue(audio_queue)
                return False

        return False

    def _play_one(
        self,
        audio_file: str,
        call: pj.Call,
        disconnected_event: threading.Event,
    ) -> bool:
        """Play a single audio file. Returns True if played fully."""
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
                return False

            player = pj.AudioMediaPlayer()
            player.createPlayer(audio_file, pj.PJMEDIA_FILE_NO_LOOP)
            player.startTransmit(aud_med)

            # Calculate playback duration
            try:
                with wave.open(audio_file, "rb") as wf:
                    duration = wf.getnframes() / float(wf.getframerate())
            except Exception:
                duration = 3.0

            # Wait for playback (interruptible)
            interrupted = disconnected_event.wait(timeout=duration + 0.3)

            try:
                player.stopTransmit(aud_med)
            except Exception:
                pass

            time.sleep(0.15)  # Conference bridge settling

            return not interrupted

        except Exception as e:
            logger.error("Playback error for %s: %s", audio_file, e)
            return False

    def _drain_queue(self, audio_queue: queue.Queue) -> None:
        """Empty the queue (discard remaining items)."""
        while True:
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break
