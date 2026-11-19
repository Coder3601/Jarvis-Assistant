"""Persistent configuration stored as JSON in the user's data folder."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "JARVIS"


def data_dir() -> Path:
    override = os.environ.get("JARVIS_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    else:
        base = Path.home() / ".jarvis"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Config:
    # identity
    assistant_name: str = "JARVIS"
    user_name: str = ""
    user_title: str = "sir"          # how JARVIS addresses you ("sir", "ma'am", a name...)

    # language model (Ollama)
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.1:8b"
    temperature: float = 0.6
    context_messages: int = 16       # recent messages sent with each request
    num_ctx: int = 8192
    use_tools: bool = True           # let the model call desktop tools
    auto_memory: bool = True         # extract long-term facts after each exchange
    keep_alive: str = "30m"

    # speech recognition (faster-whisper)
    stt_enabled: bool = True
    whisper_model: str = "base.en"
    whisper_device: str = "auto"     # auto | cpu | cuda
    whisper_compute: str = "auto"    # auto | int8 | float16 | int8_float16
    language: str = "en"
    input_device: int | None = None
    wake_word_enabled: bool = False  # hands-free mode: say "Jarvis ..." to talk
    wake_words: list[str] = field(default_factory=lambda: ["jarvis", "jervis", "jarvice", "jarvis's"])
    vad_sensitivity: float = 1.0     # >1 = more sensitive
    silence_timeout: float = 0.9     # seconds of silence that end an utterance

    # speech output
    tts_enabled: bool = True
    tts_engine: str = "windows"      # windows (built-in voices) | piper
    tts_voice: str = ""              # Windows voice id (blank = automatic, British male preferred)
    tts_rate: int = 175              # words per minute
    tts_pitch: float = 1.0           # voice depth: 1.0 = natural, lower = deeper
    tts_volume: float = 1.0
    piper_exe: str = ""
    piper_model: str = ""

    # interface
    quality: str = "high"            # low | medium | high | ultra
    fps: int = 60
    start_maximized: bool = True
    show_greeting: bool = True
    # personas: "jarvis" (general) and "atom" (work & revision)
    persona: str = "jarvis"
    atom_tts_voice: str = ""         # blank = pick a different voice from JARVIS automatically
    atom_tts_rate: int = 180
    atom_tts_pitch: float = 1.0
    atom_piper_model: str = ""       # optional Piper voice for A.T.O.M
    rufus_tts_voice: str = ""
    rufus_tts_rate: int = 185
    rufus_tts_pitch: float = 1.0
    rufus_piper_model: str = ""
    search_backend: str = "ddgs"     # ddgs (DuckDuckGo & co, free) | searxng (your own instance)
    searxng_url: str = ""
    search_region: str = "uk-en"
    research_depth: str = "Standard"
    watch_auto: bool = True          # daily watchlist digest
    watch_hour: int = 8              # ...not before this hour
    briefing_auto: bool = True       # morning briefing on the first launch of the day (before noon)
    briefing_news: bool = True       # include tech headlines (uses the internet via R.U.F.U.S's search)
    briefing_news_query: str = "technology"
    study_level: str = "GCSE"
    focus_minutes: int = 25
    break_minutes: int = 5

    mini_enabled: bool = True        # minimising condenses JARVIS to a floating hologram
    mini_size: int = 260
    mini_pos: list | None = None

    # Settings › Performance switches (see jarvis/perf.py); missing keys use their defaults
    perf: dict = field(default_factory=dict)

    # internal
    _path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls) -> "Config":
        path = data_dir() / "config.json"
        cfg = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                for k, v in raw.items():
                    if k in names and not k.startswith("_"):
                        setattr(cfg, k, v)
                # the short-lived "deeper by default" voices: return them to natural pitch
                if (raw.get("tts_pitch"), raw.get("atom_tts_pitch"), raw.get("rufus_tts_pitch")) == (0.84, 0.93, 0.88):
                    cfg.tts_pitch = cfg.atom_tts_pitch = cfg.rufus_tts_pitch = 1.0
            except Exception as exc:
                log.warning("Could not read config (%s); using defaults", exc)
        cfg._path = path
        if not path.exists():
            cfg.save()
        return cfg

    def save(self) -> None:
        path = self._path or data_dir() / "config.json"
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
