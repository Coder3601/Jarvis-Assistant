"""A.T.O.M study & work helpers: prompts, date parsing, quick intents, flashcards and quizzes."""
from __future__ import annotations

import datetime as dt
import logging
import re

log = logging.getLogger(__name__)

LEVELS = ["Simple", "GCSE", "A-Level", "University", "Expert"]

LEVEL_HINTS = {
    "Simple": "Explain as if to a curious 11-year-old: everyday words, a vivid analogy, no jargon.",
    "GCSE": "Pitch it at UK GCSE level (age 14-16): clear definitions, key terms in bold, an example, "
            "and a common exam mistake to avoid.",
    "A-Level": "Pitch it at UK A-Level (age 16-18): precise terminology, mechanisms and reasoning, a worked "
               "example where relevant, and how it is typically examined.",
    "University": "Pitch it at undergraduate level: rigorous, include the underlying theory, equations "
                  "where relevant (plain text or LaTeX), and connections to related topics.",
    "Expert": "Assume expert knowledge: be concise and technical, focus on nuance, edge cases and current understanding.",
}

ATOM_SYSTEM_PROMPT = """You are A.T.O.M. (Academic Tutor & Organisation Module), {who}'s personal study and \
work assistant, running locally on their Windows computer alongside JARVIS. You are clear, encouraging, \
precise and quietly witty, and you write in British English.

You help with revision, explaining concepts, planning work, staying focused and organising deadlines.
Your chat replies are usually spoken aloud, so keep them short (one to three sentences) and free of markdown \
unless the user asks for detail. For in-depth explanations, flashcards or quizzes, the user has the Explain \
panel on screen; you can suggest it.

You can manage the user's calendar and to-do list and start focus timers with the provided tools. Dates must \
be given to tools as YYYY-MM-DD. Never claim to have done something you did not do.

Current local date and time: {now}.
{facts}{recall}"""

EXPLAIN_PROMPT = """Explain the following clearly and accurately for a student.
{level_hint}
Structure your answer in Markdown with short sections: a one-line summary, the core explanation, \
an example or analogy, and 3-5 key points to remember. Keep it focused; no filler.

Topic or question: {topic}"""

SUMMARISE_PROMPT = """Summarise the following text for revision. {level_hint}
Use Markdown: a one-paragraph overview, then the key points as bullets, then any key terms with \
one-line definitions.

Text:
{topic}"""

FLASHCARD_PROMPT = """Create {n} high-quality revision flashcards about: {topic}
{level_hint}
Each card has a short question or term on the front and a concise, accurate answer on the back \
(at most 2 sentences). Cover the most important ideas, without duplicates.
Return JSON only: {{"cards": [{{"front": "...", "back": "..."}}]}}"""

QUIZ_PROMPT = """Write a {n}-question multiple-choice quiz about: {topic}
{level_hint}
Each question has exactly 4 options with exactly one correct answer; wrong options should be plausible. \
Include a one-sentence explanation of the correct answer.
Return JSON only: {{"questions": [{{"question": "...", "options": ["...", "...", "...", "..."], \
"answer": 0, "explanation": "..."}}]}} where "answer" is the index (0-3) of the correct option."""


def level_hint(level: str) -> str:
    return LEVEL_HINTS.get(level, LEVEL_HINTS["GCSE"])


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_date(text: str | None, today: dt.date | None = None) -> str | None:
    """Accepts YYYY-MM-DD, DD/MM(/YYYY), 'today', 'tomorrow', weekday names ('friday', 'next monday')."""
    if not text:
        return None
    today = today or dt.date.today()
    t = text.strip().lower()
    try:
        return dt.date.fromisoformat(t[:10]).isoformat()
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        if y < 100:
            y += 2000
        try:
            out = dt.date(y, mo, d)
            if not m.group(3) and out < today:
                out = dt.date(y + 1, mo, d)
            return out.isoformat()
        except ValueError:
            return None
    if t in ("today", "tonight"):
        return today.isoformat()
    if t == "tomorrow":
        return (today + dt.timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(?:(next|this) )?(" + "|".join(_DAYS) + r")", t)
    if m:
        target = _DAYS.index(m.group(2))
        delta = (target - today.weekday()) % 7
        if delta == 0 or m.group(1) == "next":
            delta = delta or 7
        return (today + dt.timedelta(days=delta)).isoformat()
    m = re.fullmatch(r"in (\d+) days?", t)
    if m:
        return (today + dt.timedelta(days=int(m.group(1)))).isoformat()
    return None


def friendly_date(iso: str, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    d = dt.date.fromisoformat(iso)
    if d == today:
        return "today"
    if d == today + dt.timedelta(days=1):
        return "tomorrow"
    if 0 < (d - today).days < 7:
        return d.strftime("%A")
    return d.strftime("%A %d %B")


# ---------------------------------------------------------------------------
# quick intents (no LLM round-trip)
# ---------------------------------------------------------------------------
SWITCH = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:(?:jarvis|atom|adam|rufus|roofus)[,!.]?\s+)?(?:please\s+|can you\s+|could you\s+)?"
    r"(?:(?:switch|change|swap|flip|go)(?: over)?(?: back)? to|activate|enable|bring up|wake up)\s+"
    r"(?:the\s+)?(atom|adam|a\.t\.o\.m\.?|jarvis|work|revision|study|general|normal|rufus|roofus|rufous|"
    r"r\.u\.f\.u\.s\.?|research)(?:\s+mode)?(?:\s+(?:please|now))?[.!]?$"
    r"|^(atom|jarvis|work|revision|study|general|rufus|research)\s+mode[.!]?$", re.I)

