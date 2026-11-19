"""R.U.F.U.S research engine: web search -> read pages -> rank passages -> cited report.

Free and key-less:
  * Web search through DuckDuckGo & friends via the `ddgs` package (or your own SearXNG instance)
  * Wikipedia's public API for reliable background
  * Pages fetched with requests and cleaned with trafilatura (BeautifulSoup fallback)
  * The local Ollama model writes the report, citing sources as [n]

Only R.U.F.U.S uses the internet; JARVIS and A.T.O.M stay offline.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import logging
import math
import re
import threading
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field

import requests

from .. import perf

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36 JARVIS-Rufus/1.0")

DEPTHS = {
    #            sub-queries  results/query  pages to read  context chars
    "Quick":    (1, 6, 4, 9000),
    "Standard": (3, 8, 8, 16000),
    "Deep":     (5, 10, 14, 26000),
}

SKIP_DOMAINS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "x.com",
                "twitter.com", "pinterest.", "linkedin.com")

STOP = set("""a an the and or but if of in on at for to with by from is are was were be been being it its this
that these those what which who whom how why when where do does did can could would should will about into
than then there their they them i you he she we me my your our as not no""".split())

PLAN_PROMPT = """You are planning web research. Break the research question into {n} focused web search \
queries that together cover it well (background, latest developments, data/statistics, different viewpoints). \
Keep each query short (3-8 words), like something you would type into a search engine.
Today's date: {today}.
Question: {q}
Return JSON only: {{"queries": ["...", "..."]}}"""

REPORT_PROMPT = """You are R.U.F.U.S., a meticulous research analyst. Using ONLY the numbered sources below, \
write a well-organised research report in Markdown answering: "{q}"

Requirements:
- Start with "## Summary": 3-5 sentences giving the direct answer.
- Then "## Key findings": 5-10 bullet points, each ending with citations like [1] or [2][5].
- Then 2-4 sections with descriptive "##" headings covering the detail, with citations.
- Then "## Uncertainties & differing views": note conflicts between sources, gaps or outdated information.
- Cite every factual claim with the source numbers. Never invent sources, facts, numbers or URLs.
- If the sources don't answer part of the question, say so plainly.
- Do NOT write a sources/references list – it is added automatically.
- British English. Today's date: {today}.

SOURCES:
{sources}"""

QUICK_PROMPT = """Answer the question using the web search results below. Be concise (2-5 sentences), \
spoken-style, no markdown. Mention where the information comes from (e.g. "according to BBC News"). \
If the results don't contain the answer, say so. Today's date: {today}.

Question: {q}

