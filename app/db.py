"""SQLite persistence: documents, full-text memory, tasks, facts, drafts, profiles.

Every row is scoped to a workspace (one per browser cookie) so a public demo keeps judges isolated.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, created_at TEXT NOT NULL,
    filenames TEXT, status TEXT, steps TEXT, error TEXT,
    raw_text TEXT, analysis TEXT, research TEXT, links TEXT,
    title TEXT, sender TEXT, doc_type TEXT, urgency TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_ws ON documents(workspace, created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    doc_id UNINDEXED, workspace UNINDEXED, title, sender, body
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, doc_id TEXT, title TEXT NOT NULL,
    detail TEXT, due_date TEXT, kind TEXT, done INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, fact TEXT NOT NULL, doc_id TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, doc_id TEXT, intent TEXT, content TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS profiles (workspace TEXT PRIMARY KEY, data TEXT);
"""

JSON_COLS = ("steps", "analysis", "research", "links", "filenames")

DEFAULT_PROFILE = {
    "name": "",
    "address": "",
    "country": "United States",
    "region": "",
    "language": "English",
    "reading_level": "simple",  # simple | standard | detailed
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query of quoted tokens."""
    tokens = [t for t in re.findall(r"[\w#-]{3,}", text.lower()) if t not in _STOP]
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens[:24]))


_STOP = {"the", "and", "for", "you", "your", "with", "this", "that", "from", "are", "was", "have", "not",
         "what", "when", "which", "does", "about", "any", "all", "can", "how", "my", "our", "has"}


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for c in JSON_COLS:
            if c in d and isinstance(d[c], str):
                try:
                    d[c] = json.loads(d[c])
                except json.JSONDecodeError:
                    pass
        return d

    # ---- documents -------------------------------------------------------
    def create_document(self, ws: str, filenames: list[str], steps: list[dict]) -> str:
        doc_id = new_id()
        self._exec(
            "INSERT INTO documents (id, workspace, created_at, filenames, status, steps) VALUES (?,?,?,?,?,?)",
            (doc_id, ws, now(), json.dumps(filenames), "queued", json.dumps(steps)),
        )
        return doc_id

    def update_document(self, doc_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in JSON_COLS and not isinstance(v, str) else v)
        self._exec(f"UPDATE documents SET {', '.join(cols)} WHERE id=?", (*vals, doc_id))

    def get_document(self, ws: str, doc_id: str) -> dict | None:
        rows = self._all("SELECT * FROM documents WHERE workspace=? AND id=?", (ws, doc_id))
        return rows[0] if rows else None

    def list_documents(self, ws: str) -> list[dict]:
        return self._all(
            "SELECT id, created_at, filenames, status, title, sender, doc_type, urgency, error, "
            "json_extract(analysis, '$.summary') AS summary, json_extract(analysis, '$.scam_risk.level') AS scam "
            "FROM documents WHERE workspace=? ORDER BY created_at DESC",
            (ws,),
        )

    def count_documents(self, ws: str) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM documents WHERE workspace=?", (ws,)).fetchone()[0]

    def delete_document(self, ws: str, doc_id: str) -> None:
        with self.lock:
            for sql in (
                "DELETE FROM documents WHERE workspace=? AND id=?",
                "DELETE FROM doc_fts WHERE workspace=? AND doc_id=?",
                "DELETE FROM tasks WHERE workspace=? AND doc_id=?",
                "DELETE FROM facts WHERE workspace=? AND doc_id=?",
                "DELETE FROM drafts WHERE workspace=? AND doc_id=?",
            ):
                self.conn.execute(sql, (ws, doc_id))
            self.conn.commit()

    def index_document(self, ws: str, doc_id: str, title: str, sender: str, body: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM doc_fts WHERE doc_id=?", (doc_id,))
            self.conn.execute(
                "INSERT INTO doc_fts (doc_id, workspace, title, sender, body) VALUES (?,?,?,?,?)",
                (doc_id, ws, title, sender, body),
            )
            self.conn.commit()

    def search(self, ws: str, text: str, limit: int = 5, exclude: str | None = None) -> list[dict]:
        q = fts_query(text)
        if not q:
            return []
        rows = self._all(
            "SELECT d.id, d.title, d.sender, d.doc_type, d.created_at, "
            "json_extract(d.analysis, '$.summary') AS summary, "
            "snippet(doc_fts, 4, '[', ']', '…', 18) AS snippet, bm25(doc_fts) AS score "
            "FROM doc_fts JOIN documents d ON d.id = doc_fts.doc_id "
            "WHERE doc_fts MATCH ? AND doc_fts.workspace=? ORDER BY score LIMIT ?",
            (q, ws, limit + 1),
        )
        return [r for r in rows if r["id"] != exclude][:limit]

    # ---- tasks -----------------------------------------------------------
    def add_task(self, ws: str, title: str, *, doc_id: str | None = None, detail: str = "",
                 due_date: str | None = None, kind: str = "action") -> str:
        tid = new_id()
        self._exec(
            "INSERT INTO tasks (id, workspace, doc_id, title, detail, due_date, kind, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (tid, ws, doc_id, title, detail, due_date, kind, now()),
        )
        return tid

    def list_tasks(self, ws: str, doc_id: str | None = None) -> list[dict]:
        sql = ("SELECT t.*, d.title AS doc_title FROM tasks t LEFT JOIN documents d ON d.id=t.doc_id "
               "WHERE t.workspace=?")
        args: tuple = (ws,)
        if doc_id:
            sql += " AND t.doc_id=?"
            args += (doc_id,)
        sql += " ORDER BY t.done, t.due_date IS NULL, t.due_date, t.created_at"
        return self._all(sql, args)

    def set_task_done(self, ws: str, task_id: str, done: bool) -> None:
        self._exec("UPDATE tasks SET done=? WHERE workspace=? AND id=?", (int(done), ws, task_id))

    # ---- facts (long-term memory) ----------------------------------------
    def add_fact(self, ws: str, fact: str, doc_id: str | None = None) -> str:
        fid = new_id()
        self._exec("INSERT INTO facts (id, workspace, fact, doc_id, created_at) VALUES (?,?,?,?,?)",
                   (fid, ws, fact.strip(), doc_id, now()))
        return fid

    def list_facts(self, ws: str) -> list[dict]:
        return self._all("SELECT * FROM facts WHERE workspace=? ORDER BY created_at DESC", (ws,))

    def delete_fact(self, ws: str, fact_id: str) -> None:
        self._exec("DELETE FROM facts WHERE workspace=? AND id=?", (ws, fact_id))

    # ---- drafts ----------------------------------------------------------
    def add_draft(self, ws: str, doc_id: str, intent: str, content: dict) -> str:
        did = new_id()
        self._exec("INSERT INTO drafts (id, workspace, doc_id, intent, content, created_at) VALUES (?,?,?,?,?,?)",
                   (did, ws, doc_id, intent, json.dumps(content), now()))
        return did

    def list_drafts(self, ws: str, doc_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM drafts WHERE workspace=? AND doc_id=? ORDER BY created_at DESC", (ws, doc_id))
        for r in rows:
            r["content"] = json.loads(r["content"])
        return rows

    # ---- profile ---------------------------------------------------------
    def get_profile(self, ws: str) -> dict:
        with self.lock:
            row = self.conn.execute("SELECT data FROM profiles WHERE workspace=?", (ws,)).fetchone()
        return {**DEFAULT_PROFILE, **(json.loads(row[0]) if row else {})}

    def save_profile(self, ws: str, data: dict) -> dict:
        profile = {**DEFAULT_PROFILE, **{k: v for k, v in data.items() if k in DEFAULT_PROFILE}}
        self._exec("INSERT INTO profiles (workspace, data) VALUES (?,?) "
                   "ON CONFLICT(workspace) DO UPDATE SET data=excluded.data", (ws, json.dumps(profile)))
        return profile

    # ---- wipe ------------------------------------------------------------
    def wipe_workspace(self, ws: str) -> None:
        with self.lock:
            for table in ("documents", "doc_fts", "tasks", "facts", "drafts", "profiles"):
                self.conn.execute(f"DELETE FROM {table} WHERE workspace=?", (ws,))
            self.conn.commit()
