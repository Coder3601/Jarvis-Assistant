"""Protocols: named sequences of steps run with "Activate <name> protocol".

Protocols live in ``%APPDATA%/JARVIS/protocols.json`` and are edited from the
dashboard (PROTOCOLS button / Ctrl+P). Each step is one line:

    say <text>            speak a line ({title} and {name} are replaced)
    wait <n> seconds      pause (seconds or minutes)
    ask <question>        ask the AI model and speak its answer (needs Ollama)
    <command>             any built-in command, e.g. "open spotify", "volume up by 20",
                          "set a timer for 25 minutes", "stop the music", "lock the computer"

Shutdown / restart / sleep steps still ask for spoken confirmation and end the protocol.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .tools import match_intent, parse_duration

log = logging.getLogger(__name__)

ACTIVATE = re.compile(
    r"^(?:activate|initiate|engage|execute|run|start|begin|launch|commence|enable|deploy)\s+(?:the\s+)?"
    r"(.+?)\s+protocols?$", re.I)
LIST = re.compile(r"^(?:list|show|what are)(?: (?:me|all|my))*(?: (?:the|your|available))* protocols"
                  r"|what protocols (?:do you have|are there|are available)$", re.I)
ABORT = re.compile(r"^(?:abort|cancel|stop|terminate|halt)(?: the)?(?: current)? protocols?$", re.I)

SPOKEN_RESULTS = {"system_status", "get_datetime", "recall"}

DEFAULTS = [
    {"name": "Focus", "description": "Start a 25-minute focus session.",
     "steps": ["say Engaging focus protocol. Distractions are now my problem, {title}.",
               "stop the music",
               "set a timer for 25 minutes for focus session",
               "say Twenty-five minutes on the clock. I'll let you know when time is up."]},
    {"name": "House Party", "description": "Music on, volume up.",
     "steps": ["say Activating house party protocol.",
               "open spotify",
               "wait 4 seconds",
               "play",
               "volume up by 30",
               "say Enjoy yourself, {title}."]},
    {"name": "Diagnostics", "description": "Full system report.",
     "steps": ["say Running full diagnostics.",
               "system status",
               "what time is it"]},
    {"name": "Goodnight", "description": "Stop the music and lock the PC.",
     "steps": ["say Goodnight, {title}. Securing the workstation.",
               "stop the music",
               "wait 2 seconds",
               "lock the computer"]},
]


@dataclass
class Protocol:
    name: str
    description: str = ""
    steps: list[str] = field(default_factory=list)


@dataclass
class Step:
    kind: str            # say | wait | ask | command | invalid
    text: str            # original line
    payload: object = None
    note: str = ""


def parse_step(line: str) -> Step:
    s = line.strip()
    low = s.lower()
    if not s or s.startswith("#"):
        return Step("invalid", s, note="empty / comment")
    m = re.match(r"^(?:say|speak|announce)[:\s]+(.+)$", s, re.I)
    if m:
        return Step("say", s, m.group(1).strip())
    m = re.match(r"^(?:wait|delay|sleep for|hold for)\s+(?:for\s+)?(.+)$", low)
    if m:
        secs = parse_duration(m.group(1))
        if secs and secs <= 3600:
            return Step("wait", s, secs)
        return Step("invalid", s, note="wait needs a duration up to 1 hour, e.g. 'wait 3 seconds'")
    m = re.match(r"^(?:ask|query)[:\s]+(.+)$", s, re.I)
    if m:
        return Step("ask", s, m.group(1).strip())
    intent = match_intent(s)
    if intent:
        return Step("command", s, intent, note=intent[0].replace("_", " "))
    return Step("invalid", s, note="not a recognised command")


class ProtocolStore:
    def __init__(self, path: Path):
        self.path = path
        self.protocols: list[Protocol] = []
        self.load()

    def load(self):
        if not self.path.exists():
            self.protocols = [Protocol(**d) for d in DEFAULTS]
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.protocols = [Protocol(name=str(d.get("name", "")).strip(),
                                       description=str(d.get("description", "")),
                                       steps=[str(x) for x in d.get("steps", [])])
                              for d in raw.get("protocols", []) if str(d.get("name", "")).strip()]
        except Exception as exc:
            log.warning("Could not read protocols (%s)", exc)
            self.protocols = []

    def save(self):
        data = {"protocols": [asdict(p) for p in self.protocols]}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def names(self) -> list[str]:
        return [p.name for p in self.protocols]

    def find(self, spoken: str) -> Protocol | None:
        def norm(x: str) -> str:
            return re.sub(r"[^a-z0-9]", "", x.lower())
        target = norm(re.sub(r"\bprotocols?\b", "", spoken, flags=re.I))
        if not target:
            return None
        table = {norm(p.name): p for p in self.protocols}
        if target in table:
            return table[target]
        close = difflib.get_close_matches(target, table.keys(), n=1, cutoff=0.72)
        return table[close[0]] if close else None
