"""OpenAI wrapper. Every call degrades gracefully to None so the app works without AI."""
import json
import logging
import re
from datetime import date
from app.core.config import get_settings
from app.prompts import news_prompt, planning_prompt, review_prompt
from app.services import task_service as ts
from app.services.scoring_service import LEETCODE_TOPICS

log = logging.getLogger("fenris.ai")


def _loads(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except Exception:
            return None


class AIService:
    def __init__(self):
        self._client = None
        self._usage: dict = {}

    def allow(self, user_id: int) -> bool:
        """Per-user daily budget of AI-assisted messages (in-memory; resets daily/on restart)."""
        key = (user_id, date.today())
        self._usage = {k: v for k, v in self._usage.items() if k[1] == key[1]}
        if self._usage.get(key, 0) >= get_settings().ai_daily_limit:
            return False
        self._usage[key] = self._usage.get(key, 0) + 1
        return True

    @property
    def configured(self) -> bool:
        return bool(get_settings().openai_api_key)

    def _get(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=get_settings().openai_api_key, timeout=40)
        return self._client

    def complete(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 900) -> str | None:
        if not self.configured:
            return None
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        variants = [{"max_tokens": max_tokens, "temperature": 0.3}, {"max_completion_tokens": max_tokens * 3}]
        for kw in variants:  # second variant covers newer models with different parameter rules
            try:
                if json_mode:
                    kw = dict(kw, response_format={"type": "json_object"})
                r = self._get().chat.completions.create(model=get_settings().openai_model, messages=msgs, **kw)
                return r.choices[0].message.content
            except Exception as e:  # never log the key or request body
                log.error("OpenAI call failed: %s", type(e).__name__)
        return None

    # ---- features
    def parse_message(self, message: str, today: date, tz: str) -> dict | None:
        sys = planning_prompt.INTENT_SYSTEM.format(today=today.isoformat(), weekday=today.strftime("%A"), tz=tz)
        return _loads(self.complete(sys, message, json_mode=True, max_tokens=400))

    def chat(self, system: str, context: str, history: list[dict], message: str) -> str | None:
        hist = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history[-8:])
        return self.complete(system, f"{context}\n\nCONVERSATION\n{hist}\n\nUSER MESSAGE\n{message}", max_tokens=700)

    def summarize_news(self, items: list[dict]) -> dict:
        payload = json.dumps([{k: i[k] for k in ("id", "title", "source", "verification", "summary", "also_reported_by")}
                              for i in items], ensure_ascii=False)
        data = _loads(self.complete(news_prompt.NEWS_SYSTEM, f"ITEMS\n{payload}",
                                    json_mode=True, max_tokens=1500))
        return (data or {}).get("items", {}) if data else {}

    def coach_note(self, data: dict) -> str | None:
        return self.complete(review_prompt.COACH_SYSTEM, json.dumps(data, default=str), max_tokens=200)


ai = AIService()

_STRIP = re.compile(r"\b(?:by|on|before|due|until)?\s*(?:next week|this week|today|tonight|tomorrow|tmrw|"
                    r"day after tomorrow|in \d+ days?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                    r"\d{4}-\d{2}-\d{2})\b", re.I)


def rule_parse(message: str, today: date) -> dict:
    """Offline fallback parser: simple patterns covering the documented examples."""
    m, low = message.strip(), message.lower().strip()
    out = {"intent": "chat"}
    mins = None
    if mm := re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\b", low):
        mins = int(float(mm.group(1)) * 60)
    elif mm := re.search(r"(\d+)\s*(?:m|min|mins|minutes?)\b", low):
        mins = int(mm.group(1))
    if re.match(r"^(good )?morning\b", low):
        return {"intent": "morning"}
    if re.search(r"\b(end my day|end the day|evening review|wrap up my day)\b", low):
        return {"intent": "end_day"}
    if re.search(r"\bplan (my|the) day\b", low):
        return {"intent": "plan_day"}
    if re.search(r"\b(news|briefing|headlines)\b", low):
        return {"intent": "show_news"}
    if "procrastinat" in low:
        return {"intent": "procrastination"}
    if re.search(r"how am i doing|my progress|how (?:is|was|did) .*(?:this|last) (?:month|week)", low):
        return {"intent": "stats"}
    if re.search(r"what'?s (important|urgent)|what should i (do|focus)|my priorities", low):
        return {"intent": "whats_important"}
    if mm := re.match(r"^(?:remember|note)(?: that)?[: ]+(.+)", m, re.I):
        return {"intent": "remember", "content": mm.group(1)}
    if re.search(r"leetcode|\blc\b", low) and re.search(r"finish|did|done|solved|complet|practic|spent|worked", low):
        topic = next((t for t in LEETCODE_TOPICS if t.lower() in low), None)
        return {"intent": "log_leetcode", "minutes": mins, "topic": topic}
    if mins and re.match(r"^(i )?(studied|learned|learnt|watched|did|completed|finished|worked on)\b", low):
        tm = re.search(r"(?:studied|learned|learnt|watched|worked on)\s+(.+?)(?:\s+for\b|\s+\d|$)", low)
        return {"intent": "log_course", "minutes": mins, "topic": tm.group(1).strip() if tm else ""}
    if mm := re.match(r"^i (?:couldn'?t|could not|didn'?t|did not) (?:finish|complete|do) (.+)", low):
        return {"intent": "couldnt_finish", "task_ref": mm.group(1)}
    if mm := re.match(r"^(?:move|postpone|push|reschedule)\s+(?:the\s+)?(.+?)\s+to\s+(.+)$", low):
        return {"intent": "postpone_task", "task_ref": mm.group(1),
                "new_deadline": ts.parse_deadline_text(mm.group(2), today).isoformat() if ts.parse_deadline_text(mm.group(2), today) else None}
    if mm := re.match(r"^(?:i )?(?:finished|completed|done with|done)\s+(.+)", low):
        return {"intent": "complete_task", "task_ref": mm.group(1).rstrip(".")}
    if mm := re.match(r"^(?:add(?: task)?|remind me to|i need to|todo|task)[: ]+(.+)", m, re.I):
        raw = mm.group(1)
        dl = ts.parse_deadline_text(raw, today)
        title = re.sub(r"\s+", " ", _STRIP.sub("", raw)).strip(" .,-") or raw
        est = None
        if em := re.search(r"(\d+)\s*(min|minutes|hour|hours|hr)\b", raw, re.I):
            est = int(em.group(1)) * (60 if em.group(2).lower().startswith("h") else 1)
        urgent = bool(re.search(r"urgent|asap|critical", raw, re.I))
        pr = "high" if urgent or (dl and (dl.date() - today).days <= 1) else "medium"
        cat = "learning" if re.search(r"study|learn|course|read", raw, re.I) else "general"
        return {"intent": "add_task", "title": title[:1].upper() + title[1:], "deadline": dl.isoformat() if dl else None,
                "priority": pr, "estimated_minutes": est, "category": cat}
    return out
