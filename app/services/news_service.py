"""Trusted-news aggregation: tiered sources, dedup, cross-checking, scoring, briefing selection."""
import hashlib
import html
import logging
import re
import threading
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse
import feedparser
import httpx
from sqlalchemy import select
from app.core.timeutil import utcnow
from app.models import NewsItem
from app.services.ai_service import ai

log = logging.getLogger("fenris.news")
CATEGORIES = ("technology", "finance", "india", "world")
BASE_TRUST = {1: 95, 2: 80, 3: 40}
TIER_POINTS = {1: 25, 2: 20, 3: 5}

TIER1_DOMAINS = ("rbi.org.in", "sebi.gov.in", "pib.gov.in", "eci.gov.in", "sci.gov.in", "federalreserve.gov", "sec.gov",
                 "whitehouse.gov", "imf.org", "worldbank.org", "un.org", "europa.eu", "ecb.europa.eu", "bis.org")
TIER2_DOMAINS = ("reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "thehindu.com", "indianexpress.com", "ft.com", "wsj.com")
PUBLISHERS = {"reuters": 2, "associated press": 2, "ap": 2, "ap news": 2, "bbc": 2, "bbc news": 2, "the hindu": 2,
              "indian express": 2, "the indian express": 2, "financial times": 2, "wall street journal": 2,
              "the wall street journal": 2, "wsj": 2, "press information bureau": 1, "pib": 1,
              "reserve bank of india": 1, "sebi": 1, "federal reserve": 1, "u.s. securities and exchange commission": 1}

# Edit this list to add/remove feeds. tier 1 = primary/official, 2 = trusted journalism.
SOURCES = [
    {"name": "Reserve Bank of India", "url": "https://rbi.org.in/pressreleases_rss.xml", "tier": 1, "category": "finance"},
    {"name": "SEBI", "url": "https://www.sebi.gov.in/sebirss.xml", "tier": 1, "category": "finance"},
    {"name": "PIB", "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "tier": 1, "category": "india"},
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "tier": 1, "category": "finance"},
    {"name": "SEC", "url": "https://www.sec.gov/news/pressreleases.rss", "tier": 1, "category": "finance"},
    {"name": "United Nations", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "tier": 1, "category": "world"},
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/technology/rss.xml", "tier": 2, "category": "technology"},
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/business/rss.xml", "tier": 2, "category": "finance"},
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "tier": 2, "category": "world"},
    {"name": "The Hindu", "url": "https://www.thehindu.com/sci-tech/technology/feeder/default.rss", "tier": 2, "category": "technology"},
    {"name": "The Hindu", "url": "https://www.thehindu.com/business/feeder/default.rss", "tier": 2, "category": "finance"},
    {"name": "The Hindu", "url": "https://www.thehindu.com/news/national/feeder/default.rss", "tier": 2, "category": "india"},
    {"name": "The Hindu", "url": "https://www.thehindu.com/news/international/feeder/default.rss", "tier": 2, "category": "world"},
]
# Tier 3 discovery (Google News). The original publisher is read from each entry and re-classified.
DISCOVERY = {
    "technology": "AI OR Nvidia OR OpenAI OR semiconductor OR cybersecurity OR Microsoft OR Google",
    "finance": "RBI OR Sensex OR Nifty OR Federal Reserve OR inflation OR IPO",
    "india": "India government OR India economy OR Supreme Court India OR India policy",
    "world": "Ukraine OR Gaza OR China OR Europe OR Middle East OR geopolitics",
}
for _c, _q in DISCOVERY.items():
    for _extra in ("", " (site:reuters.com OR site:apnews.com)"):
        SOURCES.append({"name": "Google News", "tier": 3, "category": _c, "aggregator": True,
                        "url": f"https://news.google.com/rss/search?q={quote(_q + _extra)}+when:1d&hl=en-IN&gl=IN&ceid=IN:en"})

KEYWORDS = {
    "technology": ["ai", "openai", "nvidia", "google", "microsoft", "apple", "meta", "amazon", "chip", "semiconductor", "cyber", "robot", "launch"],
    "finance": ["rbi", "repo", "fed", "rate", "inflation", "gdp", "sensex", "nifty", "ipo", "merger", "oil", "bank", "market"],
    "india": ["supreme court", "parliament", "budget", "policy", "election", "government", "infrastructure", "security"],
    "world": ["war", "ceasefire", "china", "russia", "ukraine", "gaza", "sanction", "summit", "election", "nato"],
}
STOP = set("the a an of to in on for and or at by with from as is are was were be been has have had will after over new says say amid".split())
_LOCK = threading.Lock()
LAST = {"ok": None, "last_run": None, "fetched": 0, "inserted": 0, "failed_feeds": 0}


