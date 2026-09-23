"""End-to-end tests with a fake OpenAI-compatible client — no API keys or network needed."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, resolve_models
from app.db import DB
from app.llm import LLM, clean_text, extract_json
from app.pipeline import Context, normalize_analysis
from app.research import Researcher

DUE = (date.today() + timedelta(days=20)).isoformat()


def analysis_json(ref="NRP-4471-2290", scam="low"):
    return {
        "title": "Debt collection notice from Northgate Recovery",
        "sender": {"name": "Northgate Recovery Partners", "type": "debt_collector", "contact": "(800) 555-0142"},
        "doc_type": "debt_collection", "original_language": "English", "letter_date": date.today().isoformat(),
        "summary": "A collector says you owe $1,284.37.", "what_it_means": "You can dispute it.",
        "urgency": "high", "urgency_reason": "Dispute window closes soon.",
        "deadlines": [{"date": DUE, "what": "Dispute the debt", "consequence": "Assumed valid", "estimated": "false"},
                      {"date": "not a date", "what": "bad", "consequence": ""}],
        "amounts": [{"value": 1284.37, "currency": "USD", "what": "Total claimed"}],
        "action_items": [{"action": "Send a validation request", "how": "Certified mail", "priority": "1"}],
        "if_ignored": "They may report it.", "scam_risk": {"level": scam, "signals": ["Real disclosures present"]},
        "reference_numbers": [ref], "key_facts": [f"Account {ref} with Northgate Recovery"],
        "questions_to_ask": ["Who is the original creditor?"], "rights_query": "FDCPA debt validation rights",
        "scam_query": "Northgate Recovery scam", "reply_options": ["validation_request", "bogus"],
    }


class FakeCompletions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, model, messages, **kw):
        self.owner.calls.append({"model": model, "messages": messages, **kw})
        content = messages[-1]["content"]
        text = content if isinstance(content, str) else json.dumps(content)
        tool_calls = None
        if isinstance(content, list):  # vision
            out = "<think>looking</think>NORTHGATE RECOVERY PARTNERS\nAccount Number: NRP-4471-2290\nBalance $1,284.37"
        elif "tools" in kw:
            if not any(m.get("role") == "tool" for m in messages):
                tool_calls = [NS(id="c1", function=NS(name="search_documents", arguments='{"query": "Northgate"}'))]
                out = ""
            else:
                out = "You have **one** letter from Northgate."
        elif "Analyze this letter" in text:
            out = "```json\n" + json.dumps(analysis_json()) + "\n```"
        elif "EARLIER LETTERS" in text:
            ids = [line.split("id=")[1].split()[0] for line in text.splitlines() if "id=" in line]
            out = json.dumps({"related": [{"id": ids[0], "relation": "follow_up", "note": "Second notice."}]} if ids else {"related": []})
        elif "SOURCES:" in text:
            out = 'Sure! {"rights": [{"point": "You can request validation [1]"}], "scam_verdict": "", "legal_help": "Legal aid [1]"}'
        elif "Write the letter" in text:
            out = json.dumps({"subject": "Debt validation request", "letter": "Dear Sir or Madam, ...",
                              "translation": "", "send_tips": ["Use certified mail"]})
        else:
            out = "ok"
        msg = NS(content=out, tool_calls=tool_calls)
        return NS(choices=[NS(message=msg)], usage=NS(total_tokens=42))


class FakeClient:
    def __init__(self):
        self.calls = []
        self.chat = NS(completions=FakeCompletions(self))
        self.models = NS(list=lambda: NS(data=[NS(id=m) for m in (
            "nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3-nano-30b-a3b",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "Qwen/Qwen3-Embedding-8B")]))


class FakeHTTP:
    def post(self, url, json=None, headers=None):
        return NS(raise_for_status=lambda: None, json=lambda: {
            "answer": "You have 30 days.",
            "results": [{"title": "CFPB: debt validation", "url": "https://www.consumerfinance.gov/x", "content": "..."}]})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    fake = FakeClient()
    s = Settings()
    s.data_dir = tmp_path
    c = Context(db=DB(tmp_path / "t.db"), llm=LLM(s, client=fake), researcher=Researcher("tvly-test", FakeHTTP()),
                upload_dir=tmp_path / "uploads")
    main.app.state.ctx = c
    with TestClient(main.app) as tc:
        tc.fake = fake
        yield tc
    main.app.state.ctx = None


def wait_done(tc, doc_id, timeout=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = tc.get(f"/api/documents/{doc_id}").json()
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.05)
    raise AssertionError("timed out")


# ---------- unit tests ----------
def test_extract_json_handles_think_fences_and_chatter():
    assert extract_json("<think>hmm {no}</think>```json\n{\"a\": 1}\n```") == {"a": 1}
    assert extract_json('Here you go: {"b": [1,2]} thanks') == {"b": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")
    assert clean_text("reasoning...</think>answer") == "answer"


def test_resolve_models_prefers_nemotron_roles():
    m = resolve_models(Settings(model_reasoning="", model_fast="", model_vision=""),
                       ["nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3-nano-30b-a3b",
                        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"])
    assert m == {"reasoning": "nvidia/nemotron-3-super-120b-a12b", "fast": "nvidia/nemotron-3-nano-30b-a3b",
                 "vision": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"}
    fallback = resolve_models(Settings(model_reasoning="", model_fast="", model_vision=""), [])
    assert fallback["reasoning"].startswith("nvidia/") and fallback["vision"] == ""


def test_normalize_analysis_is_defensive():
    a = normalize_analysis({"sender": "ACME", "urgency": "weird", "scam_risk": None, "deadlines": ["x"],
                            "reply_options": ["dispute", "nope"]})
    assert a["sender"]["name"] == "ACME" and a["urgency"] == "medium" and a["scam_risk"]["level"] == "low"
    assert a["deadlines"] == [] and a["reply_options"] == ["dispute"]


# ---------- integration tests ----------
def test_sample_pipeline_end_to_end(client):
    r = client.post("/api/samples/debt_collection")
    assert r.status_code == 200
    d = wait_done(client, r.json()["id"])
    assert d["status"] == "done", d.get("error")
    assert [s["state"] for s in d["steps"]] == ["done", "done", "done", "done", "done"]
    assert d["steps"][0]["model"] == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert d["analysis"]["urgency"] == "high"
    assert [x["date"] for x in d["analysis"]["deadlines"]] == [DUE, None]
    assert d["research"]["rights"][0]["point"].startswith("You can request")
    assert d["research"]["sources"][0]["url"].startswith("https://www.consumerfinance.gov")
    titles = [t["title"] for t in d["tasks"]]
    assert "Dispute the debt" in titles and "Send a validation request" in titles
    assert client.get("/api/memory").json()[0]["fact"].startswith("Account NRP-4471-2290")
    # the image was sent to the vision model
    assert any(isinstance(c["messages"][-1]["content"], list) for c in client.fake.calls)


def test_second_letter_is_linked_to_first(client):
    first = wait_done(client, client.post("/api/samples/debt_collection").json()["id"])
    second = wait_done(client, client.post("/api/samples/debt_followup").json()["id"])
    assert second["links"] and second["links"][0]["id"] == first["id"]
    assert second["links"][0]["relation"] == "follow_up"


def test_workspaces_are_isolated(client):
    doc_id = client.post("/api/documents/text", json={"text": "Dear tenant, your rent will increase by $350."}).json()["id"]
    wait_done(client, doc_id)
    other = TestClient(main.app)
    assert other.get("/api/documents").json() == []
    assert other.get(f"/api/documents/{doc_id}").status_code == 404


def test_draft_calendar_chat_and_wipe(client):
    doc_id = wait_done(client, client.post("/api/samples/debt_collection").json()["id"])["id"]
    draft = client.post(f"/api/documents/{doc_id}/draft", json={"intent": "validation_request"}).json()
    assert draft["letter"].startswith("Dear") and draft["model"]
    ics = client.get("/api/calendar.ics")
    assert ics.status_code == 200 and "BEGIN:VEVENT" in ics.text and DUE.replace("-", "") in ics.text
    chat = client.post("/api/chat", json={"message": "What do I owe Northgate?", "history": []}).json()
    assert chat["mode"] == "native-tools" and chat["trace"][0]["tool"] == "search_documents"
    assert "Northgate" in chat["answer"]
    assert client.delete("/api/workspace").json()["ok"]
    assert client.get("/api/documents").json() == []


def test_rejects_bad_uploads_and_ids(client):
    r = client.post("/api/documents", files={"files": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert client.get("/api/documents/..%2F..%2Fx/pages/1").status_code == 404
    assert client.delete("/api/documents/notahexid").status_code == 404


def test_profile_roundtrip(client):
    p = client.put("/api/profile", json={"language": "Español", "region": "California", "country": "United States"}).json()
    assert p["language"] == "Español"
    assert client.get("/api/profile").json()["region"] == "California"


def test_likely_scam_never_schedules_scammer_deadlines():
    a = normalize_analysis({"scam_risk": {"level": "high", "signals": ["gift cards"]},
                            "deadlines": [{"date": DUE, "what": "Pay with gift cards"}], "reply_options": ["payment_plan"]})
    assert a["deadlines"][0]["what"] == "Pay with gift cards" and a["reply_options"] == ["report_scam", "payment_plan"]


def test_uncited_rights_are_dropped():
    from app.pipeline import _cited_only
    assert _cited_only([{"point": "Cited [2]"}, {"point": "Made-up program"}, "Also cited [1]"]) == [
        {"point": "Cited [2]"}, {"point": "Also cited [1]"}]


def test_malformed_outer_json_is_repaired_not_replaced_by_fragment():
    from app.llm import _json_problem
    broken = '{"title": "X", "sender": {"name": "ACME", "type": "company"}, "summary": "oops "quoted" bad"}'
    assert extract_json(broken) == {"name": "ACME", "type": "company"}  # only a fragment survives…
    assert _json_problem(broken, ("title", "summary")) == "missing keys: title, summary"  # …so we repair
    assert _json_problem('{"title": 1, "summary": 2}', ("title", "summary")) is None
