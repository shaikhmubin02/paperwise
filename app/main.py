"""FastAPI app: REST API + static single-page UI."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import prompts, samples
from .agent import Agent
from .config import BASE_DIR, settings
from .db import DB
from .llm import LLM
from .pipeline import Context, IMAGE_EXT, fresh_steps, process_document
from .research import Researcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("paperwise")

COOKIE = "pw_ws"
ALLOWED_EXT = IMAGE_EXT | {".pdf", ".txt", ".md", ".eml"}


def build_context() -> Context:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return Context(db=DB(settings.db_path), llm=LLM(settings), researcher=Researcher(settings.tavily_api_key),
                   upload_dir=settings.upload_dir)


app = FastAPI(title="Paperwise", version="1.0.0")
app.state.ctx = None


def ctx() -> Context:
    if app.state.ctx is None:
        app.state.ctx = build_context()
    return app.state.ctx


@app.middleware("http")
async def workspace_cookie(request: Request, call_next):
    ws = request.cookies.get(COOKIE)
    fresh = not ws or not re.fullmatch(r"[0-9a-f]{32}", ws)
    if fresh:
        ws = secrets.token_hex(16)
    request.state.ws = ws
    response = await call_next(request)
    if fresh:
        response.set_cookie(COOKIE, ws, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


def ws(request: Request) -> str:
    return request.state.ws


def doc_folder(w: str, doc_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", doc_id):
        raise HTTPException(404, "Not found")
    return ctx().upload_dir / w / doc_id


# ---- meta -------------------------------------------------------------------
@app.get("/api/health")
def health():
    c = ctx()
    return {"ok": True, "models": c.llm.models, "nebius_key": bool(settings.nebius_api_key),
            "tavily": c.researcher.enabled, "base_url": settings.nebius_base_url}


class Profile(BaseModel):
    name: str = ""
    address: str = ""
    country: str = ""
    region: str = ""
    language: str = "English"
    reading_level: str = "simple"


@app.get("/api/profile")
def get_profile(request: Request):
    return ctx().db.get_profile(ws(request))


@app.put("/api/profile")
def put_profile(request: Request, body: Profile):
    return ctx().db.save_profile(ws(request), body.model_dump())


# ---- documents -------------------------------------------------------------
def _check_quota(w: str):
    if ctx().db.count_documents(w) >= settings.max_docs_per_workspace:
        raise HTTPException(429, "Document limit reached for this workspace. Delete some letters first.")


@app.post("/api/documents")
async def upload(request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...)):
    w = ws(request)
    _check_quota(w)
    if not files or len(files) > 8:
        raise HTTPException(400, "Upload between 1 and 8 files (pages) per letter.")
    payloads = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"Unsupported file type '{ext}'. Use photos (JPG/PNG), PDF or text.")
        data = await f.read()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} is larger than {settings.max_upload_mb} MB.")
        payloads.append((Path(f.filename).name, ext, data))
    c = ctx()
    doc_id = c.db.create_document(w, [p[0] for p in payloads], fresh_steps())
    folder = c.upload_dir / w / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (_, ext, data) in enumerate(payloads):
        p = folder / f"page{i + 1}{ext}"
        p.write_bytes(data)
        paths.append(p)
    background.add_task(process_document, c, w, doc_id, files=paths)
    return {"id": doc_id}


class TextIn(BaseModel):
    text: str


@app.post("/api/documents/text")
def upload_text(request: Request, body: TextIn, background: BackgroundTasks):
    w = ws(request)
    _check_quota(w)
    if len(body.text.strip()) < 20:
        raise HTTPException(400, "Please paste the full text of the letter.")
    c = ctx()
    doc_id = c.db.create_document(w, ["pasted text"], fresh_steps())
    background.add_task(process_document, c, w, doc_id, text=body.text[:60000])
    return {"id": doc_id}


@app.get("/api/samples")
def list_samples():
    return samples.list_samples()


@app.get("/api/samples/{sample_id}.png")
def sample_image(sample_id: str):
    if sample_id not in {s["id"] for s in samples.list_samples()}:
        raise HTTPException(404)
    return Response(samples.render_sample(sample_id), media_type="image/png")


@app.post("/api/samples/{sample_id}")
def use_sample(request: Request, sample_id: str, background: BackgroundTasks):
    if sample_id not in {s["id"] for s in samples.list_samples()}:
        raise HTTPException(404, "Unknown sample")
    w = ws(request)
    _check_quota(w)
    c = ctx()
    doc_id = c.db.create_document(w, [f"sample-{sample_id}.png"], fresh_steps())
    folder = c.upload_dir / w / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "page1.png"
    path.write_bytes(samples.render_sample(sample_id))
    if c.llm.models.get("vision"):
        background.add_task(process_document, c, w, doc_id, files=[path])
    else:  # No vision model on this account: still demo the rest of the pipeline.
        background.add_task(process_document, c, w, doc_id, text=samples.sample_text(sample_id))
    return {"id": doc_id}


@app.get("/api/documents")
def list_documents(request: Request):
    return ctx().db.list_documents(ws(request))


@app.get("/api/documents/{doc_id}")
def get_document(request: Request, doc_id: str):
    w = ws(request)
    c = ctx()
    d = c.db.get_document(w, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    d["tasks"] = c.db.list_tasks(w, doc_id)
    d["drafts"] = c.db.list_drafts(w, doc_id)
    return d


@app.get("/api/documents/{doc_id}/pages/{n}")
def get_page(request: Request, doc_id: str, n: int):
    folder = doc_folder(ws(request), doc_id)
    matches = sorted(folder.glob(f"page{n}.*")) if folder.exists() else []
    if not matches:
        raise HTTPException(404)
    return FileResponse(matches[0])


@app.delete("/api/documents/{doc_id}")
def delete_document(request: Request, doc_id: str):
    w = ws(request)
    c = ctx()
    folder = doc_folder(w, doc_id)
    c.db.delete_document(w, doc_id)
    if folder.exists():
        for p in folder.iterdir():
            p.unlink()
        folder.rmdir()
    return {"ok": True}


class DraftIn(BaseModel):
    intent: str = "dispute"
    notes: str = ""


@app.post("/api/documents/{doc_id}/draft")
def make_draft(request: Request, doc_id: str, body: DraftIn):
    w = ws(request)
    c = ctx()
    d = c.db.get_document(w, doc_id)
    if not d or not d.get("analysis"):
        raise HTTPException(404, "Letter not found or still processing")
    intent = body.intent if body.intent in prompts.DRAFT_INTENTS else "custom"
    msgs = prompts.draft(d["analysis"], d["raw_text"] or "", intent, body.notes[:2000], c.db.get_profile(w),
                         date.today().isoformat())
    content, meta = c.llm.complete_json("reasoning", msgs, max_tokens=4000, temperature=0.3, required=("letter",))
    if not isinstance(content, dict) or not content.get("letter"):
        raise HTTPException(502, "The model didn't return a usable draft. Please try again.")
    content["model"] = meta["model"]
    c.db.add_draft(w, doc_id, intent, content)
    return content


# ---- tasks & calendar ----------------------------------------------------------
@app.get("/api/tasks")
def list_tasks(request: Request):
    return ctx().db.list_tasks(ws(request))


class TaskPatch(BaseModel):
    done: bool


@app.patch("/api/tasks/{task_id}")
def patch_task(request: Request, task_id: str, body: TaskPatch):
    ctx().db.set_task_done(ws(request), task_id, body.done)
    return {"ok": True}


def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.get("/api/calendar.ics")
def calendar(request: Request):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Paperwise//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Paperwise deadlines"]
    for t in ctx().db.list_tasks(ws(request)):
        if t["done"] or not t["due_date"]:
            continue
        try:
            day = date.fromisoformat(t["due_date"])
        except ValueError:
            continue
        lines += [
            "BEGIN:VEVENT", f"UID:{t['id']}@paperwise", f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape(t['title'])}",
            f"DESCRIPTION:{_ics_escape((t.get('doc_title') or '') + ' — ' + (t.get('detail') or ''))}",
            "BEGIN:VALARM", "TRIGGER:-P3D", "ACTION:DISPLAY", "DESCRIPTION:Paperwise deadline in 3 days", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=paperwise-deadlines.ics"})


# ---- memory & chat --------------------------------------------------------------
@app.get("/api/memory")
def memory(request: Request):
    return ctx().db.list_facts(ws(request))


@app.delete("/api/memory/{fact_id}")
def forget(request: Request, fact_id: str):
    ctx().db.delete_fact(ws(request), fact_id)
    return {"ok": True}


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/api/chat")
def chat(request: Request, body: ChatIn):
    if not body.message.strip():
        raise HTTPException(400, "Empty message")
    try:
        return Agent(ctx(), ws(request)).ask(body.message[:4000], body.history)
    except Exception as e:  # noqa: BLE001
        log.exception("chat failed")
        raise HTTPException(502, f"Assistant error: {str(e)[:300]}") from e


@app.delete("/api/workspace")
def wipe(request: Request):
    w = ws(request)
    c = ctx()
    c.db.wipe_workspace(w)
    folder = c.upload_dir / w
    if folder.exists():
        import shutil

        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


# ---- static UI ------------------------------------------------------------------
STATIC = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