# ------------------------------------------------------------------ classification & hashing
def domain_of(url: str) -> str:
    h = urlparse(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _dom_match(d: str, doms) -> bool:
    return any(d == x or d.endswith("." + x) for x in doms)


def domain_tier(url: str) -> int | None:
    d = domain_of(url)
    if d.endswith(".gov.in") or d.endswith(".gov") or d.endswith("nic.in") or _dom_match(d, TIER1_DOMAINS):
        return 1
    return 2 if _dom_match(d, TIER2_DOMAINS) else None


def publisher_tier(name: str) -> int | None:
    n = (name or "").lower().strip()
    for k, t in PUBLISHERS.items():
        if n == k or (len(k) > 3 and k in n):
            return t
    return None


def classify_source(name: str, url: str = "") -> int:
    """Tier of the ORIGINAL publisher (an aggregator link never counts as trusted by itself)."""
    return domain_tier(url) or publisher_tier(name) or 3


def normalize_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())).strip()


def content_hash(source: str, title: str) -> str:
    return hashlib.sha256(f"{(source or '').lower()}|{normalize_title(title)}".encode()).hexdigest()


def tokens(title: str) -> set:
    return {w for w in normalize_title(title).split() if w not in STOP and len(w) > 2}


def similar(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) >= 3 and len(ta & tb) / len(ta | tb) >= 0.4


def cluster(items) -> list[list]:
    groups: list[list] = []
    for it in items:
        for g in groups:
            if any(similar(it.title, m.title) for m in g):
                g.append(it)
                break
        else:
            groups.append([it])
    return groups


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for i in items:
        h = content_hash(i["source"], i["title"])
        if h not in seen:
            seen.add(h)
            out.append(i)
    return out


def importance(i, now: datetime, interests: str = "") -> float:
    age_h = max(0.0, (now - i.published_at).total_seconds() / 3600)
    kw = sum(k in i.title.lower() for k in KEYWORDS.get(i.category, []))
    mine = sum(w in i.title.lower() for w in re.findall(r"[a-z]{3,}", interests.lower()))
    return round(max(0, 1 - age_h / 36) * 30 + TIER_POINTS.get(i.source_tier, 5) + min(25, (i.corroboration - 1) * 10)
                 + min(10, kw * 4) + min(10, mine * 5), 1)


def verification(i) -> str:
    if i.source_tier == 1:
        return "Official source"
    if i.corroboration >= 2 and i.source_tier <= 2:
        return f"Confirmed by {i.corroboration} sources"
    return "Single reputable source" if i.source_tier == 2 else "Unverified (discovery source only)"


# ------------------------------------------------------------------ collection
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_feed(content: bytes, src: dict) -> list[dict]:
    feed = feedparser.parse(content)
    out = []
    for e in feed.entries[:40]:
        title = _clean(e.get("title", ""))
        link = e.get("link", "")
        if not title or not link or not link.startswith(("http://", "https://")):
            continue
        publisher = src["name"]
        if src.get("aggregator"):
            publisher = (e.get("source") or {}).get("title") or ""
            if publisher and title.endswith(" - " + publisher):
                title = title[: -len(publisher) - 3]
            elif " - " in title:
                title, publisher = title.rsplit(" - ", 1)
            publisher = publisher or "Unknown publisher"
            tier = publisher_tier(publisher) or 3
        else:
            tier = src["tier"]
        tp = e.get("published_parsed") or e.get("updated_parsed")
        out.append({"title": title, "url": link, "source": publisher, "tier": tier, "category": src["category"],
                    "published_at": datetime(*tp[:6]) if tp else utcnow(),
                    "summary": _clean(e.get("summary", ""))[:400], "aggregator": bool(src.get("aggregator"))})
    return out


def collect_news(db, sources=None, client=None) -> dict:
    """Fetch every feed; one failing feed never stops the rest."""
    if not _LOCK.acquire(blocking=False):
        return dict(LAST, busy=True)  # a collection is already running
    try:
        return _collect(db, sources, client)
    finally:
        _LOCK.release()


