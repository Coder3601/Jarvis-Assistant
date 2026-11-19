"""Long-term memory: conversations, messages, facts and preferences in SQLite."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started REAL NOT NULL,
    title TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    source TEXT DEFAULT 'user',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,          -- YYYY-MM-DD
    time TEXT DEFAULT '',        -- HH:MM or ''
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due TEXT DEFAULT '',         -- YYYY-MM-DD or ''
    done INTEGER DEFAULT 0,
    created REAL NOT NULL,
    done_at REAL
);
CREATE TABLE IF NOT EXISTS research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    depth TEXT DEFAULT '',
    markdown TEXT NOT NULL,
    sources TEXT DEFAULT '[]',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created REAL NOT NULL,
    last_checked REAL
);
CREATE TABLE IF NOT EXISTS watch_seen (
    url TEXT PRIMARY KEY,
    topic_id INTEGER,
    seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
"""

STOPWORDS = set("""a an the and or but if then so to of in on at for with by from is are was were be been
being it its this that these those i you he she we they me my your our their what which who whom how why
when where do does did can could would should will shall may might must not no yes please just about
tell give show want need like know think jarvis hey hello hi okay ok""".split())


@dataclass
class Fact:
    id: int
    key: str
    value: str
    category: str
    source: str
    updated: float


def _norm_key(key: str) -> str:
    key = re.sub(r"[^a-z0-9 _-]+", "", key.lower()).strip()
    return re.sub(r"[\s-]+", "_", key)[:80] or "note"


