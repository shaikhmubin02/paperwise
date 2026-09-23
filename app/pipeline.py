"""Ingest pipeline: Read -> Analyze -> Link memory -> Research rights -> Act.

Each step records status, model used and latency so the UI can show exactly what the AI did.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import prompts
from .db import DB
from .llm import LLM
from .research import Researcher

log = logging.getLogger("paperwise")

STEPS = [
    {"key": "read", "label": "Reading the letter", "tech": "Open vision model on Nebius"},
    {"key": "analyze", "label": "Understanding what it means", "tech": "NVIDIA Nemotron Super"},
    {"key": "link", "label": "Checking your archive for related letters", "tech": "Memory + Nemotron Nano"},
    {"key": "research", "label": "Researching your rights", "tech": "Tavily + Nemotron Super"},
    {"key": "act", "label": "Saving deadlines & to-dos", "tech": "Paperwise"},
]

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".bmp", ".gif", ".tif", ".tiff"}


@dataclass
class Context:
    db: DB
    llm: LLM
    researcher: Researcher
    upload_dir: Path


def fresh_steps() -> list[dict]:
    return [{**s, "state": "pending"} for s in STEPS]


class StepTracker:
    def __init__(self, ctx: Context, doc_id: str, steps: list[dict]):
        self.ctx, self.doc_id, self.steps = ctx, doc_id, steps

    def _save(self, **extra):
        self.ctx.db.update_document(self.doc_id, steps=self.steps, **extra)

    def start(self, key: str):
        s = self._get(key)
        s["state"], s["_t0"] = "running", time.time()
        self._save(status=key)

    def done(self, key: str, state: str = "done", *, model: str | None = None, note: str | None = None):
        s = self._get(key)
        s["state"] = state
        if "_t0" in s:
            s["ms"] = int((time.time() - s.pop("_t0")) * 1000)
        if model:
            s["model"] = model
        if note:
            s["note"] = note
        self._save()

    def _get(self, key: str) -> dict:
        return next(s for s in self.steps if s["key"] == key)


def read_files(ctx: Context, files: list[Path], tracker: StepTracker) -> str:
    parts, models = [], set()
    for i, path in enumerate(files, 1):
        ext = path.suffix.lower()
        data = path.read_bytes()
        if ext == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages).strip()
            if len(text) < 40:
                raise RuntimeError("This PDF looks scanned (no text layer). Please upload photos of the pages instead.")
            parts.append(text)
            models.add("pypdf (text layer)")
        elif ext in {".txt", ".md", ".eml"}:
            parts.append(data.decode("utf-8", errors="replace"))
            models.add("plain text")
        elif ext in IMAGE_EXT:
            text, meta = ctx.llm.transcribe_image(data, prompts.TRANSCRIBE)
            parts.append(text)
            models.add(meta["model"])
        else:
            raise RuntimeError(f"Unsupported file type: {ext}")
        if len(files) > 1:
            parts[-1] = f"--- Page {i} ---\n{parts[-1]}"
    tracker.done("read", model=", ".join(sorted(models)))
    return "\n\n".join(parts)


def process_document(ctx: Context, ws: str, doc_id: str, files: list[Path] | None = None,
                     text: str | None = None) -> None:
    doc = ctx.db.get_document(ws, doc_id)
    tracker = StepTracker(ctx, doc_id, doc["steps"])
    profile = ctx.db.get_profile(ws)
    today = date.today().isoformat()
    try:
        # 1. Read -----------------------------------------------------------
        tracker.start("read")
        if text is None:
            text = read_files(ctx, files or [], tracker)
        else:
            tracker.done("read", model="plain text")
        if not text.strip():
            raise RuntimeError("Couldn't find any text in this document.")
        ctx.db.update_document(doc_id, raw_text=text)

        # 2. Analyze (with a first-pass memory lookup so the model sees prior context) ------
        tracker.start("analyze")
        prior = ctx.db.search(ws, text[:1500], limit=4, exclude=doc_id)
        analysis, meta = ctx.llm.complete_json("reasoning", prompts.analyze(text, profile, today, prior),
                                               max_tokens=8000, required=("title", "summary", "urgency", "scam_risk"))
        analysis = normalize_analysis(analysis)
        sender = analysis["sender"].get("name") or ""
        ctx.db.update_document(doc_id, analysis=analysis, title=analysis["title"], sender=sender,
                               doc_type=analysis["doc_type"], urgency=analysis["urgency"])
        tracker.done("analyze", model=meta["model"])

        # 3. Link to related letters in the archive --------------------------
        tracker.start("link")
        probe = " ".join([sender, *analysis["reference_numbers"], analysis["title"]])
        candidates = {r["id"]: r for r in ctx.db.search(ws, probe, limit=5, exclude=doc_id) + prior}
        links: list[dict] = []
        if candidates:
            result, meta = ctx.llm.complete_json("fast", prompts.link(analysis, list(candidates.values()), profile),
                                                 max_tokens=1500, required=("related",))
            for item in (result or {}).get("related", []) if isinstance(result, dict) else []:
                c = candidates.get(item.get("id"))
                if c:
                    links.append({"id": c["id"], "title": c["title"], "date": c["created_at"][:10],
                                  "relation": item.get("relation", "related"), "note": item.get("note", "")})
            tracker.done("link", model=meta["model"], note=f"{len(links)} related letter(s)")
        else:
            tracker.done("link", note="First letter of its kind in your archive")
        ctx.db.update_document(doc_id, links=links)
        ctx.db.index_document(ws, doc_id, analysis["title"], sender, text)

        # 4. Research rights & scam check ------------------------------------
        tracker.start("research")
        if ctx.researcher.enabled and analysis.get("rights_query"):
            where = " ".join(x for x in (profile.get("region"), profile.get("country")) if x)
            rq = analysis["rights_query"] if where.lower() in analysis["rights_query"].lower() else \
                f"{analysis['rights_query']} {where}".strip()
            search = ctx.researcher.search(rq, trusted=True)
            scam = None
            if analysis["scam_risk"]["level"] in ("medium", "high") and analysis.get("scam_query"):
                scam = ctx.researcher.search(analysis["scam_query"], max_results=4, depth="basic")
            if search.get("results") or (scam and scam.get("results")):
                msgs = prompts.rights(analysis, search, scam, profile)
                synth, meta = ctx.llm.complete_json("reasoning", msgs, max_tokens=3000, required=("rights",))
                synth = synth if isinstance(synth, dict) else {}
                if synth.get("rights") and not _cited_only(synth["rights"]):
                    # Model forgot citations entirely; ask once more rather than showing ungrounded claims.
                    retry = msgs + [{"role": "assistant", "content": json.dumps(synth, ensure_ascii=False)},
                                    {"role": "user", "content": "Add inline source citations like [1] to every "
                                     "point and the headline; drop anything no source supports. Same JSON only."}]
                    synth2, _ = ctx.llm.complete_json("reasoning", retry, max_tokens=3000)
                    synth = synth2 if isinstance(synth2, dict) else synth
                sources = search.get("results", []) + ((scam or {}).get("results") or [])
                synth["rights"] = _cited_only(synth.get("rights") or [])
                if not CITE_RE.search(str(synth.get("headline") or "")):
                    synth["headline"] = ""
                research = {**synth,
                            "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
                            "queries": [q for q in (rq, analysis.get("scam_query") if scam else None) if q]}
                ctx.db.update_document(doc_id, research=research)
                tracker.done("research", model=f"Tavily + {meta['model']}", note=f"{len(sources)} sources")
            else:
                tracker.done("research", "skipped", note="No sources found")
        else:
            tracker.done("research", "skipped",
                         note="Web research disabled (no TAVILY_API_KEY)" if not ctx.researcher.enabled else None)

        # 5. Act: deadlines -> tasks, key facts -> memory ----------------------
        tracker.start("act")
        likely_scam = analysis["scam_risk"]["level"] == "high"
        for d in [] if likely_scam else analysis["deadlines"]:  # never schedule a scammer's "deadline"
            ctx.db.add_task(ws, d.get("what") or "Deadline", doc_id=doc_id, detail=d.get("consequence", ""),
                            due_date=d.get("date"), kind="deadline")
        for a in sorted(analysis["action_items"], key=lambda a: _int(a.get("priority"), 9))[:4]:
            ctx.db.add_task(ws, a.get("action", ""), doc_id=doc_id, detail=a.get("how", ""), kind="action")
        for f in analysis["key_facts"][:6]:
            ctx.db.add_fact(ws, f, doc_id)
        tracker.done("act", note=f"{len(analysis['deadlines'])} deadline(s), {len(analysis['key_facts'][:6])} fact(s)")
        ctx.db.update_document(doc_id, status="done")
    except Exception as e:  # noqa: BLE001 - surface any failure to the user
        log.exception("Processing failed for %s", doc_id)
        for s in tracker.steps:
            if s["state"] == "running":
                s["state"] = "error"
                s.pop("_t0", None)
        ctx.db.update_document(doc_id, status="error", error=_friendly(e), steps=tracker.steps)


CITE_RE = re.compile(r"\[\d+\]")


def _cited_only(points: list) -> list[dict]:
    """Keep only rights the model grounded in a source; uncited legal claims are where hallucinations live."""
    points = [p if isinstance(p, dict) else {"point": str(p)} for p in points]
    return [p for p in points if CITE_RE.search(str(p.get("point", "")))]


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _friendly(e: Exception) -> str:
    msg = str(e)
    if "401" in msg or "Unauthorized" in msg or "missing-key" in msg:
        return "The AI service rejected our credentials. Check NEBIUS_API_KEY."
    if "429" in msg:
        return "The AI service is busy (rate limited). Please try again in a minute."
    return msg[:400]


LEVELS = ("low", "medium", "high", "critical")


def normalize_analysis(a: dict) -> dict:
    """Coerce model output into the shape the UI and pipeline rely on."""
    if not isinstance(a, dict):
        a = {}
    sender = a.get("sender")
    if isinstance(sender, str):
        sender = {"name": sender}
    a["sender"] = {"name": "", "type": "unknown", "contact": "", **(sender or {})}
    a["title"] = str(a.get("title") or "Untitled letter")[:160]
    a["doc_type"] = str(a.get("doc_type") or "other")
    a["urgency"] = a.get("urgency") if a.get("urgency") in LEVELS else "medium"
    scam = a.get("scam_risk") if isinstance(a.get("scam_risk"), dict) else {}
    a["scam_risk"] = {"level": scam.get("level") if scam.get("level") in LEVELS[:3] else "low",
                      "signals": [str(s) for s in scam.get("signals") or []]}
    for key in ("deadlines", "amounts", "action_items"):
        a[key] = [x for x in (a.get(key) or []) if isinstance(x, dict)]
    for key in ("reference_numbers", "key_facts", "questions_to_ask", "reply_options"):
        a[key] = [str(x) for x in (a.get(key) or []) if x]
    a["reply_options"] = [r for r in a["reply_options"] if r in prompts.DRAFT_INTENTS and r != "custom"]
    if a["scam_risk"]["level"] == "high":
        a["reply_options"] = ["report_scam"] + [r for r in a["reply_options"] if r != "report_scam"]
    for d in a["deadlines"]:
        if not _is_date(d.get("date")):
            d["date"] = None
    a["deadlines"].sort(key=lambda d: d.get("date") or "9999")
    return a


def _is_date(v) -> bool:
    try:
        date.fromisoformat(str(v))
        return True
    except ValueError:
        return False