def _collect(db, sources, client) -> dict:
    sources = sources or SOURCES
    own = client is None
    client = client or httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "Fenris/1.0 personal-assistant"})
    fetched = failed = inserted = 0
    cutoff = utcnow() - timedelta(days=3)
    try:
        for src in sources:
            try:
                r = client.get(src["url"])
                r.raise_for_status()
                entries = parse_feed(r.content, src)
            except Exception as e:
                failed += 1
                log.warning("Feed failed (%s): %s", src["name"], type(e).__name__)
                continue
            fetched += len(entries)
            for it in dedupe(entries):
                if it["published_at"] < cutoff:
                    continue
                h = content_hash(it["source"], it["title"])
                if db.scalar(select(NewsItem.id).where((NewsItem.content_hash == h) | (NewsItem.url == it["url"]))):
                    continue
                db.add(NewsItem(title=it["title"], url=it["url"], source=it["source"], source_tier=it["tier"],
                                category=it["category"], published_at=it["published_at"], summary=it["summary"],
                                content_hash=h, via_aggregator=it["aggregator"], trust_score=BASE_TRUST[it["tier"]]))
                inserted += 1
            db.commit()
    except Exception:
        db.rollback()
        log.exception("News collection error")
    finally:
        if own:
            client.close()
    recompute_scores(db)
    LAST.update(ok=failed < len(sources), last_run=utcnow().isoformat(), fetched=fetched, inserted=inserted, failed_feeds=failed)
    return dict(LAST)


def recompute_scores(db, interests: str = ""):
    now = utcnow()
    items = db.scalars(select(NewsItem).where(NewsItem.published_at >= now - timedelta(hours=48))).all()
    for g in cluster(items):
        names = {i.source.lower() for i in g}
        srcs = list({i.source: {"name": i.source, "url": i.url, "tier": i.source_tier} for i in g}.values())
        for i in g:
            i.corroboration = len(names)
            i.sources = srcs
            i.trust_score = min(100, BASE_TRUST[i.source_tier] + min(15, 5 * (len(names) - 1)))
            i.importance_score = importance(i, now, interests)
    db.commit()


# ------------------------------------------------------------------ briefing
def news_dict(i: NewsItem) -> dict:
    others = [s["name"] for s in (i.sources or []) if s["name"] != i.source]
    return {"id": i.id, "title": i.title, "url": i.url, "source": i.source, "source_tier": i.source_tier,
            "category": i.category, "published_at": i.published_at.isoformat() + "Z", "summary": i.summary,
            "importance_score": i.importance_score, "trust_score": i.trust_score, "corroboration": i.corroboration,
            "sources": i.sources or [], "also_reported_by": others, "verification": verification(i),
            "briefing": i.briefing}


def get_briefing(db, interests: str = "", analyze: bool = True) -> dict:
    now = utcnow()
    items = db.scalars(select(NewsItem).where(NewsItem.published_at >= now - timedelta(hours=36))
                       .order_by(NewsItem.importance_score.desc())).all()
    if not items:
        msg = "News service temporarily unavailable." if LAST["ok"] is False else \
            "No news collected yet. Use refresh to fetch the latest stories."
        return {"available": False, "message": msg, "top": [], "categories": {c: [] for c in CATEGORIES}, "last_run": LAST["last_run"]}
    pool: list[NewsItem] = []
    for i in items:  # one entry per story
        if not any(similar(i.title, p.title) for p in pool):
            pool.append(i)
    top = pool[:5]
    cats = {c: [i for i in pool if i.category == c and i not in top][:4] for c in CATEGORIES}
    if analyze and ai.configured:
        todo = [news_dict(i) for i in top if not i.briefing]
        if todo:
            res = ai.summarize_news(todo)
            for i in top:
                if str(i.id) in res:
                    i.briefing = res[str(i.id)]
            db.commit()
    return {"available": True, "message": None, "last_run": LAST["last_run"], "top": [news_dict(i) for i in top],
            "categories": {c: [news_dict(i) for i in v] for c, v in cats.items()}}


def what_matters(db, interests: str, briefing: dict) -> str | None:
    words = [w for w in re.findall(r"[a-z]{3,}", (interests or "").lower())]
    if not words:
        return None
    for i in briefing["top"] + [x for v in briefing["categories"].values() for x in v]:
        if any(w in i["title"].lower() for w in words):
            return f"Given your interests, watch: {i['title']} ({i['source']})."
    return None
