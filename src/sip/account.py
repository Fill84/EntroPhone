"""SIP Account - handles registration and incoming call events.

CRITICAL: onIncomingCall() must NEVER block. It runs inside PJSIP's
libHandleEvents() event loop. Any blocking (sleep, model loading, TTS)
prevents ACK/CANCEL processing and causes call drops.

Ring delay: sends 180 Ringing first, then answers with 200 OK after
a configurable delay (default 2s = ~2 rings) in a separate thread.
"""

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

import pjsua2 as pj

if TYPE_CHECKING:
    from .agent import SIPVoiceAgent

logger = logging.getLogger(__name__)


class SIPAccount(pj.Account):
    """PJSIP account that handles registration and incoming calls."""

    def __init__(self, agent: "SIPVoiceAgent"):
        super().__init__()
        self.agent = agent
        self.is_registered = False
        self.reg_event = threading.Event()
        self._call_lock = threading.RLock()
        self.current_call: Optional[pj.Call] = None

    def onRegStarted(self, prm: pj.OnRegStartedParam) -> None:
        logger.info("SIP registration started (renew=%s)", prm.renew)

    def onRegState(self, prm: pj.OnRegStateParam) -> None:
        ai = self.getInfo()
        status = ai.regStatus
        reason = ai.regStatusText

        if 200 <= status < 300:
            if not self.is_registered:
                logger.info("SIP registered successfully (status=%d %s)", status, reason)
                self.is_registered = True
                self.reg_event.set()
        else:
            logger.warning("SIP registration failed (status=%d %s)", status, reason)
            self.is_registered = False
            self.reg_event.clear()

    def onIncomingCall(self, prm: pj.OnIncomingCallParam) -> None:
        """Handle incoming call. MUST NOT BLOCK - return immediately!

        Sends 180 Ringing first, then schedules delayed 200 OK in a thread.
        """
        from .call import VoiceCall

        logger.info("Incoming call (callId=%d)", prm.callId)

        with self._call_lock:
            if self.current_call is not None:
                logger.warning("Already in a call, rejecting incoming call")
                call = pj.Call(self, prm.callId)
                reject_op = pj.CallOpParam()
                reject_op.statusCode = 486  # Busy Here
                try:
                    call.answer(reject_op)
                except Exception as e:
                    logger.error("Failed to reject call: %s", e)
                return

            call = VoiceCall(self, prm.callId)
            # Inject components (fast, no loading - just reference assignment)
            self.agent._inject_components(call)
            self.current_call = call

        # Send 180 Ringing (caller hears ringback tone)
        ring_op = pj.CallOpParam()
        ring_op.statusCode = 180
        try:
            call.answer(ring_op)
            logger.info("Sent 180 Ringing")
        except Exception as e:
            logger.error("Failed to send 180 Ringing: %s", e)

        # Delayed 200 OK in a separate thread (don't block event loop)
        ring_seconds = self.agent.config.get("sip", {}).get("ring_seconds", 2)
        t = threading.Thread(
            target=self._delayed_answer,
            args=(call, ring_seconds),
            daemon=True,
            name="delayed_answer",
        )
        t.start()

    def _delayed_answer(self, call, ring_seconds: int) -> None:
        """Wait for ring delay, then answer with 200 OK."""
        try:
            pj.Endpoint.instance().libRegisterThread("delayed_answer")
        except Exception:
            pass

        time.sleep(ring_seconds)

        # Check call is still valid (caller might have hung up during ringing)
        try:
            ci = call.getInfo()
            if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                logger.info("Caller hung up during ringing")
                with self._call_lock:
                    if self.current_call is call:
                        self.current_call = None
                return
        except Exception:
            with self._call_lock:
                if self.current_call is call:
                    self.current_call = None
            return

        answer_op = pj.CallOpParam()
        answer_op.statusCode = 200
        try:
            call.answer(answer_op)
            logger.info("Call answered with 200 OK (after %ds ring delay)", ring_seconds)
        except Exception as e:
            logger.error("Failed to answer call: %s", e)
            with self._call_lock:
                if self.current_call is call:
                    self.current_call = None

    def clear_current_call(self) -> None:
        """Clear the current call reference (called from VoiceCall on disconnect)."""
        with self._call_lock:
            self.current_call = None
            logger.info("Current call cleared")
