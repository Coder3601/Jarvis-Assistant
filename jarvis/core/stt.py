"""Speech recognition: microphone capture (sounddevice) + adaptive VAD + faster-whisper.

Two modes:
  * push-to-talk  – capture one utterance, transcribe, stop.
  * hands-free    – listen continuously; utterances must start with the wake
                    word ("Jarvis, ...") unless a follow-up window is open.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from .. import perf

log = logging.getLogger(__name__)

SR = 16000
BLOCK = 480  # 30 ms


class SpeechRecognizer:
    def __init__(self, cfg, on_text: Callable[[str], None], on_level: Callable[[float], None],
                 on_status: Callable[[str], None], on_listening: Callable[[bool], None]):
        self.cfg = cfg
        self.on_text = on_text
        self.on_level = on_level
        self.on_status = on_status
        self.on_listening = on_listening
        self._model = None
        self._model_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ptt = threading.Event()        # a push-to-talk capture was requested
        self._muted = threading.Event()      # suspended while JARVIS speaks
        self._follow_up_until = 0.0
        self.available = True
        self.error: str | None = None

    # -------------------------------------------------------------- model
    def load_model(self):
        with self._model_lock:
            if self._model is not None:
                return self._model
            _add_cuda_dll_dirs()
            from faster_whisper import WhisperModel
            device = self.cfg.whisper_device
            compute = self.cfg.whisper_compute
            attempts = []
            if device in ("auto", "cuda"):
                attempts.append(("cuda", "float16" if compute == "auto" else compute))
            if device in ("auto", "cpu"):
                attempts.append(("cpu", "int8" if compute == "auto" else compute))
            last = None
            t0 = time.perf_counter()
            for dev, comp in attempts:
                try:
                    self.on_status(f"Loading speech model {self.cfg.whisper_model} ({dev})…")
                    self._model = WhisperModel(self.cfg.whisper_model, device=dev, compute_type=comp)
                    # a tiny warm-up decode verifies CUDA libraries are actually usable
                    list(self._model.transcribe(np.zeros(SR // 2, np.float32), language=self.cfg.language or None)[0])
                    log.info("Whisper %s loaded on %s/%s", self.cfg.whisper_model, dev, comp)
                    perf.record("speech_model_load", time.perf_counter() - t0)
                    self.on_status(f"Speech recognition ready ({dev})")
                    return self._model
                except Exception as exc:
                    last = exc
                    self._model = None
                    log.warning("Whisper on %s failed: %s", dev, exc)
            raise RuntimeError(f"Could not load Whisper model: {last}")

    def unload(self):
        with self._model_lock:
            self._model = None

    # ------------------------------------------------------------ control
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stt", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def push_to_talk(self):
        self._ptt.set()
        self.start()

    def cancel_capture(self):
        self._ptt.clear()

    def set_muted(self, muted: bool):
        if muted:
            self._muted.set()
        else:
            self._muted.clear()
            self._follow_up_until = time.time() + 6.0  # allow a reply without wake word

    def open_follow_up(self, seconds: float = 8.0):
        self._follow_up_until = time.time() + seconds

    @property
    def hands_free(self) -> bool:
        return bool(self.cfg.wake_word_enabled)

    # --------------------------------------------------------------- loop
    def _run(self):
        try:
            import sounddevice as sd
        except Exception as exc:
            self.available = False
            self.error = f"sounddevice unavailable: {exc}"
            self.on_status(self.error)
            return
        try:
            threading.Thread(target=self._safe_load, daemon=True).start()
            q: queue.Queue = queue.Queue()

            def cb(indata, frames, t, status):
                q.put(indata[:, 0].copy())

            while not self._stop.is_set():
                with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                    device=self.cfg.input_device, callback=cb):
                    self._loop(q)
                if not (self._ptt.is_set() or self.hands_free) or self._stop.is_set():
                    break
        except Exception as exc:
            log.exception("Microphone error")
            self.available = False
            self.error = f"Microphone error: {exc}"
            self.on_status(self.error)
            self.on_listening(False)

    def _safe_load(self):
        try:
            self.load_model()
        except Exception as exc:
            self.error = str(exc)
            self.on_status(str(exc))

    def _loop(self, q: queue.Queue):
        noise = 0.004
        pre = deque(maxlen=int(0.4 * SR / BLOCK))
        speech: list[np.ndarray] = []
        in_speech = False
        silence = 0.0
        voiced = 0.0
        ptt_started = 0.0
        last_listening = None
        idle_since = None
        while not self._stop.is_set():
            try:
                block = q.get(timeout=0.2)
            except queue.Empty:
                continue
            active = (self._ptt.is_set() or self.hands_free) and not self._muted.is_set()
            if active != last_listening:
                self.on_listening(active)
                last_listening = active
                if active and self._ptt.is_set():
                    ptt_started = time.time()
            rms = float(np.sqrt(np.mean(block * block)) + 1e-9)
            self.on_level(min(1.0, rms * 18.0) if active else 0.0)
            if not active:
                in_speech = False
                speech.clear()
                pre.clear()
                noise = 0.97 * noise + 0.03 * rms
                if not self.hands_free and not self._ptt.is_set() and not self._muted.is_set():
                    # push-to-talk mode: release the microphone when idle
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 4.0:
                        return
                continue
            idle_since = None

            sens = max(0.3, float(self.cfg.vad_sensitivity))
            threshold = max(noise * 2.8 / sens, 0.008 / sens)
            if not in_speech:
                pre.append(block)
                noise = 0.995 * noise + 0.005 * min(rms, noise * 3)
                if rms > threshold:
                    voiced += BLOCK / SR
                    if voiced >= 0.09:
                        in_speech = True
                        speech = list(pre)
                        silence = 0.0
                else:
                    voiced = max(0.0, voiced - BLOCK / SR)
                # push-to-talk gives up after 8 s of nothing
                if self._ptt.is_set() and time.time() - ptt_started > 8.0:
                    self._ptt.clear()
                    self.on_status("No speech detected")
                continue

            speech.append(block)
            silence = silence + BLOCK / SR if rms < threshold * 0.8 else 0.0
            dur = len(speech) * BLOCK / SR
            end_pause = float(self.cfg.silence_timeout)
            if perf.on("quick_vad"):
                end_pause = min(end_pause, 0.6)
            if silence >= end_pause or dur > 20.0:
                audio = np.concatenate(speech)
                in_speech = False
                speech = []
                voiced = 0.0
                was_ptt = self._ptt.is_set()
                self._ptt.clear()
                if dur - silence < 0.25:
                    continue
                self._transcribe(audio, was_ptt)

    def _transcribe(self, audio: np.ndarray, was_ptt: bool):
        self.on_listening(False)
        self.on_status("Transcribing…")
        try:
            model = self.load_model()
            t0 = time.perf_counter()
            segments, _info = model.transcribe(audio, language=self.cfg.language or None,
                                               beam_size=1 if perf.on("fast_stt") else 3,
                                               vad_filter=True, condition_on_previous_text=False,
                                               no_speech_threshold=0.5)
            text = " ".join(s.text for s in segments).strip()
            perf.record("speech_to_text", time.perf_counter() - t0)
        except Exception as exc:
            log.exception("Transcription failed")
            self.on_status(f"Transcription failed: {exc}")
            return
        finally:
            self.on_status("")
        text = re.sub(r"\s+", " ", text)
        if not text or _is_hallucination(text):
            return
        if was_ptt or not self.hands_free:
            self.on_text(text)
            return
        # hands-free: require wake word unless in follow-up window
        stripped = self._strip_wake_word(text)
        if stripped is not None:
            if stripped:
                self.on_text(stripped)
            else:
                self.open_follow_up()
                self.on_text("__WAKE__")
        elif time.time() < self._follow_up_until:
            self.on_text(text)

    def _strip_wake_word(self, text: str) -> str | None:
        words = [w.lower() for w in self.cfg.wake_words] or ["jarvis"]
        words += [w for w in ("jarvis", "atom", "adam", "rufus", "roofus") if w not in words]  # either assistant can be called
        pat = r"^\W*(?:(?:hey|hi|ok|okay|yo|hello)\W+)?(?:" + "|".join(map(re.escape, words)) + r")\b\W*"
        m = re.match(pat, text, flags=re.I)
        if m:
            return text[m.end():].strip()
        # wake word later in a short sentence ("what's the time jarvis")
        if len(text.split()) <= 12 and re.search(r"\b(" + "|".join(map(re.escape, words)) + r")\b", text, re.I):
            return re.sub(r"\b(" + "|".join(map(re.escape, words)) + r")\b[,.!?]?", "", text, flags=re.I).strip()
        return None


def _add_cuda_dll_dirs():
    """Make pip-installed NVIDIA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) visible on Windows."""
    import os
    import sys
    if sys.platform != "win32":
        return
    import site
    for base in site.getsitepackages() + [site.getusersitepackages()]:
        nv = os.path.join(base, "nvidia")
        if not os.path.isdir(nv):
            continue
        for pkg in os.listdir(nv):
            bindir = os.path.join(nv, pkg, "bin")
            if os.path.isdir(bindir):
                try:
                    os.add_dll_directory(bindir)
                    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass


_HALLUCINATIONS = {"thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", ".",
                   "thanks for watching.", "subtitles by the amara.org community"}


def _is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    return t in _HALLUCINATIONS or len(re.sub(r"[^a-z0-9]", "", t)) < 2
