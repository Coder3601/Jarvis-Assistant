"""Desktop control tools.

Each tool is a plain function returning a short text result. Tools are
exposed to the language model through Ollama's native tool-calling API and
are also triggered directly by a fast deterministic intent router for common
commands ("open notepad", "volume up", "what time is it" ...).
"""
from __future__ import annotations

import ctypes
import datetime as dt
import difflib
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"


@dataclass
class Tool:
    name: str
    description: str
    params: dict          # JSON-schema properties
    required: list
    fn: Callable[..., str]
    llm: bool = True      # expose to the language model

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------
VK = {"volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF, "next": 0xB0, "previous": 0xB1,
      "stop": 0xB2, "play_pause": 0xB3}


def _press(vk: int, times: int = 1):
    if not IS_WIN:
        raise OSError("Media keys are only supported on Windows")
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.01)


APP_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "paint": "mspaint.exe",
    "command prompt": "cmd.exe", "cmd": "cmd.exe", "terminal": "wt.exe", "windows terminal": "wt.exe",
    "powershell": "powershell.exe", "task manager": "taskmgr.exe", "file explorer": "explorer.exe",
    "explorer": "explorer.exe", "files": "explorer.exe", "control panel": "control.exe",
    "settings": "ms-settings:", "windows settings": "ms-settings:", "bluetooth settings": "ms-settings:bluetooth",
    "wifi settings": "ms-settings:network-wifi", "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound", "snipping tool": "snippingtool.exe", "camera": "microsoft.windows.camera:",
    "edge": "msedge", "microsoft edge": "msedge", "chrome": "chrome", "google chrome": "chrome",
    "firefox": "firefox", "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "vs code": "code", "vscode": "code", "visual studio code": "code", "spotify": "spotify:",
    "store": "ms-windows-store:", "microsoft store": "ms-windows-store:", "mail": "outlookmail:",
    "calendar": "outlookcal:", "clock": "ms-clock:", "maps": "bingmaps:", "photos": "ms-photos:",
    "registry editor": "regedit.exe", "device manager": "devmgmt.msc", "disk management": "diskmgmt.msc",
}

_start_menu_cache: dict[str, str] | None = None


def _start_menu_apps() -> dict[str, str]:
    global _start_menu_cache
    if _start_menu_cache is not None:
        return _start_menu_cache
    apps: dict[str, str] = {}
    roots = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
    for root in roots:
        if root.exists():
            for p in root.rglob("*.lnk"):
                name = p.stem.lower()
                if "uninstall" in name or "readme" in name or "help" == name:
                    continue
                apps.setdefault(name, str(p))
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        for p in desktop.glob("*.lnk"):
            apps.setdefault(p.stem.lower(), str(p))
    _start_menu_cache = apps
    return apps


def _launch(target: str):
    if IS_WIN:
        # ShellExecute directly (no cmd.exe, so no shell metacharacter injection). It resolves
        # App Paths names (msedge, winword...), URI schemes (ms-settings:) and .lnk shortcuts.
        os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", target])
    else:
        subprocess.Popen([target], start_new_session=True)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
