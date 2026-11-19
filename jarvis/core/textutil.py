"""Text helpers: streaming sentence chunking, speech clean-up, <think> filtering."""
from __future__ import annotations

import re

_SENT_END = re.compile(r"([.!?;:])(\s+|$)|\n+")


class SentenceChunker:
    """Feed streamed tokens; get back complete sentences ready to be spoken."""

    def __init__(self, min_len: int = 24):
        self.buf = ""
        self.min_len = min_len
        self.first = True

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            # speak the first clause sooner for responsiveness
            limit = 8 if self.first else self.min_len
            m = None
            for mm in _SENT_END.finditer(self.buf):
                if mm.end() >= limit and not _is_abbrev(self.buf[: mm.start() + 1]):
                    m = mm
                    break
            if not m or (m.group(2) == "" and m.group(0) != "\n"):
                break
            piece, self.buf = self.buf[: m.end()], self.buf[m.end():]
            if piece.strip():
                out.append(piece.strip())
                self.first = False
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


def _is_abbrev(text: str) -> bool:
    tail = text[-6:].lower()
    return bool(re.search(r"\b(mr|mrs|ms|dr|st|vs|etc|e\.g|i\.e|no)\.$", tail)) or bool(re.search(r"\d\.$", tail))


def clean_for_speech(text: str) -> str:
    t = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "the link on screen", t)
    t = re.sub(r"[*_#>|~]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


class ThinkFilter:
    """Removes <think>...</think> sections from a token stream (reasoning models)."""

    def __init__(self):
        self.inside = False
        self.buf = ""

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while self.buf:
            if self.inside:
                i = self.buf.find("</think>")
                if i < 0:
                    self.buf = self.buf[-8:]
                    return out
                self.buf = self.buf[i + 8:]
                self.inside = False
            else:
                i = self.buf.find("<think>")
                if i < 0:
                    # keep a possible partial tag at the end
                    keep = 0
                    for k in range(1, min(7, len(self.buf) + 1)):
                        if "<think>".startswith(self.buf[-k:]):
                            keep = k
                    out += self.buf[: len(self.buf) - keep]
                    self.buf = self.buf[len(self.buf) - keep:]
                    return out
                out += self.buf[:i]
                self.buf = self.buf[i + 7:]
                self.inside = True
        return out
