"""VoiceCall - handles a single call's lifecycle and conversation loop.

This is the most critical file in the project. It integrates:
- VAD-based listening (no fixed duration)
- STT with auto language detection
- Intent routing
- Streaming Ollama responses (sentence-by-sentence TTS)
- Barge-in detection
"""

import logging
import queue
import random
import shutil
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pjsua2 as pj

if TYPE_CHECKING:
    from .account import SIPAccount

logger = logging.getLogger(__name__)


class VoiceCall(pj.Call):
    """A single voice call with natural conversation loop."""

    def __init__(self, account: "SIPAccount", call_id: int):
        super().__init__(account, call_id)
        self.account = account
        self.call_active = False
        self.was_connected = False
        self._disconnected = threading.Event()
        self._call_done = threading.Event()
        self._conversation_thread: Optional[threading.Thread] = None

        # Components (set by agent before call starts)
        self.tts = None
        self.stt = None
        self.vad_recorder = None
        self.player = None
        self.router = None
        self.conversation = None
        self.ollama = None
        self.callback_queue = None
        self.integrations = {}
        self.call_logger = None

        # Call info
        self.caller_number = ""
        self.caller_name = ""
        self._call_log_id = None
        self.outgoing_message: Optional[str] = None
        self.outgoing_audio: Optional[str] = None

        # Audio
        self._call_timeline: list = []  # [{"type": "user"|"assistant", "file": path, "start": time}]

    def onCallState(self, prm: pj.OnCallStateParam) -> None:
        """Called when call state changes."""
        try:
            ci = self.getInfo()
        except Exception as e:
            logger.error("onCallState getInfo() failed: %s", e, exc_info=True)
            self.call_active = False
            self._disconnected.set()
            self._call_done.set()
            return

        state = ci.state
        logger.info("Call state: %s (code=%d)", ci.stateText, state)

        if state == pj.PJSIP_INV_STATE_CONFIRMED:
            self.was_connected = True
            self.call_active = True
            self._disconnected.clear()

            # Extract caller info from remote URI
            remote_uri = ci.remoteUri
            self.caller_number = self._extract_number(remote_uri)
            self.caller_name = self._extract_display_name(remote_uri)
            logger.info("Call confirmed with: %s (%s)", self.caller_name or self.caller_number, remote_uri)

            # Log call start
            if self.call_logger:
                direction = "outgoing" if self.outgoing_message else "incoming"
                self._call_log_id = self.call_logger.start_call(
                    self.caller_number, self.caller_name, direction
                )

            # Start conversation in separate thread
            if self.outgoing_message:
                t = threading.Thread(
                    target=self._play_outgoing_message, daemon=True, name="outgoing_msg"
                )
                t.start()
            else:
                self._conversation_thread = threading.Thread(
                    target=self._conversation_loop, daemon=True, name="conversation"
                )
                self._conversation_thread.start()

        elif state == pj.PJSIP_INV_STATE_DISCONNECTED:
            logger.info("Call disconnected (reason=%s)", ci.lastReason)
            self.call_active = False
            self._disconnected.set()
            self._call_done.set()

            # Log call end
            if self.call_logger and self._call_log_id:
                self.call_logger.end_call(self._call_log_id)

            self._cleanup()
            self.account.clear_current_call()

    def onCallMediaState(self, prm: pj.OnCallMediaStateParam) -> None:
        """Called when media state changes (RTP active/inactive)."""
        try:
            ci = self.getInfo()
            for i, mi in enumerate(ci.media):
                if mi.type == pj.PJMEDIA_TYPE_AUDIO:
                    if mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                        logger.info("Audio media active (slot %d)", i)
                    else:
                        logger.info("Audio media status: %d (slot %d)", mi.status, i)
        except Exception as e:
            logger.error("Error in onCallMediaState: %s", e)

    def _extract_number(self, uri: str) -> str:
        """Extract phone number from SIP URI like 'sip:1234@server'."""
        if "sip:" in uri:
            uri = uri.split("sip:")[1]
        if "@" in uri:
            uri = uri.split("@")[0]
        # Remove display name quotes and angle brackets
        uri = uri.strip('"').strip("<").strip(">").strip()
        return uri

    @staticmethod
    def _extract_display_name(uri: str) -> str:
        """Extract display name from SIP URI like '"John Doe" <sip:1234@server>'.

        FreePBX typically sets display names in the URI. Returns empty string
        if no display name found or if it's just a number.
        """
        if not uri:
            return ""
        # Format: "Display Name" <sip:number@server>
        if '"' in uri:
            parts = uri.split('"')
            if len(parts) >= 3:
                name = parts[1].strip()
                # Don't return if it's just a number (no real name)
                if name and not name.isdigit():
                    return name
        # Format: Display Name <sip:number@server> (without quotes)
        if "<" in uri:
            name = uri.split("<")[0].strip().strip('"')
            if name and not name.isdigit():
                return name
        return ""

    def _get_active_audio_media(self) -> Optional[pj.AudioMedia]:
        """Get the active audio media for this call."""
        try:
            ci = self.getInfo()
            for i, mi in enumerate(ci.media):
                if (
                    mi.type == pj.PJMEDIA_TYPE_AUDIO
                    and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE
                ):
                    return self.getAudioMedia(i)
        except Exception as e:
            logger.error("Failed to get audio media: %s", e)
        return None

    def _wait_for_media_active(self, timeout_sec: float = 10.0) -> bool:
        """Wait for RTP media to become active. Event-based, not polling."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline and self.call_active:
            aud_med = self._get_active_audio_media()
            if aud_med is not None:
                return True
            if self._disconnected.wait(timeout=0.2):
                return False
        logger.warning("Media not active after %.1fs (call_active=%s)", timeout_sec, self.call_active)
        return False

    def _play_audio(self, audio_file: str) -> bool:
        """Play an audio file to the caller. Returns True if played fully."""
        if not self.call_active or not Path(audio_file).exists():
            return False

        aud_med = self._get_active_audio_media()
        if aud_med is None:
            return False

        player = pj.AudioMediaPlayer()
        connected = False
        try:
            player.createPlayer(audio_file, pj.PJMEDIA_FILE_NO_LOOP)
            player.startTransmit(aud_med)
            connected = True

            # Calculate duration from WAV file
            try:
                with wave.open(audio_file, "rb") as wf:
                    duration = wf.getnframes() / float(wf.getframerate())
            except Exception:
                duration = 3.0

            # Wait for playback, but stop early if call ends
            interrupted = self._disconnected.wait(timeout=duration + 0.3)
            return not interrupted

        except Exception as e:
            logger.error("Failed to play audio %s: %s", audio_file, e)
            return False
        finally:
            # Explicitly clean up player to free conference bridge port
            # (don't rely on GC — stale ports cause PJ_EINVAL for recorders)
            if connected:
                try:
                    player.stopTransmit(aud_med)
                except Exception:
                    pass
            try:
                del player
            except Exception:
                pass
            time.sleep(0.2)  # Conference bridge settling after port removal

    def _speak(self, text: str, language: str = "en") -> bool:
        """Generate TTS and play to caller."""
        if not self.call_active or not self.tts:
            return False

        audio_dir = Path("/app/audio/tmp")
        audio_dir.mkdir(parents=True, exist_ok=True)
        output_file = str(audio_dir / f"speech_{id(self)}_{int(time.time() * 1000)}.wav")

        try:
            audio_file = self.tts.speak(text, output_file, language=language)
            if not audio_file:
                return False

            file_size = Path(audio_file).stat().st_size
            if file_size < 100:
                logger.warning("TTS output too small (%d bytes)", file_size)
                return False

            start_ts = time.time()
            result = self._play_audio(audio_file)

            # Keep for call recording timeline
            self._call_timeline.append({"type": "assistant", "file": audio_file, "start": start_ts})

            return result
        except Exception as e:
            logger.error("TTS speak error: %s", e)
            return False

    def _speak_cached(self, phrase_key: str, language: str = "en") -> bool:
        """Play a pre-cached common phrase."""
        if not self.tts:
            return False
        audio_file = self.tts.get_cached_phrase(phrase_key, language)
        if audio_file:
            start_ts = time.time()
            result = self._play_audio(audio_file)
            # Copy cached file for timeline (originals must stay in cache)
            try:
                copy_path = f"/app/audio/tmp/cached_{id(self)}_{int(time.time()*1000)}.wav"
                shutil.copy2(audio_file, copy_path)
                self._call_timeline.append({"type": "assistant", "file": copy_path, "start": start_ts})
            except Exception:
                pass
            return result
        return False

    def _speak_greeting(self, assistant_name: str, language: str = "nl") -> bool:
        """Speak a personalized greeting with caller name and assistant identity."""
        name = self.caller_name
        if language == "nl":
            if name:
                greeting = (
                    f"Hoi {name}, ik ben {assistant_name}. "
                    "Je kunt me vragen over smart home, monitoring, agenda, "
                    "notities, muziek, of stel gewoon een vraag."
                )
            else:
                greeting = (
                    f"Hoi, ik ben {assistant_name}. "
                    "Je kunt me vragen over smart home, monitoring, agenda, "
                    "notities, muziek, of stel gewoon een vraag."
                )
        else:
            if name:
                greeting = (
                    f"Hi {name}, I'm {assistant_name}. "
                    "You can ask me about smart home, monitoring, calendar, "
                    "notes, music, or just ask a question."
                )
            else:
                greeting = (
                    f"Hi, I'm {assistant_name}. "
                    "You can ask me about smart home, monitoring, calendar, "
                    "notes, music, or just ask a question."
                )
        return self._speak(greeting, language)

    def _speak_goodbye(self, language: str = "en") -> bool:
        """Speak a natural, time-aware goodbye with caller name."""
        hour = datetime.now().hour
        name = self.caller_name

        if language == "nl":
            if 6 <= hour < 12:
                greetings = [
                    f"Fijne ochtend nog{', ' + name if name else ''}! Tot later.",
                    f"Nog een fijne ochtend{', ' + name if name else ''}! Tot de volgende keer.",
                    f"Doei{', ' + name if name else ''}! Geniet van je ochtend.",
                ]
            elif 12 <= hour < 18:
                greetings = [
                    f"Fijne middag{', ' + name if name else ''}! Ik hoor het wel als je me nodig hebt.",
                    f"Tot later{', ' + name if name else ''}! Fijne middag nog.",
                    f"Doei{', ' + name if name else ''}! Geniet van de rest van je dag.",
                ]
            elif 18 <= hour < 22:
                greetings = [
                    f"Fijne avond{', ' + name if name else ''}! Tot de volgende keer.",
                    f"Geniet van je avond{', ' + name if name else ''}! Tot ziens.",
                    f"Doei{', ' + name if name else ''}! Fijne avond nog.",
                ]
            else:
                greetings = [
                    f"Welterusten{', ' + name if name else ''}! Slaap lekker.",
                    f"Slaap lekker{', ' + name if name else ''}! Tot morgen.",
                    f"Trusten{', ' + name if name else ''}! Welterusten.",
                ]
        else:
            if 6 <= hour < 12:
                greetings = [
                    f"Have a great morning{', ' + name if name else ''}! Talk to you later.",
                    f"Enjoy your morning{', ' + name if name else ''}! Until next time.",
                    f"Bye{', ' + name if name else ''}! Have a wonderful morning.",
                ]
            elif 12 <= hour < 18:
                greetings = [
                    f"Enjoy your afternoon{', ' + name if name else ''}! Call me anytime.",
                    f"See you later{', ' + name if name else ''}! Have a good afternoon.",
                    f"Bye{', ' + name if name else ''}! Enjoy the rest of your day.",
                ]
            elif 18 <= hour < 22:
                greetings = [
                    f"Have a nice evening{', ' + name if name else ''}! Until next time.",
                    f"Enjoy your evening{', ' + name if name else ''}! Goodbye.",
                    f"Bye{', ' + name if name else ''}! Have a great evening.",
                ]
            else:
                greetings = [
                    f"Good night{', ' + name if name else ''}! Sleep well.",
                    f"Sleep well{', ' + name if name else ''}! Good night.",
                    f"Night night{', ' + name if name else ''}! Sweet dreams.",
                ]

        goodbye_text = random.choice(greetings)
        logger.info("Goodbye [%s]: %s", language, goodbye_text)

        # Log to transcript
        if self.call_logger and self._call_log_id:
            self.call_logger.add_transcript(self._call_log_id, "assistant", goodbye_text, language)

        # Try to speak the goodbye
        result = self._speak(goodbye_text, language)
        if not result:
            logger.warning("Dynamic goodbye TTS failed, trying cached phrase")
            result = self._speak_cached("goodbye", language)

        # Extra delay to flush RTP buffers before SIP BYE
        if result:
            time.sleep(0.5)

        return result

    def _conversation_loop(self) -> None:
        """VAD-driven, streaming conversation loop."""
        try:
            pj.Endpoint.instance().libRegisterThread(f"conv_{id(self)}")

            if not self._wait_for_media_active():
                logger.error("Media not active, ending call")
                return

            config = self.account.agent.config
            time.sleep(config["sip"]["greeting_delay"])

            # Play personalized greeting
            detected_lang = "nl"  # Default to Dutch
            assistant_name = config.get("assistant", {}).get("name", "ClaudePhone")
            self._speak_greeting(assistant_name, detected_lang)

            silence_cycles = 0

            while self.call_active:
                # Fixed-duration recording (6s)
                # VAD recorder's rapid port create/destroy breaks PJSIP conference bridge.
                # Use single long recording instead; faster-whisper has built-in VAD.
                audio_file = self._fixed_listen(6)

                if not self.call_active:
                    break

                if audio_file is None:
                    silence_cycles += 1
                    if silence_cycles >= 3:
                        self._speak_cached("no_input_goodbye", detected_lang)
                        break
                    self._speak_cached("no_input_prompt", detected_lang)
                    continue

                silence_cycles = 0

                # STT with language detection
                if self.stt:
                    text, language = self.stt.transcribe(audio_file)
                else:
                    text, language = None, None

                # Keep recording segment for call recording timeline
                self._call_timeline.append({"type": "user", "file": audio_file, "start": time.time() - 6})

                if not text:
                    self._speak_cached("not_understood", detected_lang)
                    continue

                if language:
                    detected_lang = language
                logger.info("User [%s]: %s", detected_lang, text)

                # Log transcript
                if self.call_logger and self._call_log_id:
                    self.call_logger.add_transcript(self._call_log_id, "user", text, detected_lang)

                # Check for goodbye
                if self.router and self.router.route(text, detected_lang) == "goodbye":
                    self._speak_goodbye(detected_lang)
                    break

                # Route and respond
                self._handle_user_input(text, detected_lang)

                # Follow-up prompt
                if self.call_active:
                    self._speak_cached("anything_else", detected_lang)

        except Exception as e:
            logger.error("Conversation error: %s", e, exc_info=True)
        finally:
            self._save_call_recording()
            self._hangup()

    def _save_call_recording(self) -> None:
        """Merge timeline entries (user + assistant audio) into a single call recording."""
        if not self._call_timeline or not self._call_log_id:
            return

        try:
            rec_dir = Path("/app/audio/recordings")
            rec_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(rec_dir / f"{self._call_log_id}.wav")

            # Sort timeline by start timestamp
            timeline = sorted(self._call_timeline, key=lambda e: e["start"])

            # Filter to existing files
            timeline = [e for e in timeline if Path(e["file"]).exists()]
            if not timeline:
                return

            # Target format: 8kHz mono 16-bit (PJSIP conference bridge standard)
            sample_rate = 8000
            sample_width = 2
            n_channels = 1

            with wave.open(output_path, "wb") as out_wav:
                out_wav.setnchannels(n_channels)
                out_wav.setsampwidth(sample_width)
                out_wav.setframerate(sample_rate)

                current_time = timeline[0]["start"]

                for entry in timeline:
                    # Insert silence for gap between segments
                    gap = entry["start"] - current_time
                    if gap > 0.1:
                        # Cap silence at 10 seconds to avoid huge gaps from timing errors
                        gap = min(gap, 10.0)
                        silence_frames = int(gap * sample_rate)
                        out_wav.writeframes(b'\x00' * silence_frames * sample_width * n_channels)

                    try:
                        with wave.open(entry["file"], "rb") as seg_wav:
                            seg_rate = seg_wav.getframerate()
                            seg_frames = seg_wav.readframes(seg_wav.getnframes())
                            seg_duration = seg_wav.getnframes() / float(seg_rate)

                            # If sample rate matches, write directly; otherwise skip
                            if seg_rate == sample_rate and seg_wav.getsampwidth() == sample_width:
                                out_wav.writeframes(seg_frames)
                            else:
                                # Best-effort: write anyway if width matches
                                out_wav.writeframes(seg_frames)

                            current_time = entry["start"] + seg_duration
                    except Exception as e:
                        logger.debug("Skip timeline entry %s: %s", entry["file"], e)

            if Path(output_path).exists() and Path(output_path).stat().st_size > 100:
                if self.call_logger:
                    self.call_logger.set_recording(self._call_log_id, output_path)
                logger.info("Call recording saved: %s (%d entries, user+assistant)",
                            output_path, len(timeline))

            # Clean up individual segment files
            for entry in self._call_timeline:
                try:
                    Path(entry["file"]).unlink(missing_ok=True)
                except Exception:
                    pass

        except Exception as e:
            logger.error("Failed to save call recording: %s", e)

    def _handle_user_input(self, text: str, language: str) -> None:
        """Route user input to the appropriate handler."""
        intent = "general"
        if self.router:
            intent = self.router.route(text, language)

        if intent == "general":
            self._handle_streaming_response(text, language)
        elif intent not in ("goodbye", "time") and self.router.is_category_only(text, language):
            self._speak_category_menu(intent, language)
        else:
            self._handle_integration(intent, text, language)

    def _speak_category_menu(self, intent: str, language: str) -> None:
        """Speak the available sub-commands for a category."""
        from ..ai.categories import get_category_menu

        menu_text = get_category_menu(intent, language)
        if menu_text:
            logger.info("Category menu [%s]: %s", intent, menu_text[:80])
            self._speak(menu_text, language)
        else:
            self._handle_integration(intent, "", language)

    def _handle_streaming_response(self, text: str, language: str) -> None:
        """Stream Ollama response sentence-by-sentence to TTS+playback."""
        if not self.ollama:
            self._speak("Sorry, AI is not available right now.", language)
            return

        if self.conversation:
            messages = self.conversation.get_messages_for_ollama()
            messages.append({"role": "user", "content": text})
        else:
            messages = [{"role": "user", "content": text}]

        audio_queue: queue.Queue = queue.Queue()
        full_response = []
        timed_out = False

        def produce():
            nonlocal timed_out
            try:
                def on_sentence(sentence: str):
                    full_response.append(sentence)  # Keep original for transcript
                    if self.tts and self.call_active:
                        from ..ai.ollama import clean_for_speech
                        clean_text = clean_for_speech(sentence)
                        if not clean_text:
                            return
                        audio_dir = Path("/app/audio/tmp")
                        out = str(audio_dir / f"stream_{id(self)}_{int(time.time()*1000)}.wav")
                        audio_file = self.tts.speak(clean_text, out, language=language)
                        if audio_file:
                            audio_queue.put(audio_file)

                self.ollama.stream_chat(messages, on_sentence)

            except TimeoutError:
                timed_out = True
            except Exception as e:
                logger.error("Ollama streaming error: %s", e)
            finally:
                audio_queue.put(None)  # Sentinel

        producer = threading.Thread(target=produce, daemon=True, name="ollama_stream")
        producer.start()

        # Play audio files as they arrive
        while self.call_active:
            try:
                audio_file = audio_queue.get(timeout=20)
            except queue.Empty:
                break

            if audio_file is None:
                break

            start_ts = time.time()
            played = self._play_audio(audio_file)

            # Keep for call recording timeline (don't delete)
            self._call_timeline.append({"type": "assistant", "file": audio_file, "start": start_ts})

            if not played:
                break

        producer.join(timeout=5)

        # Save to conversation history
        if self.conversation and full_response:
            full_text = " ".join(full_response)
            self.conversation.add_exchange(text, full_text, language)

            # Log AI response transcript
            if self.call_logger and self._call_log_id:
                self.call_logger.add_transcript(self._call_log_id, "assistant", full_text, language)

        # Handle timeout -> callback
        if timed_out and self.callback_queue and self.caller_number:
            self.callback_queue.add(self.caller_number, text)
            self._speak_cached("callback_notice", language)

    def _handle_integration(self, intent: str, text: str, language: str) -> None:
        """Handle a direct integration command."""
        handler = self.integrations.get(intent)
        if handler:
            try:
                response = handler.handle(text, language)
                self._speak(response, language)
            except Exception as e:
                logger.error("Integration %s error: %s", intent, e)
                error_msg = "Er ging iets mis." if language == "nl" else "Something went wrong."
                self._speak(error_msg, language)
        else:
            # Fallback to general AI if no handler
            self._handle_streaming_response(text, language)

    def _fixed_listen(self, duration: float = 6.0) -> Optional[str]:
        """Fallback: fixed-duration recording (used when VAD is not available)."""
        if not self.call_active:
            return None

        aud_med = self._get_active_audio_media()
        if aud_med is None:
            return None

        record_file = f"/app/audio/tmp/recording_{id(self)}_{int(time.time()*1000)}.wav"
        recorder = pj.AudioMediaRecorder()
        try:
            recorder.createRecorder(record_file)
            time.sleep(0.05)  # Conference bridge settling
            aud_med.startTransmit(recorder)

            interrupted = self._disconnected.wait(timeout=duration)

            try:
                aud_med.stopTransmit(recorder)
            except Exception:
                pass

            if interrupted:
                return None

            if Path(record_file).exists() and Path(record_file).stat().st_size > 1000:
                return record_file
            return None

        except Exception as e:
            logger.error("Recording failed: %s", e)
            return None

    def _play_outgoing_message(self) -> None:
        """Play a pre-generated message for callback calls, then mini conversation."""
        try:
            pj.Endpoint.instance().libRegisterThread(f"outgoing_{id(self)}")

            if not self._wait_for_media_active():
                return

            time.sleep(0.5)

            if self.outgoing_audio and Path(self.outgoing_audio).exists():
                self._play_audio(self.outgoing_audio)
            elif self.outgoing_message:
                self._speak(self.outgoing_message)

            # Mini conversation loop after callback (max 3 rounds)
            detected_lang = "nl"
            silence_cycles = 0

            while self.call_active and silence_cycles < 3:
                self._speak_cached("anything_else", detected_lang)

                audio_file = self._fixed_listen(6)
                if not self.call_active:
                    break

                if not audio_file:
                    silence_cycles += 1
                    if silence_cycles >= 2:
                        self._speak_cached("no_input_prompt", detected_lang)
                    continue

                if self.stt:
                    text, lang = self.stt.transcribe(audio_file)
                    # Keep for timeline recording
                    self._call_timeline.append({"type": "user", "file": audio_file, "start": time.time() - 6})
                else:
                    text, lang = None, None

                if not text:
                    self._speak_cached("not_understood", detected_lang)
                    continue

                silence_cycles = 0
                if lang:
                    detected_lang = lang
                logger.info("Callback user [%s]: %s", detected_lang, text)

                # Check for goodbye
                if self.router and self.router.route(text, detected_lang) == "goodbye":
                    self._speak_goodbye(detected_lang)
                    break

                self._handle_user_input(text, detected_lang)

            if silence_cycles >= 3 and self.call_active:
                self._speak_goodbye(detected_lang)

        except Exception as e:
            logger.error("Outgoing message error: %s", e)
        finally:
            self._save_call_recording()
            self._hangup()

    def _hangup(self) -> None:
        """End the call."""
        if not self.call_active:
            return
        self.call_active = False
        try:
            op = pj.CallOpParam()
            self.hangup(op)
        except Exception as e:
            logger.debug("Hangup: %s", e)

    def _cleanup(self) -> None:
        """Clean up resources after call ends."""
        self.call_active = False
        self.recorder = None
        # Clean up temp audio files for this call
        tmp_dir = Path("/app/audio/tmp")
        if tmp_dir.exists():
            call_id = str(id(self))
            for f in tmp_dir.glob(f"*_{call_id}_*"):
                try:
                    f.unlink()
                except Exception:
                    pass
