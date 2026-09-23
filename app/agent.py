"""'Ask Paperwise' — a Nemotron tool-calling agent over the user's private document archive."""
from __future__ import annotations

import json
import logging
from datetime import date

from . import prompts
from .pipeline import Context

log = logging.getLogger("paperwise")

MAX_STEPS = 6

TOOLS = [
    {"type": "function", "function": {
        "name": "search_documents",
        "description": "Full-text search over the user's letters. Returns matching letters with id, title, sender, date, summary and a snippet.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_document",
        "description": "Get the full analysis and text of one letter by id.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "list_deadlines",
        "description": "List the user's open deadlines and to-dos, soonest first.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "web_research",
        "description": "Search authoritative web sources (government, legal aid, consumer protection) about rights, laws, or whether something is a scam.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "add_task",
        "description": "Add a reminder / to-do for the user, optionally with a due date (YYYY-MM-DD) and linked letter id.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}, "due_date": {"type": "string"}, "doc_id": {"type": "string"}},
            "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "Store a durable fact about the user or their affairs in long-term memory.",
        "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}}},
]


class Agent:
    def __init__(self, ctx: Context, ws: str):
        self.ctx, self.ws = ctx, ws
        self.trace: list[dict] = []

    # ---- tools -----------------------------------------------------------
    def run_tool(self, name: str, args: dict) -> dict | list:
        db = self.ctx.db
        if name == "search_documents":
            return db.search(self.ws, args.get("query", ""), limit=6)
        if name == "get_document":
            d = db.get_document(self.ws, args.get("id", ""))
            if not d:
                return {"error": "not found"}
            return {"id": d["id"], "date": d["created_at"][:10], "title": d["title"], "analysis": d["analysis"],
                    "links": d["links"], "research": d["research"], "text": (d["raw_text"] or "")[:6000]}
        if name == "list_deadlines":
            return [{k: t[k] for k in ("id", "title", "due_date", "kind", "doc_id", "doc_title")}
                    for t in db.list_tasks(self.ws) if not t["done"]][:25]
        if name == "web_research":
            res = self.ctx.researcher.search(args.get("query", ""), trusted=True)
            if res.get("disabled"):
                return {"error": "web research is not configured"}
            return {"answer": res.get("answer"), "results": res["results"][:5]}
        if name == "add_task":
            due = args.get("due_date") or None
            tid = db.add_task(self.ws, args.get("title", "Reminder"), doc_id=args.get("doc_id") or None,
                              due_date=due, kind="reminder")
            return {"ok": True, "task_id": tid}
        if name == "remember":
            fid = db.add_fact(self.ws, args.get("fact", ""))
            return {"ok": True, "fact_id": fid}
        return {"error": f"unknown tool {name}"}

    def _call(self, name: str, raw_args) -> str:
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        try:
            result = self.run_tool(name, args)
        except Exception as e:  # noqa: BLE001
            result = {"error": str(e)}
        out = json.dumps(result, ensure_ascii=False, default=str)[:9000]
        self.trace.append({"tool": name, "args": args, "result": _preview(result)})
        return out

    # ---- loop ------------------------------------------------------------
    def system_prompt(self) -> str:
        profile = self.ctx.db.get_profile(self.ws)
        facts = "\n".join(f"- {f['fact']}" for f in self.ctx.db.list_facts(self.ws)[:40]) or "(none yet)"
        return prompts.AGENT_SYSTEM.format(profile=prompts.profile_block(profile), today=date.today().isoformat(),
                                           facts=facts)

    def ask(self, message: str, history: list[dict]) -> dict:
        messages = [{"role": "system", "content": self.system_prompt()}]
        messages += [{"role": h["role"], "content": str(h["content"])[:4000]}
                     for h in history[-10:] if h.get("role") in ("user", "assistant")]
        messages.append({"role": "user", "content": message})
        llm = self.ctx.llm
        if llm.tools_supported is not False:
            try:
                return self._native(messages)
            except Exception as e:  # noqa: BLE001 - some deployments reject `tools`
                rejected = getattr(e, "status_code", None) in (400, 404, 422) or "tool" in str(e).lower()
                if not rejected or llm.tools_supported:
                    raise
                log.warning("Native tool calling unavailable (%s); using JSON protocol", e)
                llm.tools_supported = False
                self.trace = []
        return self._json_protocol(messages)

    def _native(self, messages: list[dict]) -> dict:
        from .llm import clean_text

        model = None
        for _ in range(MAX_STEPS):
            msg, model = self.ctx.llm.raw("reasoning", messages, tools=TOOLS, tool_choice="auto",
                                          temperature=0.2, max_tokens=3000)
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                self.ctx.llm.tools_supported = True
                return {"answer": clean_text(msg.content), "trace": self.trace, "model": model, "mode": "native-tools"}
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls]})
            for c in calls:
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": self._call(c.function.name, c.function.arguments)})
        return self._finalize(messages, model, "native-tools")

    def _json_protocol(self, messages: list[dict]) -> dict:
        from .llm import extract_json

        spec = "\n".join(f"- {t['function']['name']}: {t['function']['description']} "
                         f"args={json.dumps(t['function']['parameters']['properties'])}" for t in TOOLS)
        messages[0]["content"] += prompts.AGENT_JSON_PROTOCOL + spec
        model = self.ctx.llm.model("reasoning")
        for _ in range(MAX_STEPS):
            text, meta = self.ctx.llm.complete("reasoning", messages, max_tokens=3000)
            model = meta["model"]
            try:
                obj = extract_json(text)
            except ValueError:
                return {"answer": text, "trace": self.trace, "model": model, "mode": "json-protocol"}
            if isinstance(obj, dict) and "final" in obj:
                return {"answer": obj["final"], "trace": self.trace, "model": model, "mode": "json-protocol"}
            if isinstance(obj, dict) and obj.get("tool"):
                result = self._call(obj["tool"], obj.get("args") or {})
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"TOOL RESULT ({obj['tool']}):\n{result}"}]
                continue
            return {"answer": text, "trace": self.trace, "model": model, "mode": "json-protocol"}
        return self._finalize(messages, model, "json-protocol")

    def _finalize(self, messages: list[dict], model, mode: str) -> dict:
        messages.append({"role": "user", "content": "Please give your final answer now, without calling tools."})
        text, meta = self.ctx.llm.complete("reasoning", messages, max_tokens=2000)
        return {"answer": text, "trace": self.trace, "model": meta["model"], "mode": mode}


def _preview(result) -> str:
    if isinstance(result, list):
        titles = [r.get("title") for r in result if isinstance(r, dict) and r.get("title")]
        return f"{len(result)} result(s)" + (f": {', '.join(titles[:4])}" if titles else "")
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        if "results" in result:
            return f"{len(result['results'])} web source(s)"
        if "title" in result:
            return f"opened “{result['title']}”"
        return "ok"
    return str(result)[:120]