class ToolBox:
    def __init__(self, memory=None, notify: Callable[[str], None] | None = None,
                 screenshot_dir: Path | None = None):
        self.memory = memory
        self.notify = notify or (lambda msg: None)
        self.screenshot_dir = screenshot_dir or (Path.home() / "Pictures" / "JARVIS")
        self.tools: dict[str, Tool] = {}
        self._timers: list[threading.Timer] = []
        self._net_prev = (psutil.net_io_counters(), time.time())
        self._register()

    def _add(self, name, desc, params, required, fn, llm=True):
        self.tools[name] = Tool(name, desc, params, required, fn, llm)

    def schemas(self, exclude: set[str] | frozenset = frozenset()) -> list[dict]:
        return [t.schema() for t in self.tools.values() if t.llm and t.name not in exclude]

    def call(self, name: str, args: dict | None) -> str:
        tool = self.tools.get(name)
        if not tool:
            return f"Unknown tool '{name}'."
        args = args or {}
        try:
            log.info("tool %s(%s)", name, args)
            return tool.fn(**{k: v for k, v in args.items() if k in tool.params})
        except Exception as exc:
            log.exception("tool %s failed", name)
            return f"The {name.replace('_', ' ')} command failed: {exc}"

    # ------------------------------------------------------------------
    def _register(self):
        S = lambda d: {"type": "string", "description": d}
        I = lambda d: {"type": "integer", "description": d}
        self._add("open_application", "Open/launch an application or program on the computer.",
                  {"name": S("Application name, e.g. 'notepad', 'spotify', 'calculator'")}, ["name"],
                  self.open_application)
        self._add("close_application", "Close a running application by name.",
                  {"name": S("Application/process name, e.g. 'notepad'")}, ["name"], self.close_application)
        self._add("open_website", "Open a website or URL in the default browser.",
                  {"url": S("URL or domain, e.g. 'github.com'")}, ["url"], self.open_website)
        self._add("search_web", "Search the web, YouTube or Wikipedia in the browser.",
                  {"query": S("What to search for"),
                   "site": {"type": "string", "enum": ["google", "youtube", "wikipedia", "maps"],
                            "description": "Where to search (default google)"}}, ["query"], self.search_web)
        self._add("open_folder", "Open a folder in File Explorer (desktop, documents, downloads, pictures, "
                  "music, videos, home, or a full path).", {"name": S("Folder name or path")}, ["name"],
                  self.open_folder)
        self._add("system_status", "Report CPU, memory, disk, battery and network status of this computer.",
                  {}, [], self.system_status)
        self._add("get_datetime", "Get the current local date and time.", {}, [], self.get_datetime)
        self._add("set_volume", "Change the system volume.",
                  {"action": {"type": "string", "enum": ["up", "down", "mute"], "description": "Volume action"},
                   "amount": I("Percent to change by (default 10)")}, ["action"], self.set_volume)
        self._add("media_control", "Control media playback.",
                  {"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"],
                              "description": "Media action"}}, ["action"], self.media_control)
        self._add("take_screenshot", "Take a screenshot of the screen and save it.", {}, [], self.take_screenshot)
        self._add("lock_computer", "Lock the computer.", {}, [], self.lock_computer)
        self._add("set_timer", "Set a countdown timer / reminder.",
                  {"seconds": I("Duration in seconds"), "label": S("What the timer is for")}, ["seconds"],
                  self.set_timer)
        self._add("remember", "Store a long-term fact about the user or their preferences.",
                  {"key": S("Short snake_case label, e.g. 'favourite_colour'"), "value": S("The fact")},
                  ["key", "value"], self.remember)
        self._add("forget", "Delete stored long-term facts matching a topic.",
                  {"topic": S("Topic to forget")}, ["topic"], self.forget)
        self._add("recall", "List what is stored in long-term memory about the user.", {}, [], self.recall)
        self._add("power", "Shut down, restart or sleep the computer (requires confirmation).",
                  {"action": S("shutdown | restart | sleep")}, ["action"], self.power, llm=False)

    # ------------------------------------------------------------- tools
    def open_application(self, name: str) -> str:
        key = name.lower().strip().strip(".").removeprefix("the ").removesuffix(" app")
        # only plain application names: no paths, shell metacharacters or script files
        if not re.fullmatch(r"[a-z0-9][a-z0-9 ._+-]{0,60}", key) or ".." in key or \
                re.search(r"\.(bat|cmd|ps1|vbs|js|py|msi|reg|scr|hta)$", key):
            return f"I can't open '{name}' – please give me a plain application name."
        target = APP_ALIASES.get(key)
        label = name
        if not target and IS_WIN:
            apps = _start_menu_apps()
            if key in apps:
                target, label = apps[key], key
            else:
                contains = [n for n in apps if key in n]
                close = contains or difflib.get_close_matches(key, apps.keys(), n=1, cutoff=0.6)
                if close:
                    best = min(close, key=len)
                    target, label = apps[best], best
        if not target:
            exe = shutil.which(key) or shutil.which(key.replace(" ", ""))
            if exe and os.path.basename(exe).lower().endswith((".exe", "")) and not exe.lower().endswith(
                    (".bat", ".cmd", ".ps1")):
                target = exe
        if not target:
            if IS_WIN and "." not in key:
                target = key.replace(" ", "")  # let ShellExecute resolve registered App Paths
            else:
                return f"I couldn't find an application called {name}."
        _launch(target)
        return f"Opening {label.title()}."

    def close_application(self, name: str) -> str:
        key = name.lower().strip().removesuffix(".exe")
        alias = APP_ALIASES.get(key, key)
        alias = Path(alias).stem.lower()
        protected = {"explorer", "python", "pythonw", "system", "csrss", "winlogon", "svchost", "ollama"}
        killed = 0
        for p in psutil.process_iter(["name"]):
            pname = (p.info.get("name") or "").lower().removesuffix(".exe")
            if pname in protected:
                continue
            if pname == alias or pname == key.replace(" ", ""):
                try:
                    p.terminate()
                    killed += 1
                except psutil.Error:
                    pass
        return f"Closed {name}." if killed else f"{name.title()} doesn't appear to be running."

    def open_website(self, url: str) -> str:
        url = url.strip()
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url, flags=re.I):
            url = "https://" + url
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return "I can only open ordinary web addresses (http or https)."
        webbrowser.open(url)
        host = urllib.parse.urlparse(url).netloc or url
        return f"Opening {host}."

    def search_web(self, query: str, site: str = "google") -> str:
        q = urllib.parse.quote_plus(query)
        urls = {"google": f"https://www.google.com/search?q={q}",
                "youtube": f"https://www.youtube.com/results?search_query={q}",
                "wikipedia": f"https://en.wikipedia.org/w/index.php?search={q}",
                "maps": f"https://www.google.com/maps/search/{q}"}
        webbrowser.open(urls.get(site or "google", urls["google"]))
        where = {"google": "the web", None: "the web"}.get(site, site.title())
        return f"Searching {where} for {query}."

    def open_folder(self, name: str) -> str:
        n = name.lower().strip().removeprefix("my ").removesuffix(" folder")
        home = Path.home()
        known = {"desktop": home / "Desktop", "documents": home / "Documents", "downloads": home / "Downloads",
                 "pictures": home / "Pictures", "photos": home / "Pictures", "music": home / "Music",
                 "videos": home / "Videos", "home": home, "user": home,
                 "screenshots": self.screenshot_dir}
        path = known.get(n) or Path(os.path.expandvars(os.path.expanduser(name)))
        if not path.is_dir():
            # OneDrive-redirected folders
            od = home / "OneDrive" / path.name
            if od.is_dir():
                path = od
            else:
                return f"I couldn't find the folder {name}."
        if IS_WIN:
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(path)])
        return f"Opening {path.name or str(path)}."

    def system_status(self) -> str:
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.home().anchor or "/"))
        parts = [f"CPU at {cpu:.0f} percent", f"memory {mem.percent:.0f} percent used "
                 f"({mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB)",
                 f"system drive {disk.percent:.0f} percent full"]
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            parts.append(f"battery {bat.percent:.0f} percent{' and charging' if bat.power_plugged else ''}")
        up = time.time() - psutil.boot_time()
        parts.append(f"uptime {int(up // 3600)} hours {int(up % 3600 // 60)} minutes")
        return "System status: " + ", ".join(parts) + "."

    def get_datetime(self) -> str:
        now = dt.datetime.now()
        return now.strftime("It is %H:%M on %A %d %B %Y.")

    def set_volume(self, action: str, amount: int = 10) -> str:
        action = action.lower()
        if action == "mute":
            _press(VK["volume_mute"])
            return "Toggling mute."
        steps = max(1, int(amount or 10) // 2)
        _press(VK["volume_up" if action == "up" else "volume_down"], steps)
        return f"Volume {'up' if action == 'up' else 'down'}."

    def media_control(self, action: str) -> str:
        action = {"play": "play_pause", "pause": "play_pause", "resume": "play_pause", "skip": "next",
                  "back": "previous"}.get(action, action)
        _press(VK.get(action, VK["play_pause"]))
        return {"play_pause": "Toggling playback.", "next": "Next track.", "previous": "Previous track.",
                "stop": "Stopping playback."}.get(action, "Done.")

    def take_screenshot(self) -> str:
        from PIL import ImageGrab
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / dt.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return f"Screenshot saved to {path.parent.name} as {path.name}."

    def lock_computer(self) -> str:
        if not IS_WIN:
            return "Locking is only supported on Windows."
        ctypes.windll.user32.LockWorkStation()
        return "Locking the workstation."

    def set_timer(self, seconds: int, label: str = "") -> str:
        seconds = int(seconds)
        if seconds <= 0:
            return "That timer duration doesn't make sense."
        label = (label or "").strip()

        def fire():
            self.notify(f"Timer complete{': ' + label if label else ''}.")
        t = threading.Timer(seconds, fire)
        t.daemon = True
        t.start()
        self._timers.append(t)
        return f"Timer set for {_fmt_duration(seconds)}{' for ' + label if label else ''}."

    def remember(self, key: str, value: str) -> str:
        if not self.memory:
            return "Memory is unavailable."
        k = self.memory.upsert_fact(key, value, source="user")
        return f"Noted. I'll remember that ({k.replace('_', ' ')})."

    def forget(self, topic: str) -> str:
        if not self.memory:
            return "Memory is unavailable."
        removed = self.memory.forget(topic)
        return f"Forgotten: {', '.join(r.replace('_', ' ') for r in removed)}." if removed \
            else f"I had nothing stored about {topic}."

    def recall(self) -> str:
        if not self.memory:
            return "Memory is unavailable."
        facts = self.memory.facts()
        if not facts:
            return "My long-term memory about you is currently empty."
        return "Here is what I remember: " + "; ".join(f"{f.key.replace('_', ' ')}: {f.value}" for f in facts[:25]) + "."

    def power(self, action: str) -> str:
        if not IS_WIN:
            return "Power controls are only supported on Windows."
        action = action.lower()
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "10"], creationflags=0x08000000)
            return "Shutting down in ten seconds."
        if action == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "10"], creationflags=0x08000000)
            return "Restarting in ten seconds."
        if action == "sleep":
            ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
            return "Going to sleep."
        return "Unknown power action."

    def cancel_timers(self):
        for t in self._timers:
            t.cancel()


