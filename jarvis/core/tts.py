"""Text-to-speech with pluggable engines.

* WindowsVoiceEngine – Windows' built-in (SAPI5) voices, rendered to audio and played by JARVIS
                       itself. This lets every agent have its own voice, speed and *depth*
                       (pitch), drives the hologram with the real speech waveform, and skips
                       voices that Windows lists but can't actually speak. Fully offline.
* PiperEngine        – natural neural voices via the maintained `piper-tts` package
                       (Open Home Foundation), one voice per agent, fully offline.

Add another engine by subclassing ``TTSEngine`` and registering it in ``ENGINES``.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .. import perf

log = logging.getLogger(__name__)
PRELOAD = "\x00preload"

PERSONAS = ("jarvis", "atom", "rufus")


def _persona_setting(cfg, persona: str, name: str, default):
    """cfg.tts_<name> for JARVIS, cfg.<persona>_tts_<name> for the others."""
    if persona in ("atom", "rufus"):
        return getattr(cfg, f"{persona}_tts_{name}", default)
    return getattr(cfg, f"tts_{name}", default)


def deepen(audio: np.ndarray, factor: float) -> np.ndarray:
    """Lower the pitch (and formants) by `factor` (< 1 = deeper) by resampling.
    The audio gets 1/factor longer, so engines synthesise faster to compensate."""
    if audio.size < 2 or abs(factor - 1.0) < 1e-3:
        return audio
    n_out = int(len(audio) / factor)
    x_new = np.linspace(0, len(audio) - 1, n_out)
    return np.interp(x_new, np.arange(len(audio)), audio).astype(np.float32)


class TTSEngine:
    name = "base"
    provides_levels = True

    def __init__(self, cfg):
        self.cfg = cfg
        self._interrupt = threading.Event()

    persona = "jarvis"   # set by SpeechSynthesizer before each utterance

    def speak(self, text: str, on_level: Callable[[float], None]) -> None:
        raise NotImplementedError

    def interrupt(self):
        self._interrupt.set()

    def voices(self) -> list[tuple[str, str]]:
        return []

    def synth(self, text: str) -> tuple[np.ndarray, int] | None:
        """Render text to (audio, sample_rate) without playing it. Engines that support this
        allow the next sentence to be prepared while the current one plays."""
        return None

    def preload(self):
        pass

    def close(self):
        pass

    # shared playback with live level metering for the hologram
    def _play(self, audio: np.ndarray, sr: int, on_level):
        import sounddevice as sd
        audio = np.clip(audio * float(self.cfg.tts_volume), -1.0, 1.0).astype(np.float32)
        # 8 ms fade in/out avoids clicks between sentences
        f = min(len(audio) // 2, int(sr * 0.008))
        if f > 0:
            ramp = np.linspace(0, 1, f, dtype=np.float32)
            audio[:f] *= ramp
            audio[-f:] *= ramp[::-1]
        block = max(256, sr // 30)
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as out:
            for i in range(0, len(audio), block):
                if self._interrupt.is_set():
                    break
                chunk = audio[i:i + block]
                on_level(min(1.0, float(np.sqrt(np.mean(chunk * chunk))) * 5.0))
                out.write(chunk.reshape(-1, 1))
        on_level(0.0)


class WindowsVoiceEngine(TTSEngine):
    name = "windows"

    # voices each agent prefers when none is chosen in Settings (first working match wins)
    PREFERRED = {"jarvis": ("george", "ryan", "daniel", "david", "mark", "guy", "james"),
                 "atom": ("sonia", "libby", "hazel", "zira", "susan", "aria", "jenny"),
                 "rufus": ("mark", "guy", "ryan", "james", "david", "daniel", "george")}

    def __init__(self, cfg):
        super().__init__(cfg)
        if sys.platform != "win32":
            raise RuntimeError("Windows voices are only available on Windows")
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        self._client = comtypes.client
        self.sp = comtypes.client.CreateObject("SAPI.SpVoice")
        self._tokens = {}
        tokens = self.sp.GetVoices()
        for i in range(tokens.Count):
            t = tokens.Item(i)
            self._tokens[t.Id] = (t, t.GetDescription())
        self._bad: set[str] = set()
        self._tmp = Path(tempfile.gettempdir()) / "jarvis_tts"
        self._tmp.mkdir(exist_ok=True)
        if not self._tokens:
            raise RuntimeError("No Windows voices are installed")

    def voices(self):
        return [(vid, desc) for vid, (_t, desc) in self._tokens.items()]

    def _candidates(self, persona: str) -> list[str]:
        """Voice ids to try, best first."""
        chosen = _persona_setting(self.cfg, persona, "voice", "")
        order = []
        if chosen and chosen in self._tokens:
            order.append(chosen)
        jarvis_choice = self.cfg.tts_voice
        for want in self.PREFERRED.get(persona, ()):
            for vid, (_t, desc) in self._tokens.items():
                if want in desc.lower() and vid not in order:
                    # keep agents apart: avoid JARVIS's explicitly chosen voice for the others
                    if persona != "jarvis" and vid == jarvis_choice:
                        continue
                    order.append(vid)
        english = [vid for vid, (_t, d) in self._tokens.items() if "english" in d.lower() or "en-" in vid.lower()]
        order += [v for v in english + list(self._tokens) if v not in order]
        return [v for v in order if v not in self._bad] or order

    def _synth_memory(self, text: str, vid: str, rate: int) -> tuple[np.ndarray, int]:
        """Render to an in-memory stream (no temp file)."""
        token, _desc = self._tokens[vid]
        ms = self._client.CreateObject("SAPI.SpMemoryStream")
        fmt = self._client.CreateObject("SAPI.SpAudioFormat")
        fmt.Type = 22                             # SAFT22kHz16BitMono
        ms.Format = fmt
        previous = self.sp.AudioOutputStream
        try:
            self.sp.Voice = token
            self.sp.Rate = int(max(-10, min(10, rate)))
            self.sp.Volume = 100
            self.sp.AudioOutputStream = ms
            self.sp.Speak(text, 0)
        finally:
            self.sp.AudioOutputStream = previous
        data = ms.GetData()
        raw = data if isinstance(data, (bytes, bytearray)) else bytes(bytearray(data))
        return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0, 22050

    def _synth(self, text: str, vid: str, rate: int) -> tuple[np.ndarray, int]:
        if perf.on("memory_audio") and not getattr(self, "_mem_failed", False):
            try:
                audio, sr = self._synth_memory(text, vid, rate)
                if audio.size > sr * 0.05:
                    return audio, sr
                # nothing came back: check with a file before blaming the voice
                audio2, sr2 = self._synth_file(text, vid, rate)
                if audio2.size > sr2 * 0.05:
                    self._mem_failed = True
                    log.info("In-memory voice audio unsupported here; using temp files")
                return audio2, sr2
            except Exception as exc:
                self._mem_failed = True
                log.info("In-memory voice audio failed (%s); using temp files", exc)
        return self._synth_file(text, vid, rate)

    def _synth_file(self, text: str, vid: str, rate: int) -> tuple[np.ndarray, int]:
        token, _desc = self._tokens[vid]
        path = self._tmp / f"utt_{threading.get_ident()}.wav"
        stream = self._client.CreateObject("SAPI.SpFileStream")
        stream.Open(str(path), 3)                 # SSFMCreateForWrite
        previous = self.sp.AudioOutputStream
        try:
            self.sp.Voice = token
            self.sp.Rate = int(max(-10, min(10, rate)))
            self.sp.Volume = 100
            self.sp.AudioOutputStream = stream
            self.sp.Speak(text, 0)                # synchronous: renders the whole sentence to the file
        finally:
            try:
                self.sp.AudioOutputStream = previous
            finally:
                stream.Close()
        try:
            with wave.open(str(path), "rb") as w:
                sr, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
                raw = w.readframes(w.getnframes())
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if width == 2:
            audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        elif width == 1:
            audio = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            audio = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1)
        return audio, sr

    def speak(self, text, on_level):
        self._interrupt.clear()
        res = self.synth(text)
        if res and not self._interrupt.is_set():
            self._play(*res, on_level)

    def preload(self):
        try:
            self.synth("Ready.")
        except Exception:
            pass

    def synth(self, text):
        persona = self.persona
        wpm = int(_persona_setting(self.cfg, persona, "rate", 180))
        depth = float(_persona_setting(self.cfg, persona, "pitch", 1.0))
        depth = max(0.6, min(1.2, depth))
        # SAPI rate r gives roughly 3^(r/10) x normal speed; speed up by 1/depth so the deeper,
        # stretched audio still plays at the requested pace
        speed = (wpm / 180.0) / depth
        rate = round(10 * math.log(max(0.2, speed)) / math.log(3))
        safe = text.replace("&", " and ").replace("<", " ").replace(">", " ")
        for vid in self._candidates(persona):
            try:
                audio, sr = self._synth(safe, vid, rate)
            except Exception as exc:
                log.warning("voice %s failed: %s", self._tokens[vid][1], exc)
                self._bad.add(vid)
                continue
            if audio.size < sr * 0.05 or float(np.abs(audio).max(initial=0)) < 1e-3:
                log.warning("voice %s produced no audio – skipping it", self._tokens[vid][1])
                self._bad.add(vid)
                continue
            return deepen(audio, depth), sr
        raise RuntimeError("None of the installed Windows voices produced any audio")


class PiperEngine(TTSEngine):
    """Piper neural voices, fully offline.

    Uses the maintained `piper-tts` Python package (Open Home Foundation). A legacy
    `piper.exe` set in Settings is still supported if the package isn't installed.
    Each agent can have its own voice model (Settings › Voice / A.T.O.M / R.U.F.U.S).
    """
    name = "piper"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._voices: dict[str, object] = {}
        self._rates: dict[str, int] = {}
        import importlib.util
        self._api = importlib.util.find_spec("piper") is not None
        self.exe = cfg.piper_exe
        if not self._api and not (self.exe and Path(self.exe).exists()):
            raise RuntimeError("Piper isn't installed – run:  pip install piper-tts")
        if not self._model_for("jarvis"):
            raise RuntimeError("No Piper voice selected – download one in Settings › Voice")

    def _model_for(self, persona: str) -> str:
        own = self.cfg.piper_model if persona == "jarvis" else getattr(self.cfg, f"{persona}_piper_model", "")
        for m in (own, self.cfg.piper_model):
            if m and Path(m).exists():
                return m
        return ""

    def _sample_rate(self, model: str) -> int:
        if model not in self._rates:
            rate = 22050
            meta = Path(model + ".json")
            if meta.exists():
                try:
                    rate = int(json.loads(meta.read_text(encoding="utf-8"))["audio"]["sample_rate"])
                except Exception:
                    pass
            self._rates[model] = rate
        return self._rates[model]

    def _synth_api(self, text: str, model: str, length_scale: float) -> tuple[np.ndarray, int]:
        from piper import PiperVoice, SynthesisConfig
        voice = self._voices.get(model)
        if voice is None:
            voice = PiperVoice.load(model)
            self._voices[model] = voice
        cfg = SynthesisConfig(length_scale=length_scale, volume=1.0)
        chunks = list(voice.synthesize(text, syn_config=cfg))
        if not chunks:
            return np.zeros(0, np.float32), self._sample_rate(model)
        audio = np.concatenate([np.asarray(c.audio_float_array, dtype=np.float32) for c in chunks])
        return audio, int(chunks[0].sample_rate)

    def _synth_exe(self, text: str, model: str, length_scale: float) -> tuple[np.ndarray, int]:
        proc = subprocess.run(
            [self.exe, "--model", model, "--output_raw", "--length_scale", f"{length_scale:.2f}"],
            input=text.encode("utf-8"), capture_output=True, timeout=60,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, self._sample_rate(model)

    def speak(self, text, on_level):
        self._interrupt.clear()
        res = self.synth(text)
        if res and not self._interrupt.is_set():
            self._play(*res, on_level)

    def preload(self):
        if not self._api:
            return
        from piper import PiperVoice
        for p in PERSONAS:
            m = self._model_for(p)
            if m and m not in self._voices:
                self._voices[m] = PiperVoice.load(m)

    def synth(self, text):
        persona = self.persona
        model = self._model_for(persona)
        if not model:
            raise RuntimeError("No Piper voice selected for this agent")
        wpm = int(_persona_setting(self.cfg, persona, "rate", 175))
        depth = max(0.6, min(1.2, float(_persona_setting(self.cfg, persona, "pitch", 1.0))))
        # Piper's natural pace is roughly 170 wpm; a larger length_scale speaks more slowly.
        # Deepening stretches the audio, so shorten by the same factor to keep the pace.
        length_scale = max(0.4, min(2.0, (170.0 / max(60, wpm)) * depth))
        audio, sr = (self._synth_api if self._api else self._synth_exe)(text, model, length_scale)
        if audio.size == 0:
            return None
        return deepen(audio, depth), sr


ENGINES: dict[str, type[TTSEngine]] = {"windows": WindowsVoiceEngine, "piper": PiperEngine}
ALIASES = {"pyttsx3": "windows", "sapi": "windows"}


class SpeechSynthesizer:
    """Owns a worker thread that speaks queued sentences in order."""

    def __init__(self, cfg, on_start: Callable[[], None], on_done: Callable[[], None],
                 on_level: Callable[[float], None], on_error: Callable[[str], None]):
        self.cfg = cfg
        self.on_start = on_start
        self.on_done = on_done
        self.on_level = on_level
        self.on_error = on_error
        self._q: queue.Queue = queue.Queue()          # text waiting to be synthesised
        self._audio_q: queue.Queue = queue.Queue()    # audio waiting to be played (overlap mode)
        self._engine: TTSEngine | None = None
        self._speaking = False
        self._synth_busy = False
        self._state_lock = threading.Lock()
        self._generation = 0
        self._reload = threading.Event()
        self._voices: list[tuple[str, str]] = []
        self.persona = getattr(cfg, "persona", "jarvis")
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()
        self._player = threading.Thread(target=self._play_loop, name="tts-player", daemon=True)
        self._player.start()

    @property
    def engine_provides_levels(self) -> bool:
        return bool(self._engine and self._engine.provides_levels)

    def voices(self) -> list[tuple[str, str]]:
        return list(self._voices)

    def say(self, text: str):
        if text and text.strip() and self.cfg.tts_enabled:
            self._q.put((self._generation, text, self.persona))

    def preload(self):
        """Load voice models now so the first spoken reply doesn't wait."""
        self._q.put((self._generation, PRELOAD, self.persona))

    @staticmethod
    def _drain(q: queue.Queue):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break

    def interrupt(self):
        """Stop speaking now and drop anything queued."""
        self._generation += 1
        self._drain(self._q)
        self._drain(self._audio_q)
        if self._engine:
            self._engine.interrupt()

    def reload(self):
        self.interrupt()
        self._reload.set()
        self._q.put((self._generation, None, self.persona))

    def is_busy(self) -> bool:
        return self._speaking or self._synth_busy or not self._q.empty() or not self._audio_q.empty()

    def _set_speaking(self, on: bool):
        with self._state_lock:
            if on == self._speaking:
                return
            self._speaking = on
        (self.on_start if on else self.on_done)()

    def _maybe_done(self):
        if self._speaking and self._q.empty() and self._audio_q.empty() and not self._synth_busy:
            self._set_speaking(False)

    def _make_engine(self):
        if self._engine:
            try:
                self._engine.close()
            except Exception:
                pass
        name = ALIASES.get(self.cfg.tts_engine, self.cfg.tts_engine)
        name = name if name in ENGINES else "windows"
        try:
            self._engine = ENGINES[name](self.cfg)
        except Exception as exc:
            log.exception("TTS engine %s failed", name)
            self.on_error(f"Voice engine '{name}' unavailable: {exc}")
            self._engine = None
            if name != "windows":
                try:
                    self._engine = WindowsVoiceEngine(self.cfg)
                except Exception as exc2:
                    self.on_error(f"No speech output available: {exc2}")
        try:
            self._voices = WindowsVoiceEngine(self.cfg).voices() if not isinstance(
                self._engine, WindowsVoiceEngine) and sys.platform == "win32" else (
                self._engine.voices() if self._engine else [])
        except Exception:
            self._voices = []

    def _run(self):
        self._make_engine()
        while True:
            gen, text, persona = self._q.get()
            if self._reload.is_set():
                self._reload.clear()
                self._make_engine()
            if text is None or self._engine is None:
                continue
            if text == PRELOAD:
                t0 = time.perf_counter()
                try:
                    self._engine.persona = persona
                    self._engine.preload()
                    perf.record("voice_preload", time.perf_counter() - t0)
                except Exception as exc:
                    log.info("voice preload failed: %s", exc)
                continue
            if gen != self._generation:
                continue
            self._engine.persona = persona
            overlap = perf.on("overlap_speech") and type(self._engine).synth is not TTSEngine.synth
            if overlap:
                self._synth_busy = True
                try:
                    res = self._engine.synth(text)
                    if res is not None and gen == self._generation:
                        self._audio_q.put((gen, res[0], res[1]))
                except Exception as exc:
                    log.exception("TTS failed")
                    self.on_error(f"Speech failed: {exc}")
                finally:
                    self._synth_busy = False
                self._maybe_done()
                continue
            # sequential mode: synthesise and play in this thread
            self._set_speaking(True)
            try:
                self._engine.speak(text, self.on_level)
            except Exception as exc:
                log.exception("TTS failed")
                self.on_error(f"Speech failed: {exc}")
            if self._q.empty():
                self._set_speaking(False)

    def _play_loop(self):
        while True:
            gen, audio, sr = self._audio_q.get()
            if gen != self._generation or self._engine is None:
                self._maybe_done()
                continue
            self._set_speaking(True)
            try:
                self._engine._interrupt.clear()
                self._engine._play(audio, sr, self.on_level)
            except Exception as exc:
                log.exception("audio playback failed")
                self.on_error(f"Audio playback failed: {exc}")
            self._maybe_done()
