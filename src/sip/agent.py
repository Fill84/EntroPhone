"""SIP Voice Agent - main coordinator for PJSIP, calls, and background workers."""

import logging
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import pjsua2 as pj

from ..config import get_config
from .account import SIPAccount
from .call import VoiceCall

logger = logging.getLogger(__name__)


class SIPVoiceAgent:
    """Main SIP voice agent coordinator."""

    def __init__(
        self,
        tts=None,
        stt=None,
        vad_recorder=None,
        player=None,
        router=None,
        conversation_factory=None,
        ollama=None,
        callback_queue=None,
        integrations: Optional[Dict] = None,
    ):
        self.config = get_config()
        self.running = False
        self.ep: Optional[pj.Endpoint] = None
        self.account: Optional[SIPAccount] = None

        # Components injected from main.py
        self.tts = tts
        self.stt = stt
        self.vad_recorder = vad_recorder
        self.player = player
        self.router = router
        self.conversation_factory = conversation_factory
        self.ollama = ollama
        self.callback_queue = callback_queue
        self.integrations = integrations or {}
        self.call_logger = None  # Set by main.py after init

        # Background workers
        self._callback_thread: Optional[threading.Thread] = None
        self._monitoring_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Initialize PJSIP, register, and start the event loop."""
        self.running = True

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        try:
            self._init_pjsip()
            self._register()
            self._start_background_workers()
            self._event_loop()
        except Exception as e:
            logger.error("Fatal error: %s", e, exc_info=True)
        finally:
            self._shutdown()

    def _init_pjsip(self) -> None:
        """Initialize PJSIP endpoint and transport."""
        sip = self.config["sip"]

        self.ep = pj.Endpoint()
        self.ep.libCreate()

        # Endpoint config
        ep_cfg = pj.EpConfig()
        ep_cfg.logConfig.level = 3
        ep_cfg.logConfig.consoleLevel = 3
        ep_cfg.logConfig.filename = "/app/logs/pjsip.log"

        # Media config: 8kHz mono for telephony
        ep_cfg.medConfig.clockRate = 8000
        ep_cfg.medConfig.sndClockRate = 8000
        ep_cfg.medConfig.channelCount = 1
        ep_cfg.medConfig.quality = 10
        ep_cfg.medConfig.ecOptions = 0
        ep_cfg.medConfig.ecTailLen = 0
        ep_cfg.medConfig.noVad = True

        self.ep.libInit(ep_cfg)

        # Transport config
        transport_cfg = pj.TransportConfig()
        transport_cfg.port = sip["local_port"]

        # Docker NAT handling
        public_ip = sip.get("public_ip", "")
        if public_ip:
            transport_cfg.publicAddress = public_ip
        elif Path("/.dockerenv").exists():
            try:
                host_ip = socket.gethostbyname("host.docker.internal")
                transport_cfg.publicAddress = host_ip
                logger.info("Docker detected, using host IP: %s", host_ip)
            except socket.gaierror:
                logger.warning("Could not resolve host.docker.internal")

        transport_type = (
            pj.PJSIP_TRANSPORT_UDP
            if sip["transport"] == "UDP"
            else pj.PJSIP_TRANSPORT_TCP
        )
        self.ep.transportCreate(transport_type, transport_cfg)
        self.ep.libStart()

        # CRITICAL: Use null audio device in Docker (no sound card available)
        # Without this, PJSIP can't set up media and calls disconnect immediately
        self.ep.audDevManager().setNullDev()

        logger.info("PJSIP initialized (port=%d, transport=%s, null_audio=True)", sip["local_port"], sip["transport"])

    def _register(self) -> None:
        """Register SIP account with the server."""
        sip = self.config["sip"]
        transport = sip["transport"].lower()

        acc_cfg = pj.AccountConfig()
        acc_cfg.idUri = f"sip:{sip['username']}@{sip['server']}"
        acc_cfg.regConfig.registrarUri = f"sip:{sip['server']};transport={transport}"
        acc_cfg.regConfig.timeoutSec = sip["registration_timeout"]

        # Credentials
        cred = pj.AuthCredInfo()
        cred.scheme = "digest"
        cred.realm = "*"
        cred.username = sip["username"]
        cred.data = sip["password"]
        cred.dataType = 0  # Plain text
        acc_cfg.sipConfig.authCreds.append(cred)

        # Proxy
        proxy = sip.get("proxy", "")
        if proxy:
            acc_cfg.sipConfig.proxies.append(proxy)

        # NAT / Docker config
        public_ip = sip.get("public_ip", "")
        public_port = sip.get("public_port", 5061)
        if public_ip:
            contact_uri = f"sip:{sip['username']}@{public_ip}:{public_port};ob"
            acc_cfg.sipConfig.contactUri = contact_uri
            acc_cfg.natConfig.contactRewriteUse = 0
            logger.info("Using explicit contact URI: %s", contact_uri)

        # RTP port range + public IP for media (critical for Docker NAT)
        # Without publicAddress on media transport, SDP will contain the container's
        # internal IP (172.x.x.x) and FreePBX won't be able to send RTP back to us
        acc_cfg.mediaConfig.transportConfig.port = 4000
        acc_cfg.mediaConfig.transportConfig.portRange = 20
        if public_ip:
            acc_cfg.mediaConfig.transportConfig.publicAddress = public_ip
            logger.info("RTP media public address: %s (ports 4000-4019)", public_ip)

        # UDP keep-alive for NAT
        acc_cfg.natConfig.udpKaIntervalSec = 15

        # Create account
        self.account = SIPAccount(self)
        self.account.create(acc_cfg)

        # Wait for registration
        timeout = sip["registration_timeout"]
        logger.info("Waiting for SIP registration (timeout=%ds)...", timeout)
        if self.account.reg_event.wait(timeout=timeout):
            logger.info("SIP registration successful")
        else:
            logger.error("SIP registration timeout after %ds", timeout)

    def _start_background_workers(self) -> None:
        """Start callback worker, monitoring, and watchdog threads."""
        if self.callback_queue:
            self._callback_thread = threading.Thread(
                target=self._callback_worker, daemon=True, name="callback_worker"
            )
            self._callback_thread.start()
            logger.info("Callback worker started")

        monitoring = self.config.get("monitoring", {})
        if monitoring.get("enabled") and self.integrations.get("monitoring"):
            self._monitoring_thread = threading.Thread(
                target=self._monitoring_loop, daemon=True, name="monitoring"
            )
            self._monitoring_thread.start()
            logger.info("Monitoring loop started")

        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True, name="watchdog"
        )
        self._watchdog_thread.start()

    def _event_loop(self) -> None:
        """Main PJSIP event loop."""
        logger.info("Entering PJSIP event loop")
        while self.running:
            try:
                self.ep.libHandleEvents(100)  # 100ms timeout
            except pj.Error as e:
                if self.running:
                    logger.error("PJSIP event error: %s", e)
                    time.sleep(0.5)

    def _callback_worker(self) -> None:
        """Process callback queue - make outgoing calls with pre-generated responses."""
        pj.Endpoint.instance().libRegisterThread("callback_worker")
        logger.info("Callback worker thread registered")

        while self.running:
            # Don't make callbacks while in a call
            if self.account and self.account.current_call is not None:
                time.sleep(2)
                continue

            item = self.callback_queue.pop()
            if item is None:
                time.sleep(2)
                continue

            logger.info("Processing callback: %s -> %s", item.number, item.message[:50])

            try:
                # Get response with longer timeout
                response = self._get_callback_response(item.message)
                if not response:
                    response = "Sorry, I could not process your question."

                # Pre-generate TTS
                audio_file = None
                if self.tts:
                    audio_dir = Path("/app/audio/tmp")
                    audio_dir.mkdir(parents=True, exist_ok=True)
                    out = str(audio_dir / f"callback_{int(time.time()*1000)}.wav")
                    audio_file = self.tts.speak(response, out)

                # Make outgoing call
                self._make_outgoing_call(item.number, response, audio_file)

            except Exception as e:
                logger.error("Callback processing error: %s", e)
                # Re-queue with retry
                if hasattr(item, "retry_count"):
                    item.retry_count += 1
                    if item.retry_count <= 3:
                        time.sleep(30)
                        self.callback_queue.prepend(item)

    def _get_callback_response(self, question: str) -> Optional[str]:
        """Get a response for a callback question (with longer timeout)."""
        if self.ollama:
            try:
                response = self.ollama.chat_sync(
                    [{"role": "user", "content": question}],
                    timeout=120.0,
                )
                return response
            except Exception as e:
                logger.error("Callback Ollama error: %s", e)
        return None

    def _make_outgoing_call(
        self, number: str, message: str, audio_file: Optional[str] = None
    ) -> None:
        """Make an outgoing SIP call."""
        if not self.account or not self.account.is_registered:
            logger.error("Cannot make outgoing call: not registered")
            return

        sip = self.config["sip"]
        dest_uri = f"sip:{number}@{sip['server']}"
        logger.info("Making outgoing call to: %s", dest_uri)

        call = VoiceCall(self.account, pj.PJSUA_INVALID_ID)
        call.outgoing_message = message
        call.outgoing_audio = audio_file
        self._inject_components(call)

        with self.account._call_lock:
            self.account.current_call = call

        try:
            prm = pj.CallOpParam(True)
            call.makeCall(dest_uri, prm)
            # Wait for call to complete
            call._call_done.wait(timeout=120)
        except Exception as e:
            logger.error("Outgoing call failed: %s", e)
            self.account.clear_current_call()

    def _monitoring_loop(self) -> None:
        """Periodically check server health and trigger callbacks on failure."""
        pj.Endpoint.instance().libRegisterThread("monitoring")
        interval = self.config["monitoring"]["check_interval"]
        monitor = self.integrations.get("monitoring")

        while self.running:
            try:
                if monitor:
                    alerts = monitor.check_all()
                    callback_number = self.config["sip"].get("callback_number", "")
                    if alerts and callback_number and self.callback_queue:
                        for alert in alerts:
                            self.callback_queue.add(callback_number, alert)
                            logger.warning("Monitoring alert queued: %s", alert)
            except Exception as e:
                logger.error("Monitoring error: %s", e)

            # Sleep in small chunks so we can exit quickly
            for _ in range(interval * 10):
                if not self.running:
                    break
                time.sleep(0.1)

    def _watchdog(self) -> None:
        """Watch for stale calls and clean them up."""
        while self.running:
            time.sleep(10)
            if self.account and self.account.current_call:
                call = self.account.current_call
                if not call.call_active and call._call_done.is_set():
                    logger.info("Watchdog: cleaning up stale call")
                    self.account.clear_current_call()

    def _inject_components(self, call: VoiceCall) -> None:
        """Inject all components into a VoiceCall."""
        call.tts = self.tts
        call.stt = self.stt
        call.vad_recorder = self.vad_recorder
        call.player = self.player
        call.router = self.router
        call.ollama = self.ollama
        call.callback_queue = self.callback_queue
        call.integrations = self.integrations
        call.call_logger = self.call_logger
        if self.conversation_factory:
            call.conversation = self.conversation_factory()

    def _signal_handler(self, signum, frame) -> None:
        logger.info("Signal %d received, shutting down...", signum)
        self.running = False

    def _shutdown(self) -> None:
        """Clean shutdown of PJSIP and all workers."""
        logger.info("Shutting down SIP Voice Agent...")
        self.running = False

        # Hang up current call
        if self.account and self.account.current_call:
            try:
                call = self.account.current_call
                if call.call_active:
                    op = pj.CallOpParam()
                    call.hangup(op)
            except Exception:
                pass

        # Destroy PJSIP
        if self.ep:
            try:
                self.ep.libDestroy()
            except Exception:
                pass

        logger.info("Shutdown complete")