def _fmt_duration(s: int) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if sec:
        parts.append(f"{sec} second{'s' if sec != 1 else ''}")
    return " ".join(parts) or "0 seconds"


# ---------------------------------------------------------------------------
# Deterministic intent router (fast path, no LLM round-trip)
# ---------------------------------------------------------------------------
_POLITE = re.compile(r"^(?:(?:hey|ok|okay|yo)\s+)?(?:jarvis[,!.]?\s*)?(?:please\s+|can you\s+|could you\s+|would you\s+|"
                     r"will you\s+|i want you to\s+|go ahead and\s+)*", re.I)
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "forty-five": 45, "sixty": 60, "ninety": 90, "half an": 0.5}
FOLDERS = {"desktop", "documents", "downloads", "pictures", "photos", "music", "videos", "home", "screenshots"}


def normalise_command(text: str) -> str:
    t = text.strip()
    t = _POLITE.sub("", t)
    t = re.sub(r"[?!.]+$", "", t).strip()
    t = re.sub(r"\s+(please|for me|now)$", "", t, flags=re.I)
    return t


def match_intent(text: str) -> tuple[str, dict] | None:
    t = normalise_command(text)
    low = t.lower()
    if not low:
        return None

    if re.fullmatch(r"(what(?:'s| is) the time|what time is it|time|tell me the time|"
                    r"what(?:'s| is) (?:the |today's )?date|what day is (?:it|today)|what(?:'s| is) today)", low):
        return "get_datetime", {}
    if re.fullmatch(r"(?:run |show |give me )?(?:a )?(system (status|diagnostics|report|check)|diagnostics|"
                    r"status report|how(?:'s| is) (?:my|the) (?:computer|pc|system)(?: doing)?|cpu usage|"
                    r"battery(?: level| status)?)", low):
        return "system_status", {}
    if re.fullmatch(r"(?:take|grab|capture) (?:a )?screen ?shot(?: of (?:the|my) screen)?", low):
        return "take_screenshot", {}
    if re.fullmatch(r"lock (?:the |my )?(?:computer|pc|screen|workstation|laptop)", low):
        return "lock_computer", {}

    m = re.fullmatch(r"(?:turn (?:the )?volume (up|down)|volume (up|down)|(increase|raise|lower|decrease|reduce) "
                     r"(?:the )?volume)(?: by (\d+)(?: ?%| percent)?)?", low)
    if m:
        word = next(g for g in m.groups()[:3] if g)
        up = word in ("up", "increase", "raise")
        return "set_volume", {"action": "up" if up else "down", "amount": int(m.group(4) or 10)}
    if re.fullmatch(r"(?:mute|unmute)(?: (?:the )?(?:volume|sound|audio|computer))?|(?:toggle )?mute", low):
        return "set_volume", {"action": "mute"}
    m = re.fullmatch(r"(pause|play|resume|stop|next|skip|previous)(?: (?:the )?(?:music|song|track|playback|video|media))?"
                     r"(?: track| song)?", low)
    if m:
        return "media_control", {"action": {"pause": "play_pause", "play": "play_pause", "resume": "play_pause",
                                            "skip": "next"}.get(m.group(1), m.group(1))}

    m = re.fullmatch(r"(?:set |start )?(?:a )?timer (?:for )?(.+?)(?: (?:for|called|named) (.+))?", low) or \
        re.fullmatch(r"remind me in (.+?) (?:to|about) (.+)", low)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            return "set_timer", {"seconds": secs, "label": (m.group(2) or "").strip()}

    m = re.fullmatch(r"(?:search|google|look up)(?: (?:the web|google|online))?(?: for)? (.+?)(?: on (youtube|wikipedia|google|maps))?", low)
    if m:
        return "search_web", {"query": m.group(1), "site": m.group(2) or "google"}
    m = re.fullmatch(r"(?:search youtube for|play (.+?) on youtube|youtube) ?(.*)", low)
    if m and (m.group(1) or m.group(2)):
        return "search_web", {"query": m.group(1) or m.group(2), "site": "youtube"}

    m = re.fullmatch(r"(?:open|launch|start|run|fire up|load up|bring up)(?: up)? (.+)", t, flags=re.I)
    if m:
        target = m.group(1).strip()
        tl = target.lower().removeprefix("the ").removeprefix("my ")
        tl_f = tl.removesuffix(" folder")
        if tl_f in FOLDERS or re.match(r"^[a-z]:\\", tl):
            return "open_folder", {"name": tl_f}
        if re.search(r"\.(com|org|net|io|co|uk|dev|ai|gov|edu|tv|me|app)\b|^https?://|^www\.", tl):
            return "open_website", {"url": tl.replace(" dot ", ".")}
        if tl.endswith(" website") or tl.endswith(" site"):
            return "open_website", {"url": re.sub(r"\s+(website|site)$", "", tl).replace(" ", "") + ".com"}
        if len(tl.split()) <= 4:
            return "open_application", {"name": tl}
    m = re.fullmatch(r"(?:close|quit|exit|kill) (.+)", low)
    if m and len(m.group(1).split()) <= 3 and m.group(1) not in ("yourself", "jarvis", "this", "it"):
        return "close_application", {"name": m.group(1).removeprefix("the ")}

    m = re.fullmatch(r"remember (?:that )?(.+)", t, flags=re.I)
    if m:
        fact = m.group(1).strip()
        mm = re.match(r"my (.+?) (?:is|are) (.+)", fact, flags=re.I)
        if mm and len(mm.group(1).split()) <= 4:
            return "remember", {"key": mm.group(1), "value": mm.group(2)}
        return "remember", {"key": _auto_key(fact), "value": fact}
    m = re.fullmatch(r"forget (?:about |that )?(.+)", low)
    if m and m.group(1) not in ("it", "everything"):
        return "forget", {"topic": m.group(1)}
    if re.fullmatch(r"what do you (?:remember|know) about me|what have you remembered|list (?:your )?memories|"
                    r"show (?:me )?(?:your )?memor(?:y|ies)", low):
        return "recall", {}

    m = re.fullmatch(r"(shut ?down|turn off|power off|restart|reboot|sleep)(?: (?:the |my )?(?:computer|pc|system|laptop))?", low)
    if m:
        w = m.group(1).replace(" ", "")
        return "power", {"action": {"shutdown": "shutdown", "turnoff": "shutdown", "poweroff": "shutdown",
                                    "restart": "restart", "reboot": "restart", "sleep": "sleep"}[w]}
    return None


def parse_duration(text: str) -> int | None:
    text = text.lower().replace("-", " ")
    total = 0.0
    found = False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|half an|an|a|one|two|three|four|five|six|seven|eight|nine|ten|"
                                r"fifteen|twenty|thirty|forty five|forty|sixty|ninety)\s*(hours?|hrs?|h|minutes?|mins?|m|"
                                r"seconds?|secs?|s)\b", text):
        n = float(num) if num[0].isdigit() else _NUM.get(num.replace(" ", "-"), _NUM.get(num, 0))
        mult = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
        total += n * mult
        found = True
    return int(total) if found and total > 0 else None


def _auto_key(fact: str) -> str:
    f = fact.lower()
    m = re.match(r"my (.+?) (?:is|are|was) ", f)
    if m:
        return m.group(1)
    m = re.match(r"i (like|love|prefer|hate|dislike|am allergic to) (.+)", f)
    if m:
        return f"{m.group(1)}_{' '.join(m.group(2).split()[:3])}"
    words = [w for w in re.findall(r"[a-z]+", f) if len(w) > 2][:4]
    return "_".join(words) or "note"