class Memory:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(messages)").fetchall()}
        if "persona" not in cols:  # upgrade older databases
            self._db.execute("ALTER TABLE messages ADD COLUMN persona TEXT DEFAULT 'jarvis'")
        self.fts = True
        try:
            self._db.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 not available (%s); falling back to LIKE search", exc)
            self.fts = False

    # ----------------------------------------------------------- utilities
    def _q(self, sql: str, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql: str, args=()) -> int:
        with self._lock:
            cur = self._db.execute(sql, args)
            return cur.lastrowid

    def close(self):
        with self._lock:
            self._db.close()

    # ------------------------------------------------------- conversations
    def new_conversation(self) -> int:
        return self._x("INSERT INTO conversations(started) VALUES (?)", (time.time(),))

    def set_title(self, conv_id: int, title: str):
        self._x("UPDATE conversations SET title=? WHERE id=?", (title[:120], conv_id))

    def add_message(self, conv_id: int, role: str, content: str, persona: str = "jarvis") -> int:
        return self._x("INSERT INTO messages(conversation_id, role, content, ts, persona) VALUES (?,?,?,?,?)",
                       (conv_id, role, content, time.time(), persona))

    def recent_messages(self, limit: int = 20, persona: str | None = None) -> list[dict]:
        if persona:
            rows = self._q("SELECT id, role, content, ts, conversation_id FROM messages "
                           "WHERE COALESCE(persona, 'jarvis')=? ORDER BY id DESC LIMIT ?", (persona, limit))
        else:
            rows = self._q("SELECT id, role, content, ts, conversation_id FROM messages ORDER BY id DESC LIMIT ?",
                           (limit,))
        return [dict(r) for r in reversed(rows)]

    def messages_after(self, persona: str, after_id: int, limit: int) -> list[dict]:
        """Messages for one agent with id > after_id, oldest first (at most the newest `limit`)."""
        rows = self._q("SELECT id, role, content, ts FROM messages WHERE id > ? AND COALESCE(persona, 'jarvis')=? "
                       "ORDER BY id DESC LIMIT ?", (after_id, persona, limit))
        return [dict(r) for r in reversed(rows)]

    def conversation_messages(self, conv_id: int) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT id, role, content, ts FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))]

    def conversations(self, limit: int = 50) -> list[dict]:
        rows = self._q("""SELECT c.id, c.started, c.title, COUNT(m.id) AS n
                          FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id
                          GROUP BY c.id HAVING n > 0 ORDER BY c.id DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]

    def search_messages(self, text: str, limit: int = 4, exclude_after_id: int | None = None) -> list[dict]:
        words = [w for w in re.findall(r"[a-zA-Z0-9']{3,}", text.lower()) if w not in STOPWORDS]
        if not words:
            return []
        words = list(dict.fromkeys(words))[:8]
        try:
            if self.fts:
                q = " OR ".join(f'"{w}"' for w in words)
                rows = self._q("""SELECT m.id, m.role, m.content, m.ts FROM messages_fts f
                                  JOIN messages m ON m.id=f.rowid
                                  WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts) LIMIT ?""",
                               (q, limit * 3))
            else:
                clause = " OR ".join("content LIKE ?" for _ in words)
                rows = self._q(f"SELECT id, role, content, ts FROM messages WHERE {clause} "
                               f"ORDER BY id DESC LIMIT ?", (*[f"%{w}%" for w in words], limit * 3))
        except sqlite3.Error as exc:
            log.debug("search failed: %s", exc)
            return []
        out = []
        for r in rows:
            if exclude_after_id is not None and r["id"] >= exclude_after_id:
                continue
            out.append(dict(r))
            if len(out) >= limit:
                break
        return out

    def clear_history(self):
        with self._lock:
            self._db.execute("DELETE FROM messages")
            self._db.execute("DELETE FROM conversations")
            if self.fts:
                self._db.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

    # --------------------------------------------------------------- facts
    def upsert_fact(self, key: str, value: str, category: str = "general", source: str = "user") -> str:
        key = _norm_key(key)
        now = time.time()
        self._x("""INSERT INTO facts(key, value, category, source, created, updated) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, category=excluded.category,
                   source=excluded.source, updated=excluded.updated""",
                (key, value.strip(), category, source, now, now))
        return key

    def facts(self) -> list[Fact]:
        return [Fact(r["id"], r["key"], r["value"], r["category"], r["source"], r["updated"])
                for r in self._q("SELECT * FROM facts ORDER BY category, key")]

    def delete_fact(self, fact_id: int):
        self._x("DELETE FROM facts WHERE id=?", (fact_id,))

    def forget(self, text: str) -> list[str]:
        """Delete facts whose key or value matches the text. Returns removed keys."""
        words = [w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOPWORDS]
        removed = []
        for f in self.facts():
            hay = f"{f.key} {f.value}".lower()
            if words and all(w in hay for w in words):
                self.delete_fact(f.id)
                removed.append(f.key)
        return removed

    def clear_facts(self):
        self._x("DELETE FROM facts")

    # ------------------------------------------------------------ calendar
    def add_event(self, date: str, title: str, time_: str = "", notes: str = "") -> int:
        return self._x("INSERT INTO events(date, time, title, notes, created) VALUES (?,?,?,?,?)",
                       (date, time_ or "", title.strip(), notes or "", time.time()))

    def events_between(self, start: str, end: str) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date, CASE WHEN time='' THEN '99' ELSE time END",
            (start, end))]

    def events_on(self, date: str) -> list[dict]:
        return self.events_between(date, date)

    def event_dates(self, start: str, end: str) -> set[str]:
        return {r["date"] for r in self._q("SELECT DISTINCT date FROM events WHERE date BETWEEN ? AND ?",
                                           (start, end))}

    def delete_event(self, event_id: int):
        self._x("DELETE FROM events WHERE id=?", (event_id,))

    # --------------------------------------------------------------- tasks
    def add_task(self, title: str, due: str = "") -> int:
        return self._x("INSERT INTO tasks(title, due, created) VALUES (?,?,?)", (title.strip(), due or "", time.time()))

    def tasks(self, include_done: bool = True) -> list[dict]:
        sql = "SELECT * FROM tasks" + ("" if include_done else " WHERE done=0") + \
              " ORDER BY done, CASE WHEN due='' THEN '9999' ELSE due END, id"
        return [dict(r) for r in self._q(sql)]

    def set_task_done(self, task_id: int, done: bool):
        self._x("UPDATE tasks SET done=?, done_at=? WHERE id=?", (1 if done else 0, time.time() if done else None,
                                                                  task_id))

    def complete_task_matching(self, text: str) -> str | None:
        words = [w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOPWORDS]
        for t in self.tasks(include_done=False):
            hay = t["title"].lower()
            if text.lower().strip() in hay or (words and all(w in hay for w in words)):
                self.set_task_done(t["id"], True)
                return t["title"]
        return None

    def delete_task(self, task_id: int):
        self._x("DELETE FROM tasks WHERE id=?", (task_id,))

    def clear_done_tasks(self):
        self._x("DELETE FROM tasks WHERE done=1")

    # ------------------------------------------------------------ research
    def add_research(self, query: str, depth: str, markdown: str, sources_json: str) -> int:
        return self._x("INSERT INTO research(query, depth, markdown, sources, created) VALUES (?,?,?,?,?)",
                       (query, depth, markdown, sources_json, time.time()))

    def research_list(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id, query, depth, created FROM research ORDER BY id DESC LIMIT ?",
                                         (limit,))]

    def get_research(self, rid: int) -> dict | None:
        rows = self._q("SELECT * FROM research WHERE id=?", (rid,))
        return dict(rows[0]) if rows else None

    def delete_research(self, rid: int):
        self._x("DELETE FROM research WHERE id=?", (rid,))

    # ------------------------------------------------------------ watchlist
    def watch_topics(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM watch_topics ORDER BY topic COLLATE NOCASE")]

    def add_watch_topic(self, topic: str) -> bool:
        topic = topic.strip().strip(".")
        if not topic:
            return False
        try:
            self._x("INSERT INTO watch_topics(topic, created) VALUES (?,?)", (topic, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_watch_topic(self, text: str) -> str | None:
        low = text.lower().strip()
        for t in self.watch_topics():
            if t["topic"].lower() == low or low in t["topic"].lower() or t["topic"].lower() in low:
                self._x("DELETE FROM watch_topics WHERE id=?", (t["id"],))
                return t["topic"]
        return None

    def touch_watch_topic(self, topic_id: int):
        self._x("UPDATE watch_topics SET last_checked=? WHERE id=?", (time.time(), topic_id))

    def seen_urls(self, urls: list[str]) -> set[str]:
        if not urls:
            return set()
        marks = ",".join("?" * len(urls))
        return {r["url"] for r in self._q(f"SELECT url FROM watch_seen WHERE url IN ({marks})", urls)}

    def mark_seen(self, urls: list[str], topic_id: int):
        with self._lock:
            self._db.executemany("INSERT OR IGNORE INTO watch_seen(url, topic_id, seen) VALUES (?,?,?)",
                                 [(u, topic_id, time.time()) for u in urls])
            # keep the table small: forget items older than 60 days
            self._db.execute("DELETE FROM watch_seen WHERE seen < ?", (time.time() - 60 * 86400,))

    # --------------------------------------------------------- preferences
    def get_pref(self, key: str, default: str | None = None) -> str | None:
        rows = self._q("SELECT value FROM preferences WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_pref(self, key: str, value: str):
        self._x("INSERT INTO preferences(key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # --------------------------------------------------------------- stats
    def stats(self) -> dict:
        row = self._q("""SELECT (SELECT COUNT(*) FROM messages) AS messages,
                                (SELECT COUNT(*) FROM conversations) AS conversations,
                                (SELECT COUNT(*) FROM facts) AS facts""")[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return {**dict(row), "size_kb": size // 1024}