Results:
{results}"""


@dataclass
class Source:
    n: int
    title: str
    url: str
    snippet: str = ""
    kind: str = "web"           # web | news | wikipedia
    text: str = ""
    status: str = "found"       # found | reading | read | failed | skipped
    date: str = ""

    @property
    def domain(self) -> str:
        return urllib.parse.urlparse(self.url).netloc.removeprefix("www.")


@dataclass
class Report:
    query: str
    depth: str
    markdown: str = ""
    sources: list[Source] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    seconds: float = 0.0

    def full_markdown(self) -> str:
        used = [s for s in self.sources if s.status == "read" or s.snippet]
        refs = "\n".join(f"{s.n}. [{s.title or s.domain}]({s.url}) — {s.domain}" for s in used)
        stamp = dt.datetime.fromtimestamp(self.started).strftime("%d %B %Y, %H:%M")
        return (f"# {self.query}\n\n*R.U.F.U.S research · {self.depth} · {stamp} · {len(used)} sources*\n\n"
                f"{self.markdown.strip()}\n\n## Sources\n{refs}\n")


class ResearchError(RuntimeError):
    pass


class Researcher:
    def __init__(self, cfg, llm):
        self.cfg = cfg
        self.llm = llm
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})

    # ------------------------------------------------------------- search
    def web_search(self, query: str, max_results: int = 8, news: bool = False,
                   timelimit: str | None = None) -> list[dict]:
        """Returns [{title, url, snippet, date, kind}]. timelimit: 'd', 'w', 'm' or None."""
        backend = getattr(self.cfg, "search_backend", "ddgs")
        if backend == "searxng" and getattr(self.cfg, "searxng_url", ""):
            return self._searxng(query, max_results, news, timelimit)
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise ResearchError("The 'ddgs' package is missing. Run: pip install ddgs") from exc
        region = getattr(self.cfg, "search_region", "uk-en") or "wt-wt"
        try:
            with DDGS() as d:
                rows = (d.news(query, region=region, timelimit=timelimit, max_results=max_results) if news
                        else d.text(query, region=region, safesearch="moderate", timelimit=timelimit,
                                    max_results=max_results))
        except Exception as exc:
            raise ResearchError(f"Web search failed: {exc}") from exc
        out = []
        for r in rows or []:
            url = r.get("href") or r.get("url") or ""
            if url.startswith("http"):
                out.append({"title": r.get("title", ""), "url": url, "snippet": r.get("body", ""),
                            "date": r.get("date", ""), "kind": "news" if news else "web"})
        return out

    def _searxng(self, query, max_results, news, timelimit=None):
        base = self.cfg.searxng_url.rstrip("/")
        params = {"q": query, "format": "json", "categories": "news" if news else "general", "language": "en"}
        if timelimit in ("d", "w", "m", "y"):
            params["time_range"] = {"d": "day", "w": "week", "m": "month", "y": "year"}[timelimit]
        try:
            r = self.session.get(f"{base}/search", params=params, timeout=12)
            r.raise_for_status()
            rows = r.json().get("results", [])[:max_results]
        except Exception as exc:
            raise ResearchError(f"SearXNG search failed: {exc}") from exc
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", ""),
                 "date": x.get("publishedDate", "") or "", "kind": "news" if news else "web"}
                for x in rows if str(x.get("url", "")).startswith("http")]

    def wikipedia(self, query: str, n: int = 2) -> list[dict]:
        try:
            r = self.session.get("https://en.wikipedia.org/w/api.php", timeout=10, params={
                "action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n})
            titles = [x["title"] for x in r.json().get("query", {}).get("search", [])]
        except Exception as exc:
            log.debug("wikipedia search failed: %s", exc)
            return []
        out = []
        for t in titles:
            url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_"))
            out.append({"title": f"{t} – Wikipedia", "url": url, "snippet": "", "date": "", "kind": "wikipedia"})
        return out

    # -------------------------------------------------------------- pages
    def fetch_text(self, url: str, timeout: float = 12.0) -> str:
        if not url.startswith(("http://", "https://")):
            return ""
        if "wikipedia.org/wiki/" in url:
            text = self._wiki_text(url)
            if text:
                return text
        r = self.session.get(url, timeout=timeout, stream=True, allow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            r.close()
            raise ResearchError(f"unsupported content ({ctype.split(';')[0] or 'unknown'})")
        raw = b""
        for chunk in r.iter_content(65536):
            raw += chunk
            if len(raw) > 3_000_000:
                break
        r.close()
        html = raw.decode(r.encoding or "utf-8", errors="ignore")
        return extract_main_text(html, url)

    def _wiki_text(self, url: str) -> str:
        title = urllib.parse.unquote(url.split("/wiki/", 1)[1].split("#")[0])
        try:
            r = self.session.get("https://en.wikipedia.org/w/api.php", timeout=10, params={
                "action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "format": "json",
                "redirects": 1})
            pages = r.json().get("query", {}).get("pages", {})
            return next(iter(pages.values()), {}).get("extract", "") or ""
        except Exception:
            return ""

    # ----------------------------------------------------------- pipeline
    def run(self, query: str, depth: str = "Standard", *, on_step=None, on_source=None, on_token=None,
            stop: threading.Event | None = None) -> Report:
        step = on_step or (lambda s: None)
        src_cb = on_source or (lambda s: None)
        tok = on_token or (lambda t: None)
        stop = stop or threading.Event()
        n_q, per_q, n_pages, budget = DEPTHS.get(depth, DEPTHS["Standard"])
        report = Report(query=query, depth=depth)
        today = dt.date.today().strftime("%d %B %Y")

        # 1. plan
        queries = [query]
        if n_q > 1:
            step("Planning search strategy…")
            try:
                plan = self.llm.chat_json(self.cfg.model, [{"role": "user", "content": PLAN_PROMPT.format(
                    n=n_q, q=query, today=today)}], options={"temperature": 0.2}, keep_alive=self.cfg.keep_alive,
                    timeout=90)
                extra = [str(x).strip() for x in plan.get("queries", []) if str(x).strip()]
                queries = list(dict.fromkeys([query] + extra))[: n_q + 1]
            except Exception as exc:
                log.info("query planning failed (%s); using the question directly", exc)
        if stop.is_set():
            raise ResearchError("Research cancelled.")

        # 2. search
        found: dict[str, dict] = {}
        errors = []
        recent = bool(re.search(r"\b(latest|news|today|this week|this year|current|recent|20\d\d)\b", query, re.I))
        if perf.on("parallel_search") and (len(queries) > 1 or recent):
            step(f"Searching the web ({len(queries)} searches in parallel)…")
            jobs = [(q, False) for q in queries] + ([(queries[0], True)] if recent else [])
            with cf.ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
                futures = [pool.submit(self.web_search, q, 6 if news else per_q, news) for q, news in jobs]
                for f in futures:                       # keep the original query order
                    try:
                        for r in f.result():
                            found.setdefault(_canon(r["url"]), r)
                    except ResearchError as exc:
                        errors.append(str(exc))
            step(f"Searching the web ({len(queries)}/{len(queries)}): done")
            if stop.is_set():
                raise ResearchError("Research cancelled.")
        else:
            for i, q in enumerate(queries):
                step(f"Searching the web ({i + 1}/{len(queries)}): {q}")
                try:
                    for r in self.web_search(q, per_q):
                        found.setdefault(_canon(r["url"]), r)
                    if recent and i == 0:
                        for r in self.web_search(q, 6, news=True):
                            found.setdefault(_canon(r["url"]), r)
                except ResearchError as exc:
                    errors.append(str(exc))
                if stop.is_set():
                    raise ResearchError("Research cancelled.")
        step("Checking Wikipedia…")
        for r in self.wikipedia(query, 2 if n_q > 1 else 1):
            found.setdefault(_canon(r["url"]), r)
        if not found:
            raise ResearchError(errors[0] if errors else "No search results – check your internet connection.")

        candidates = [r for r in found.values() if not any(d in r["url"] for d in SKIP_DOMAINS)]
        candidates.sort(key=lambda r: (r["kind"] != "wikipedia", -_overlap(query, r["title"] + " " + r["snippet"])))
        candidates = _diversify(candidates)
        for i, r in enumerate(candidates[:max(n_pages + 6, 12)], 1):
            s = Source(i, r["title"], r["url"], r["snippet"], r["kind"], date=r.get("date", ""))
            report.sources.append(s)
            src_cb(s)
        step(f"Found {len(found)} results · reading the top {min(n_pages, len(report.sources))} pages…")

        # 3. read pages in parallel
        to_read = report.sources[:n_pages]
        for s in report.sources[n_pages:]:
            s.status = "skipped"
            src_cb(s)

        def read(s: Source):
            if stop.is_set():
                return s
            s.status = "reading"
            src_cb(s)
            try:
                s.text = self.fetch_text(s.url, timeout=8.0 if perf.on("fast_timeouts") else 12.0)
                s.status = "read" if len(s.text) > 200 else "failed"
            except Exception as exc:
                log.debug("fetch %s failed: %s", s.url, exc)
                s.status = "failed"
            src_cb(s)
            return s

        with cf.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(read, to_read))
        if stop.is_set():
            raise ResearchError("Research cancelled.")
        ok = [s for s in to_read if s.status == "read"]
        step(f"Read {len(ok)} of {len(to_read)} pages · selecting the most relevant passages…")

        # 4. rank passages and build the context
        context = build_context(query + " " + " ".join(queries[1:]), report.sources, budget)
        if not context.strip():
            raise ResearchError("Couldn't read any of the pages found. Try rephrasing, or a different depth.")

        # 5. write the report
        step("Writing the report…")
        options = {"temperature": 0.3, "num_ctx": max(int(self.cfg.num_ctx), 16384 if depth == "Deep" else 8192)}
        md = ""
        for kind, payload in self.llm.chat_stream(self.cfg.model, [
                {"role": "user", "content": REPORT_PROMPT.format(q=query, today=today, sources=context)}],
                options=options, keep_alive=self.cfg.keep_alive, stop=stop):
            if kind == "token":
                md += payload
                tok(payload)
        md = re.sub(r"<think>.*?</think>", "", md, flags=re.S).strip()
        md = re.sub(r"\n#+\s*(sources|references)\b.*$", "", md, flags=re.S | re.I).strip()
        report.markdown = md
        report.seconds = time.time() - report.started
        perf.record("research_report", report.seconds)
        step(f"Done in {report.seconds:.0f}s · {len(ok)} pages read · {len(report.sources)} sources found")
        return report

    def quick_answer(self, question: str) -> str:
        """Fast web lookup for chat: search snippets + a short answer."""
        rows = self.web_search(question, 6)
        recent = re.search(r"\b(latest|news|today|score|weather|price|current)\b", question, re.I)
        if recent:
            try:
                rows = self.web_search(question, 4, news=True) + rows
            except ResearchError:
                pass
        if not rows:
            return "I couldn't find anything useful on that."
        results = "\n".join(f"- {r['title']} ({urllib.parse.urlparse(r['url']).netloc}"
                            f"{', ' + r['date'][:10] if r.get('date') else ''}): {r['snippet']}" for r in rows[:8])
        return results


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def extract_main_text(html: str, url: str = "") -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                                   favor_recall=True) or ""
        if len(text) > 200:
            return text
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            t.decompose()
        node = soup.find("article") or soup.find("main") or soup.body or soup
        paras = [p.get_text(" ", strip=True) for p in node.find_all(["p", "li", "h2", "h3", "td"])]
        return "\n".join(p for p in paras if len(p) > 40)
    except Exception:
        text = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text)


def _canon(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP]


def _overlap(query: str, text: str) -> float:
    q = set(_terms(query))
    if not q:
        return 0.0
    t = set(_terms(text))
    return len(q & t) / len(q)


def _diversify(rows: list[dict]) -> list[dict]:
    """At most two results per domain, keeping order."""
    seen = Counter()
    out = []
    for r in rows:
        d = urllib.parse.urlparse(r["url"]).netloc.removeprefix("www.")
        if seen[d] < 2:
            out.append(r)
            seen[d] += 1
    return out


def build_context(query: str, sources: list[Source], budget: int) -> str:
    """BM25-style passage ranking across all read pages, keeping every source represented."""
    q = _terms(query)
    passages: list[tuple[float, int, str]] = []
    docs = []
    for s in sources:
        if s.status != "read":
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n", s.text) if len(p.strip()) > 60]
        chunk, chunks = "", []
        for p in paras:
            if len(chunk) + len(p) < 700:
                chunk = (chunk + "\n" + p).strip()
            else:
                if chunk:
                    chunks.append(chunk)
                chunk = p[:1400]
        if chunk:
            chunks.append(chunk)
        docs.append((s, chunks))
    n_chunks = sum(len(c) for _, c in docs) or 1
    df = Counter()
    for _, chunks in docs:
        for c in chunks:
            for t in set(_terms(c)):
                df[t] += 1
    for s, chunks in docs:
        for idx, c in enumerate(chunks):
            tf = Counter(_terms(c))
            length = sum(tf.values()) or 1
            score = 0.0
            for t in q:
                if tf[t]:
                    idf = math.log(1 + (n_chunks - df[t] + 0.5) / (df[t] + 0.5))
                    score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * length / 120))
            score += 0.15 / (1 + idx)          # earlier passages often summarise the page
            passages.append((score, s.n, c))
    passages.sort(key=lambda x: -x[0])
    # guarantee each source gets its best passage first, then fill by score
    chosen: dict[int, list[str]] = {}
    used = 0
    best_per_source = {}
    for sc, n, c in passages:
        best_per_source.setdefault(n, (sc, c))
    for n, (sc, c) in sorted(best_per_source.items(), key=lambda kv: -kv[1][0]):
        if used + len(c) > budget:
            break
        chosen.setdefault(n, []).append(c)
        used += len(c)
    for sc, n, c in passages:
        if used >= budget:
            break
        if c in chosen.get(n, []) or len(chosen.get(n, [])) >= 4 or used + len(c) > budget:
            continue
        chosen.setdefault(n, []).append(c)
        used += len(c)
    by_n = {s.n: s for s in sources}
    blocks = []
    for n in sorted(chosen):
        s = by_n[n]
        head = f"[{n}] {s.title} ({s.domain}{', ' + s.date[:10] if s.date else ''})"
        blocks.append(head + "\n" + "\n…\n".join(chosen[n]))
    # include unread sources' snippets as weak evidence
    for s in sources:
        if s.status != "read" and s.snippet and used < budget:
            blocks.append(f"[{s.n}] {s.title} ({s.domain}) – search snippet only: {s.snippet}")
            used += len(s.snippet)
    return "\n\n".join(blocks)


def research_intent(text: str) -> str | None:
    low = text.strip().rstrip(".?!")
    low = re.sub(r"^(?:(?:hey\s+)?(?:rufus|roofus|rufous)[,!.]?\s+)?(?:please\s+|can you\s+|could you\s+)?", "",
                 low, flags=re.I)
    m = re.fullmatch(r"(?:research|investigate|deep dive (?:into|on)|do (?:some )?research (?:on|into)|"
                     r"find out (?:everything |all )?about|look into|dig into)\s+(.+)", low, re.I)
    return m.group(1).strip() if m else None
