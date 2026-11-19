"""Performance switches (Settings › Performance) and simple timing measurements.

Every optimisation can be turned on or off. Values live in ``cfg.perf`` (a dict), so
new switches never break older config files.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("jarvis.perf")

# key, group, label, description, default, needs restart
OPTIONS: list[tuple[str, str, str, str, bool, bool]] = [
    ("stable_prompt", "Faster replies", "Reuse the AI's processed instructions",
     "Keeps the start of every request identical (time and recalled memories go at the end), so Ollama can "
     "skip re-reading it. Usually the biggest cut to the wait before the first word.", True, False),
    ("smart_tools", "Faster replies", "Only send the action list when needed",
     "Leaves the ~20 tool descriptions out of normal chat; they're added when a message looks like a command. "
     "R.U.F.U.S always gets its web tools.", True, False),
    ("defer_memory", "Faster replies", "Learn facts about you while idle",
     "Runs the 'what should I remember?' check a few seconds after you stop talking, so it never delays "
     "your next answer.", True, False),
    ("small_context", "Faster replies", "Compact context window (4096 tokens)",
     "Caps the AI's working memory at 4096 tokens for chat. Quicker to process; plenty for conversation. "
     "Research reports still use a larger window.", True, False),

    ("lazy_holograms", "Faster startup", "Build holograms only when first shown",
     "A.T.O.M's and R.U.F.U.S's holograms are created the first time you switch to them.", True, True),
    ("geometry_cache", "Faster startup", "Cache hologram geometry on disk",
     "Saves the generated 3D geometry after the first run and loads it on later launches.", True, True),
    ("preload_speech", "Faster startup", "Preload speech models in the background",
     "Loads Whisper and the voices shortly after startup so the first voice command and spoken reply "
     "don't wait.", True, True),

    ("overlap_speech", "Smoother speech", "Prepare the next sentence while speaking",
     "Generates the next sentence's audio while the current one plays, removing gaps.", True, False),
    ("memory_audio", "Smoother speech", "Keep Windows-voice audio in memory",
     "Avoids writing a temporary file for every sentence (falls back automatically if unsupported).", True, False),
    ("fast_stt", "Smoother speech", "Lighter speech recognition",
     "Uses Whisper's fast single-pass decoding. Slightly less accurate on unclear audio.", True, False),
    ("quick_vad", "Smoother speech", "Quicker end-of-speech detection",
     "Treats 0.6 s of silence as the end of what you said (unless your Voice setting is already shorter).",
     True, False),

    ("idle_redraw", "Lower CPU when idle", "Only redraw panels when something changes",
     "Gauges, mic buttons, status lights and the HUD stop redrawing 20-30 times a second when nothing moves.",
     True, False),
    ("pause_hidden", "Lower CPU when idle", "Pause hidden pages",
     "Panels on the agents you're not looking at stop animating.", True, False),
    ("gl_cache", "Lower CPU when idle", "Efficient hologram drawing",
     "Skips re-sending unchanged settings to the graphics card each frame.", True, True),
    ("background_fps", "Lower CPU when idle", "30 fps when JARVIS isn't the active window",
     "Drops the hologram frame rate while you're working in another app.", True, False),
    ("mini_30fps", "Lower CPU when idle", "Floating hologram at 30 fps", "", True, False),
    ("light_stats", "Lower CPU when idle", "Lighter system monitoring",
     "Counts running processes every 5 seconds instead of every second.", True, False),

    ("parallel_search", "Faster research", "Run searches in parallel",
     "R.U.F.U.S's searches, watchlist checks and briefing news run at the same time.", True, False),
    ("fast_timeouts", "Faster research", "Give up on slow web pages sooner",
     "Waits 8 s instead of 12 s for a page before moving on.", True, False),

    ("timing_logs", "Measurement", "Measure and log timings",
     "Records startup time, time to first word / first speech, transcription and research times "
     "(shown below and in jarvis.log).", True, False),
]

DEFAULTS = {k: d for k, _g, _l, _desc, d, _r in OPTIONS}
_cfg = None
START = time.perf_counter()
METRICS: dict[str, float] = {}
_once: set[str] = set()
_lock = threading.Lock()


def bind(cfg):
    global _cfg
    _cfg = cfg
    if not isinstance(getattr(cfg, "perf", None), dict):
        cfg.perf = {}


def on(key: str) -> bool:
    if _cfg is None:
        return DEFAULTS.get(key, False)
    return bool(_cfg.perf.get(key, DEFAULTS.get(key, False)))


def record(name: str, seconds: float):
    if not on("timing_logs"):
        return
    with _lock:
        METRICS[name] = seconds
    log.info("timing %-28s %.3f s", name, seconds)


def record_once(name: str, seconds: float):
    with _lock:
        if name in _once:
            return
        _once.add(name)
    record(name, seconds)


def since_start() -> float:
    return time.perf_counter() - START


LABELS = {
    "startup_window": "Startup → window shown",
    "startup_hologram": "Startup → first hologram frame",
    "reply_first_word": "Request → first word",
    "reply_total": "Request → reply finished",
    "reply_first_audio": "Request → first speech",
    "speech_to_text": "Transcription",
    "speech_model_load": "Speech model load",
    "voice_preload": "Voice preload",
    "research_report": "Research report",
    "briefing": "Morning briefing",
}


def summary() -> list[tuple[str, str]]:
    with _lock:
        items = dict(METRICS)
    return [(LABELS.get(k, k), f"{v:.2f} s") for k, v in items.items()]
