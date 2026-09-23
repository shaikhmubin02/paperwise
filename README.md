# 📬 Paperwise — the AI that reads your scary mail

**Snap a photo of any intimidating letter.** A debt collector, a landlord, a hospital bill, a tax notice, a
"FINAL NOTICE" that might be a scam. Paperwise tells you — *in your language, at your reading level*:

- **What it really means** — plain-language summary, amounts, and why it's urgent (or not)
- **Is it a scam?** — red flags from the letter itself, cross-checked on the live web
- **Your rights** — researched live from government and legal-aid sources, **with citations**
- **Deadlines** — extracted (even "within 30 days of this letter"), tracked, exported to your calendar
- **What to do next** — a numbered action plan and questions to ask
- **Your reply, written** — dispute, debt validation, payment plan, hardship, appeal… in the sender's language, with a translation for you
- **It remembers** — the second notice is automatically linked to the first ("the balance went up $56.75")
- **Ask anything** — a tool-using agent over your whole paperwork archive

Built for the **Nebius x NVIDIA Global AI Hackathon** · Track: **Personal AI**

## Try it
- **Live demo:** _coming soon_. It's hosted on Render's free tier, so the first visit after idle takes about a minute to wake. Click any sample letter; no sign-up needed.
- **Demo video:** _coming soon_
- **Deploy your own:** [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/shaikhmubin02/paperwise) (you'll be asked for `NEBIUS_API_KEY` and `TAVILY_API_KEY`)

## Why
Millions of people miss deadlines on letters they can't understand — and lose money, housing and benefits
because of it. Default judgments in debt cases, evictions, and lapsed benefits often trace back to a single
unanswered letter. Meanwhile impostor scams arrive in the very same mailbox. The people hit hardest — older
adults, immigrants, people with low literacy or limited English — are the least able to pay a lawyer to read
their mail. Paperwise is the knowledgeable friend who reads it with them.

## How it uses NVIDIA + Nebius

All inference runs on **Nebius Token Factory** (OpenAI-compatible API) using **NVIDIA Nemotron** open models,
each chosen for the job it's best at:

| Step | Model | What it does |
|---|---|---|
| 1. Read | Open vision model on Token Factory (**MiniCPM-V 4.5**) | Transcribes phone photos of letters: tables, fine print, amounts, reference numbers. (Token Factory has no Nemotron vision model today; if one appears, discovery picks it first.) |
| 2. Understand | **Nemotron 3 Super 120B** | Structured legal/financial reasoning → strict JSON: type, urgency, deadlines (computed), amounts, scam signals, action plan, reply options |
| 3. Remember | **Nemotron 3 Nano 30B** + SQLite FTS5 | Finds related earlier letters, explains the connection and what changed |
| 4. Research | **Tavily** + **Nemotron 3 Super** | Searches authoritative sources (CFPB, FTC, IRS, HUD, legal aid…) and synthesizes your rights with inline citations; scam cross-check |
| 5. Act | — | Deadlines → to-dos + `.ics` calendar with 3-day alarms; key facts → long-term memory |
| Ask | **Nemotron 3 Super** (tool calling) | Agent with `search_documents`, `get_document`, `list_deadlines`, `web_research`, `add_task`, `remember` |
| Reply | **Nemotron 3 Super** | Drafts protective letters in the recipient's language + translation for the user |

Model IDs are **auto-discovered** from `/v1/models` (preferring the IDs above) and can be pinned via env vars.
The agent uses native tool calling and automatically falls back to a JSON tool protocol if a deployment
doesn't support `tools`. Every step's model and latency are shown in the UI ("How Paperwise read this").

## Architecture
```
Browser SPA (vanilla JS, no build) ──► FastAPI
   ├─ POST /api/documents  → background pipeline: Read → Analyze → Link → Research → Act
   ├─ GET  /api/documents/:id (polled; per-step status, model, latency)
   ├─ POST /api/documents/:id/draft → reply letters
   ├─ POST /api/chat → Nemotron tool-calling agent
   ├─ GET  /api/calendar.ics, /api/tasks, /api/memory
   └─ SQLite (documents, FTS5 index, tasks, facts, drafts, profile) — isolated per-browser workspace
```

## Run it locally

Requires Python 3.11+.

```bash
git clone <this repo> && cd paperwise
python -m venv .venv
# Windows: .venv\Scripts\activate   ·   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add NEBIUS_API_KEY (required) and TAVILY_API_KEY (recommended)
uvicorn app.main:app --reload
```
Open http://localhost:8000 and click a sample letter, or upload your own.
**Settings → Under the hood** shows which Nemotron models were resolved.

### Docker
```bash
docker build -t paperwise .
docker run -p 8000:8000 --env-file .env -v paperwise-data:/data paperwise
```

### Tests
```bash
pip install -r requirements-dev.txt
pytest -q
```
The suite runs the full pipeline, memory linking, agent tool calls, drafting, calendar export and workspace
isolation against a fake OpenAI-compatible client — no keys or network needed.

## Privacy
- Each browser gets an isolated workspace; nothing is shared between users.
- Documents are stored only in this server's SQLite database and upload folder. **Memory → Delete all my data** wipes everything.
- Open-weight models: you can self-host the same Nemotron models on Nebius AI Cloud for full data control.

## Disclaimer
Paperwise explains documents and helps you act; it is not a law firm and does not give legal advice.
For high-stakes matters, contact free legal aid (Paperwise suggests where).

## License
MIT