STUDY_WORDS = {"atom", "adam", "a.t.o.m", "a.t.o.m.", "work", "revision", "study"}
RESEARCH_WORDS = {"rufus", "roofus", "rufous", "r.u.f.u.s", "r.u.f.u.s.", "research"}


def match_switch(text: str) -> str | None:
    m = SWITCH.match(text.strip())
    if not m:
        return None
    word = (m.group(1) or m.group(2) or "").lower()
    if word in STUDY_WORDS:
        return "atom"
    if word in RESEARCH_WORDS:
        return "rufus"
    return "jarvis"


def match_study_intent(text: str) -> tuple[str, dict] | None:
    low = text.lower().strip().rstrip(".?!")
    low = re.sub(r"^(?:(?:hey\s+)?(?:atom|adam|jarvis)[,!.]?\s+)?(?:please\s+|can you\s+|could you\s+)?", "", low)
    m = re.fullmatch(r"(?:what(?:'s| is) (?:on )?my (?:calendar|schedule|agenda|diary)|what do i have on|"
                     r"what(?:'s| is) on|what have i got(?: on)?)(?: for)?\s*(today|tomorrow|this week|next week)?", low)
    if m and (m.group(1) or "calendar" in low or "schedule" in low or "agenda" in low or "diary" in low):
        when = m.group(1) or "today"
        days = 7 if "week" in when else 1
        start = parse_date("today") if when in ("today", "this week") else (
            parse_date("tomorrow") if when == "tomorrow" else
            (dt.date.today() + dt.timedelta(days=7 - dt.date.today().weekday())).isoformat())
        return "list_events", {"start_date": start, "days": days}
    m = re.fullmatch(r"add (.+?) to (?:my |the )?(?:to ?-?do|task|tasks|todo) list", low)
    if m:
        return "add_task", {"title": m.group(1)}
    if re.fullmatch(r"what(?:'s| is) on my (?:to ?-?do|task|todo) list|what are my tasks|list my tasks|"
                    r"what do i (?:still )?need to do", low):
        return "list_tasks", {}
    m = re.fullmatch(r"(?:tick off|complete|mark|cross off|finish(?:ed)?|i(?:'ve| have) (?:done|finished))\s+(.+?)"
                     r"(?:\s+as (?:done|complete|completed))?", low)
    if m and not m.group(1).startswith(("the timer", "timer")):
        return "complete_task", {"title": m.group(1)}
    m = re.fullmatch(r"(?:start|begin|set)(?: a| an| my)? (?:focus|study|revision|pomodoro|work)(?: session| timer| block)?"
                     r"(?: for (\d+) (?:minutes?|mins?))?", low)
    if m:
        return "start_study_timer", {"minutes": int(m.group(1)) if m.group(1) else 0}
    return None


def match_workspace_intent(text: str) -> tuple[str, str] | None:
    """Voice requests that should open the Explain workspace: ("explain"|"flashcards"|"quiz", topic)."""
    low = text.strip().rstrip(".?!")
    low = re.sub(r"^(?:(?:hey\s+)?(?:atom|adam)[,!.]?\s+)?(?:please\s+|can you\s+|could you\s+)?", "", low, flags=re.I)
    m = re.fullmatch(r"(?:explain|teach me(?: about)?|help me understand|break down)\s+(.+)", low, re.I)
    if m:
        return "explain", m.group(1)
    m = re.fullmatch(r"(?:make|create|generate|give)(?: me)?(?: some)? flash ?cards (?:on|about|for)\s+(.+)", low, re.I)
    if m:
        return "flashcards", m.group(1)
    m = re.fullmatch(r"(?:quiz me|test me|give me a quiz)(?: on| about)?\s+(.+)", low, re.I)
    if m:
        return "quiz", m.group(1)
    return None


# ---------------------------------------------------------------------------
# validation of model JSON
# ---------------------------------------------------------------------------
def clean_cards(data: dict) -> list[dict]:
    out = []
    for c in (data or {}).get("cards", []) or []:
        if isinstance(c, dict):
            f, b = str(c.get("front", "")).strip(), str(c.get("back", "")).strip()
            if f and b:
                out.append({"front": f, "back": b})
    return out


def clean_quiz(data: dict) -> list[dict]:
    out = []
    for q in (data or {}).get("questions", []) or []:
        if not isinstance(q, dict):
            continue
        opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        try:
            ans = int(q.get("answer", -1))
        except (TypeError, ValueError):
            # some models return the answer text instead of the index
            ans = opts.index(str(q.get("answer")).strip()) if str(q.get("answer")).strip() in opts else -1
        text = str(q.get("question", "")).strip()
        if text and len(opts) >= 2 and 0 <= ans < len(opts):
            out.append({"question": text, "options": opts[:6], "answer": ans,
                        "explanation": str(q.get("explanation", "")).strip()})
    return out
