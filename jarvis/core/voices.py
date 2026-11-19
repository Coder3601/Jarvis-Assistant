"""Piper neural voices: the recommended voice for each agent and a safe downloader.

Voices come only from the official Piper voice library on Hugging Face
(huggingface.co/rhasspy/piper-voices), fetched with Piper's own downloader.
Once downloaded, speech runs fully offline.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..config import data_dir

log = logging.getLogger(__name__)

# one distinct male voice per agent
RECOMMENDED = {
    "jarvis": ("en_GB-alan-medium", "Alan – British male, calm and clear"),
    "atom": ("en_GB-northern_english_male-medium", "Northern English male, warm and friendly"),
    "rufus": ("en_US-ryan-high", "Ryan – American male, crisp and energetic (high quality)"),
}


def voices_dir() -> Path:
    d = data_dir() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def piper_installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("piper") is not None


def model_path(voice_code: str) -> Path:
    return voices_dir() / f"{voice_code}.onnx"


def download_recommended(cfg, progress: Callable[[str], None] | None = None) -> dict[str, str]:
    """Download the three recommended voices and point each agent at its voice."""
    say = progress or (lambda s: None)
    if not piper_installed():
        raise RuntimeError("The Piper package isn't installed. Run:  pip install piper-tts")
    from piper.download_voices import download_voice
    out = {}
    for persona, (code, desc) in RECOMMENDED.items():
        path = model_path(code)
        if path.exists() and Path(str(path) + ".json").exists():
            say(f"{code} already downloaded")
        else:
            say(f"Downloading {code} ({desc})…")
            download_voice(code, voices_dir())
        out[persona] = str(path)
    cfg.tts_engine = "piper"
    cfg.piper_model = out["jarvis"]
    cfg.atom_piper_model = out["atom"]
    cfg.rufus_piper_model = out["rufus"]
    cfg.save()
    say("All three voices are ready.")
    return out
