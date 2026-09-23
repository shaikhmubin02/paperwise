// Paperwise single-page UI. No build step, no framework: small, fast, and easy for judges to read.

const $ = (sel, root = document) => root.querySelector(sel);
const app = $("#app");

// ---------- utilities ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4200);
}

function daysUntil(iso) {
  if (!iso) return null;
  const d = new Date(iso + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
}
function relDays(n) {
  if (n === null) return "";
  if (n < 0) return `${-n} day${n === -1 ? "" : "s"} ago`;
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  return `in ${n} days`;
}
const fmtDate = (iso) => iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
const TYPE_ICON = { bill: "🧾", debt_collection: "📞", government: "🏛️", court_legal: "⚖️", tax: "🧮", housing: "🏠", medical: "🩺", insurance: "🛡️", immigration: "🛂", benefits: "🤝", employment: "💼", bank: "🏦", school: "🎓", marketing: "📣", scam_suspect: "🚩", other: "✉️" };
const URGENCY_LABEL = { low: "Low urgency", medium: "Needs attention", high: "Urgent", critical: "Act now" };

// Tiny, safe markdown: escape first, then add emphasis, links, lists, paragraphs.
function md(text) {
  const lines = esc(text).split(/\n/);
  let html = "", list = null;
  const inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  let table = null;
  const flushTable = () => {
    if (!table) return;
    const [head, ...body] = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r));
    const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
    html += `<div class="md-table"><table><thead><tr>${cells(head).map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${cells(r).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    table = null;
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("|")) { if (list) { html += `</${list}>`; list = null; } (table ||= []).push(line); continue; }
    flushTable();
    const m = line.match(/^([-*•]|\d+[.)])\s+(.*)/);
    if (m) {
      const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (list !== tag) { if (list) html += `</${list}>`; html += `<${tag}>`; list = tag; }
      html += `<li>${inline(m[2])}</li>`;
      continue;
    }
    if (list) { html += `</${list}>`; list = null; }
    if (/^#{1,4}\s/.test(line)) html += `<p><strong>${inline(line.replace(/^#+\s/, ""))}</strong></p>`;
    else if (line) html += `<p>${inline(line)}</p>`;
  }
  flushTable();
  if (list) html += `</${list}>`;
  return html;
}

// Turn "[1]" citations into links to the numbered source.
function cite(text, sources) {
  return esc(text).replace(/\[(\d+)\]/g, (m, n) => {
    const s = sources?.[Number(n) - 1];
    return s ? `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.title)}">${n}</a>` : m;
  });
}

// ---------- router ----------
const routes = {
  "": renderInbox, deadlines: renderDeadlines, ask: renderAsk, memory: renderMemory, settings: renderSettings, doc: renderDoc,
};
let pollTimer = null;

function route() {
  clearTimeout(pollTimer);
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (name || "inbox") || (name === "doc" && a.dataset.route === "inbox")));
  (routes[name] || renderInbox)(arg).catch((e) => { app.innerHTML = `<div class="card">⚠️ ${esc(e.message)}</div>`; });
  app.focus({ preventScroll: true });
  refreshBadge();
}
window.addEventListener("hashchange", route);

async function refreshBadge() {
  try {
    const tasks = await api("/api/tasks");
    const urgent = tasks.filter((t) => !t.done && t.due_date && daysUntil(t.due_date) <= 7).length;
    const b = $("#deadline-count");
    b.textContent = urgent; b.hidden = !urgent;
  } catch {}
}

// ---------- inbox ----------
async function renderInbox() {
  const [docs, samples] = await Promise.all([api("/api/documents"), api("/api/samples")]);
  app.innerHTML = `
    <section class="hero">
      <div>
        <h1>The AI that reads your scary mail.</h1>
        <p class="lead">Snap a photo of any letter — a debt collector, landlord, hospital bill or a “final notice”. Paperwise tells you what it really means, in your language, spots scams, checks your rights, and writes the reply.</p>
        <div class="trust">
          <span class="chip brand">🔒 Private workspace</span>
          <span class="chip brand">🌍 Any language</span>
          <span class="chip brand">⚡ NVIDIA Nemotron on Nebius</span>
        </div>
      </div>
      <div>
        <div class="drop" id="drop" tabindex="0" role="button" aria-label="Upload a letter">
          <div class="big">📸</div>
          <strong>Drop a photo or PDF of a letter</strong>
          <span class="muted small">JPG, PNG, PDF · multiple pages OK</span>
          <div class="actions">
            <button class="btn primary" id="pick">Choose file</button>
            <button class="btn" id="camera">Take photo</button>
            <button class="btn" id="paste">Paste text</button>
          </div>
          <input type="file" id="file" accept="image/*,.pdf,.txt" multiple hidden />
          <input type="file" id="cam" accept="image/*" capture="environment" hidden />
        </div>
      </div>
    </section>

    <div class="section-title"><h2>No letter handy? Try one of these</h2></div>
    <div class="samples">${samples.map((s) => `
      <button class="sample" data-sample="${esc(s.id)}"><strong>${esc(s.label)}</strong><span>${esc(s.blurb)}</span></button>`).join("")}
    </div>

    <div class="section-title"><h2>Your letters</h2><span class="muted small">${docs.length ? docs.length + " in your archive" : ""}</span></div>
    <div class="doc-list">${docs.length ? docs.map(docItem).join("") : `<div class="empty card">Nothing here yet. Upload a letter or try a sample above.</div>`}</div>`;

  const file = $("#file"), cam = $("#cam"), drop = $("#drop");
  $("#pick").onclick = (e) => { e.stopPropagation(); file.click(); };
  $("#camera").onclick = (e) => { e.stopPropagation(); cam.click(); };
  $("#paste").onclick = (e) => { e.stopPropagation(); pasteDialog(); };
  drop.onclick = () => file.click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } };
  file.onchange = cam.onchange = (e) => e.target.files.length && uploadFiles(e.target.files);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files.length && uploadFiles(e.dataTransfer.files); };
  app.querySelectorAll("[data-sample]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { const { id } = await api(`/api/samples/${b.dataset.sample}`, { method: "POST" }); location.hash = `#/doc/${id}`; }
    catch (e) { toast(e.message, true); b.disabled = false; }
  }));
  if (docs.some((d) => !["done", "error"].includes(d.status))) pollTimer = setTimeout(() => location.hash === "" || location.hash === "#/" ? renderInbox() : null, 2500);
}

function docItem(d) {
  const processing = !["done", "error"].includes(d.status);
  const urg = d.urgency || "";
  return `<a class="doc-item" href="#/doc/${d.id}">
    <span class="bar ${esc(urg)}"></span>
    <div>
      <div class="title">${TYPE_ICON[d.doc_type] || "✉️"} ${esc(d.title || (processing ? "Reading your letter…" : "Letter"))}</div>
      <div class="sub">${d.status === "error" ? "⚠️ " + esc(d.error) : esc(d.summary || (d.sender ? "From " + d.sender : ""))}</div>
    </div>
    <div class="meta">
      ${processing ? `<span class="chip brand">Working…</span>` : urg ? `<span class="chip ${esc(urg)}">${URGENCY_LABEL[urg] || urg}</span>` : ""}
      ${d.scam === "high" ? `<span class="chip high">🚩 Likely scam</span>` : d.scam === "medium" ? `<span class="chip medium">⚠️ Possible scam</span>` : ""}
      <span class="muted small">${fmtDate(d.created_at.slice(0, 10))}</span>
    </div>
  </a>`;
}

async function uploadFiles(fileList) {
  const fd = new FormData();
  [...fileList].forEach((f) => fd.append("files", f));
  toast("Uploading…");
  try { const { id } = await api("/api/documents", { method: "POST", body: fd }); location.hash = `#/doc/${id}`; }
  catch (e) { toast(e.message, true); }
}

function pasteDialog() {
  app.insertAdjacentHTML("afterbegin", `<div class="card" id="paste-card" style="margin-bottom:20px">
    <h2>Paste the text of a letter or email</h2>
    <textarea id="paste-text" rows="10" placeholder="Paste here…"></textarea>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="paste-go">Explain this</button><button class="btn ghost" id="paste-x">Cancel</button></div>
  </div>`);
  $("#paste-text").focus();
  $("#paste-x").onclick = () => $("#paste-card").remove();
  $("#paste-go").onclick = async () => {
    try { const { id } = await api("/api/documents/text", { method: "POST", json: { text: $("#paste-text").value } }); location.hash = `#/doc/${id}`; }
    catch (e) { toast(e.message, true); }
  };
}

// ---------- document ----------
async function renderDoc(id) {
  const d = await api(`/api/documents/${id}`);
  const processing = !["done", "error"].includes(d.status);
  const a = d.analysis;
  const pages = (d.filenames || []).filter((f) => /\.(png|jpe?g|webp|gif|bmp)$/i.test(f));

  const side = `<aside class="doc-side">
      <div class="card">
        <h3>${processing ? "Paperwise is working…" : "How Paperwise read this"}</h3>
        <ul class="steps">${(d.steps || []).map((s) => `
          <li class="step ${esc(s.state)}"><span class="dot">${s.state === "done" ? "✓" : s.state === "error" ? "!" : s.state === "skipped" ? "–" : ""}</span>
            <div><div class="lbl">${esc(s.label)}</div>
            <div class="tech">${esc(s.model || s.tech)}${s.ms ? ` · ${(s.ms / 1000).toFixed(1)}s` : ""}${s.note ? ` · ${esc(s.note)}` : ""}</div></div></li>`).join("")}
        </ul>
      </div>
      ${pages.map((_, i) => `<img class="page-thumb" src="/api/documents/${d.id}/pages/${i + 1}" alt="Page ${i + 1} of the original letter" />`).join("")}
      ${d.raw_text ? `<details class="card"><summary>Transcribed text</summary><pre class="transcript">${esc(d.raw_text)}</pre></details>` : ""}
      <button class="btn danger sm no-print" id="del">Delete this letter</button>
    </aside>`;

  if (!a) {
    app.innerHTML = `<div class="doc-layout"><div>
        <div class="doc-head"><a href="#/" class="muted small">← Inbox</a><h1>${d.status === "error" ? "We couldn't read this one" : "Reading your letter…"}</h1>
        <p class="muted">${d.status === "error" ? esc(d.error) : "This usually takes 20–40 seconds. You can leave this page — we'll keep working."}</p></div>
      </div>${side}</div>`;
    bindDelete(d.id);
    if (processing) pollTimer = setTimeout(() => location.hash.endsWith(id) && renderDoc(id), 1800);
    return;
  }

  const r = d.research || {};
  const scam = a.scam_risk || {};
  const links = d.links || [];
  const replyOpts = [...new Set([...(a.reply_options || []), "request_more_time", "custom"])];

  app.innerHTML = `<div class="doc-layout"><div>
    <div class="doc-head">
      <a href="#/" class="muted small">← Inbox</a>
      <h1>${TYPE_ICON[a.doc_type] || "✉️"} ${esc(a.title)}</h1>
      <div class="row">
        <span class="chip ${esc(a.urgency)}">${URGENCY_LABEL[a.urgency] || esc(a.urgency)}</span>
        ${a.sender?.name ? `<span class="chip">From ${esc(a.sender.name)}</span>` : ""}
        ${a.original_language ? `<span class="chip">Written in ${esc(a.original_language)}</span>` : ""}
        ${processing ? `<span class="chip brand">Still researching…</span>` : ""}
      </div>
    </div>

    ${scam.level === "high" ? `<div class="banner scam-high"><div class="icon">🚩</div><div><h3>This looks like a scam. Do not pay or call the number in the letter.</h3>
        <ul>${scam.signals.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>${r.scam_verdict ? `<p>${cite(r.scam_verdict, r.sources)}</p>` : ""}</div></div>`
      : scam.level === "medium" ? `<div class="banner scam-medium"><div class="icon">⚠️</div><div><h3>Some warning signs — verify before you pay</h3>
        <ul>${scam.signals.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>${r.scam_verdict ? `<p>${cite(r.scam_verdict, r.sources)}</p>` : ""}</div></div>` : ""}

    ${r.headline && scam.level !== "high" ? `<div class="banner finding"><div class="icon">💡</div><div><h3>Paperwise found something important</h3>
        <p style="margin:0">${cite(r.headline, r.sources)}</p></div></div>` : ""}

    ${links.length ? `<div class="banner links"><div class="icon">🧠</div><div><h3>Paperwise remembers: this is connected to ${links.length === 1 ? "an earlier letter" : links.length + " earlier letters"}</h3>
        <ul>${links.map((l) => `<li><a href="#/doc/${esc(l.id)}">${esc(l.title)}</a> (${fmtDate(l.date)}) — ${esc(l.note)}</li>`).join("")}</ul></div></div>` : ""}

    <div class="card">
      <h2>In plain words</h2>
      <p class="summary">${esc(a.summary)}</p>
      ${a.what_it_means ? `<p>${esc(a.what_it_means)}</p>` : ""}
      ${a.urgency_reason ? `<p class="muted small">Why this urgency: ${esc(a.urgency_reason)}</p>` : ""}
      ${a.amounts?.length ? `<div class="amounts">${a.amounts.map((m) => `<div class="amount"><div class="v">${fmtMoney(m)}</div><div class="w">${esc(m.what)}</div></div>`).join("")}</div>` : ""}
    </div>

    ${a.deadlines?.length ? `<div class="card"><div class="row"><h2>📅 ${scam.level === "high" ? "Dates this letter claims" : "Deadlines"}</h2><span class="spacer"></span>${scam.level === "high" ? `<span class="chip high">Not added to your calendar — verify first</span>` : `<a class="btn sm" href="/api/calendar.ics">Add to my calendar</a>`}</div>
      ${a.deadlines.map((dl) => { const n = daysUntil(dl.date); const dt = dl.date ? new Date(dl.date + "T00:00:00") : null; return `
        <div class="deadline"><div class="cal ${n !== null && n <= 7 ? "soon" : ""}">
          <div class="m">${dt ? dt.toLocaleDateString(undefined, { month: "short" }) : "—"}</div><div class="d">${dt ? dt.getDate() : "?"}</div><div class="c">${dt ? relDays(n) : "date unclear"}</div></div>
          <div><strong>${esc(dl.what)}</strong>${String(dl.estimated) === "true" ? ` <span class="chip">estimated</span>` : ""}<div class="muted small">If missed: ${esc(dl.consequence)}</div></div></div>`; }).join("")}
    </div>` : ""}

    ${a.action_items?.length ? `<div class="card"><h2>✅ What to do</h2><ol class="actions-list">
      ${[...a.action_items].sort((x, y) => (+x.priority || 9) - (+y.priority || 9)).map((i) => `<li><strong>${esc(i.action)}</strong><div class="muted small">${esc(i.how)}</div></li>`).join("")}</ol></div>` : ""}

    ${a.if_ignored ? `<div class="card"><h2>🙈 If you ignore it</h2><p>${esc(a.if_ignored)}</p></div>` : ""}

    ${r.rights?.length || r.legal_help ? `<div class="card"><h2>⚖️ Your rights</h2>
      <ul class="rights">${(r.rights || []).map((x) => `<li>${cite(x.point || x, r.sources)}</li>`).join("")}</ul>
      ${r.legal_help ? `<p><strong>Free help:</strong> ${cite(r.legal_help, r.sources)}</p>` : ""}
      <details><summary class="small">Sources (${(r.sources || []).length}) — researched live with Tavily</summary>
        <ol class="sources">${(r.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></li>`).join("")}</ol></details>
    </div>` : ""}

    ${a.questions_to_ask?.length ? `<div class="card"><h2>❓ Questions to ask them</h2><ul>${a.questions_to_ask.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></div>` : ""}

    <div class="card no-print-parent" id="reply">
      <h2>✍️ Write my reply</h2>
      <p class="muted small">Paperwise drafts a firm, polite letter in the sender's language — with a translation for you.</p>
      <div class="row" id="intents">${replyOpts.map((o) => `<button class="btn sm" data-intent="${esc(o)}">${esc(INTENT_LABEL[o] || o)}</button>`).join("")}</div>
      <div class="field" style="margin-top:12px"><textarea id="notes" rows="2" placeholder="Anything to add? e.g. “I never had an account with Brightline” or “I can pay $50/month”"></textarea></div>
      <div id="drafts">${(d.drafts || []).map((x) => draftBox(x.content, x.intent)).join("")}</div>
    </div>
  </div>${side}</div>`;

  bindDelete(d.id);
  app.querySelectorAll("[data-intent]").forEach((b) => (b.onclick = () => makeDraft(d.id, b.dataset.intent, b)));
  bindDraftBoxes();
  if (processing) pollTimer = setTimeout(() => location.hash.endsWith(id) && renderDoc(id), 2000);
}

const INTENT_LABEL = {
  dispute: "Dispute it", validation_request: "Demand proof of debt", payment_plan: "Ask for a payment plan", hardship: "Ask for hardship help",
  appeal: "Appeal the decision", request_itemized_bill: "Request itemized bill", request_more_time: "Ask for more time", report_scam: "Report this scam", custom: "Something else…",
};

function fmtMoney(m) {
  const v = Number(m.value);
  if (!isFinite(v)) return esc(m.value);
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: m.currency || "USD" }).format(v); }
  catch { return `${v.toFixed(2)} ${esc(m.currency || "")}`; }
}

function draftBox(c, intent) {
  const hasT = c.translation && c.translation.trim();
  return `<div class="draft-box" data-draft>
    <div class="row"><strong>${esc(INTENT_LABEL[intent] || intent)}</strong><span class="spacer"></span>
      <button class="btn sm" data-copy>Copy</button><button class="btn sm" data-dl>Download</button><button class="btn sm" data-print>Print</button></div>
    ${hasT ? `<div class="tabs" style="margin-top:10px"><button class="on" data-tab="letter">Letter to send</button><button data-tab="translation">What it says (translation)</button></div>` : ""}
    <p class="small muted" style="margin:8px 0">${esc(c.subject || "")}</p>
    <pre data-pane="letter">${esc(c.letter)}</pre>
    ${hasT ? `<pre data-pane="translation" hidden>${esc(c.translation)}</pre>` : ""}
    ${c.send_tips?.length ? `<p class="small" style="margin-top:12px"><strong>Sending tips:</strong></p><ul class="small">${c.send_tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
  </div>`;
}

function bindDraftBoxes() {
  app.querySelectorAll("[data-draft]").forEach((box) => {
    const letter = () => $('[data-pane="letter"]', box).textContent;
    $("[data-copy]", box).onclick = async () => { await navigator.clipboard.writeText(letter()); toast("Copied to clipboard"); };
    $("[data-dl]", box).onclick = () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([letter()], { type: "text/plain" }));
      a.download = "paperwise-reply.txt"; a.click();
    };
    $("[data-print]", box).onclick = () => {
      const w = window.open("", "_blank");
      w.document.write(`<pre style="font:15px/1.6 Georgia,serif;white-space:pre-wrap;max-width:700px;margin:60px auto">${esc(letter())}</pre>`);
      w.document.close(); w.print();
    };
    box.querySelectorAll("[data-tab]").forEach((t) => (t.onclick = () => {
      box.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x === t));
      box.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== t.dataset.tab));
    }));
  });
}

async function makeDraft(id, intent, btn) {
  const notes = $("#notes").value;
  if (intent === "custom" && !notes.trim()) { toast("Tell Paperwise what the letter should say in the box below."); $("#notes").focus(); return; }
  const all = app.querySelectorAll("[data-intent]");
  all.forEach((b) => (b.disabled = true));
  const old = btn.textContent; btn.textContent = "Writing…";
  try {
    const c = await api(`/api/documents/${id}/draft`, { method: "POST", json: { intent, notes } });
    $("#drafts").insertAdjacentHTML("afterbegin", draftBox(c, intent));
    bindDraftBoxes();
    $("#drafts .draft-box").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { toast(e.message, true); }
  all.forEach((b) => (b.disabled = false)); btn.textContent = old;
}

function bindDelete(id) {
  $("#del").onclick = async () => {
    if (!confirm("Delete this letter and everything Paperwise learned from it?")) return;
    await api(`/api/documents/${id}`, { method: "DELETE" });
    toast("Deleted"); location.hash = "#/";
  };
}

// ---------- deadlines ----------
async function renderDeadlines() {
  const tasks = await api("/api/tasks");
  const groups = { Overdue: [], "This week": [], Later: [], "To-dos": [], Done: [] };
  for (const t of tasks) {
    const n = daysUntil(t.due_date);
    if (t.done) groups.Done.push(t);
    else if (n === null) groups["To-dos"].push(t);
    else if (n < 0) groups.Overdue.push(t);
    else if (n <= 7) groups["This week"].push(t);
    else groups.Later.push(t);
  }
  app.innerHTML = `<div class="row"><div><h1>Deadlines & to-dos</h1><p class="muted">Every date Paperwise found in your mail, in one place.</p></div>
      <span class="spacer"></span><a class="btn primary" href="/api/calendar.ics">📅 Add all to calendar</a></div>
    <div class="card">${tasks.length ? Object.entries(groups).filter(([, v]) => v.length).map(([g, v]) => `
      <div class="group-title ${g === "Overdue" ? "overdue" : ""}">${g}</div>
      ${v.map((t) => `<div class="task ${t.done ? "done" : ""}">
        <input type="checkbox" data-task="${esc(t.id)}" ${t.done ? "checked" : ""} aria-label="Mark done" />
        <div><div class="t">${esc(t.title)}</div><div class="d">${t.detail ? esc(t.detail) + " · " : ""}${t.doc_id ? `<a href="#/doc/${esc(t.doc_id)}">${esc(t.doc_title || "letter")}</a>` : "reminder"}</div></div>
        <div class="muted small" style="text-align:right">${t.due_date ? `<strong>${fmtDate(t.due_date)}</strong><br>${relDays(daysUntil(t.due_date))}` : ""}</div>
      </div>`).join("")}`).join("") : `<div class="empty">No deadlines yet. They'll appear here as you add letters.</div>`}</div>`;
  app.querySelectorAll("[data-task]").forEach((c) => (c.onchange = async () => {
    await api(`/api/tasks/${c.dataset.task}`, { method: "PATCH", json: { done: c.checked } });
    renderDeadlines(); refreshBadge();
  }));
}

// ---------- ask (agent chat) ----------
const chatHistory = [];
async function renderAsk() {
  app.innerHTML = `<h1>Ask Paperwise</h1>
    <div class="chat card">
      <div class="messages" id="msgs">${chatHistory.length ? "" : `
        <div class="msg assistant"><p>Hi! I know every letter in your archive and can research your rights. Try:</p>
          <div class="suggest">${["What do I need to deal with this week?", "Is the debt collector allowed to add those fees?", "Summarize everything about Northgate Recovery", "Remind me to call the hospital on Friday"].map((s) => `<button class="btn sm" data-suggest>${s}</button>`).join("")}</div></div>`}</div>
      <form class="composer" id="composer"><textarea id="q" placeholder="Ask about your letters, deadlines or rights…" aria-label="Message"></textarea><button class="btn primary">Send</button></form>
    </div>`;
  const msgs = $("#msgs");
  chatHistory.forEach((m) => msgs.insertAdjacentHTML("beforeend", bubble(m)));
  msgs.scrollTop = msgs.scrollHeight;
  app.querySelectorAll("[data-suggest]").forEach((b) => (b.onclick = () => { $("#q").value = b.textContent; send(); }));
  $("#composer").onsubmit = (e) => { e.preventDefault(); send(); };
  $("#q").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  $("#q").focus();

  async function send() {
    const q = $("#q").value.trim();
    if (!q) return;
    $("#q").value = "";
    const history = chatHistory.map(({ role, content }) => ({ role, content }));
    const userMsg = { role: "user", content: q };
    chatHistory.push(userMsg);
    msgs.insertAdjacentHTML("beforeend", bubble(userMsg) + `<div class="msg assistant" id="typing"><span class="typing"><span></span><span></span><span></span></span></div>`);
    msgs.scrollTop = msgs.scrollHeight;
    try {
      const r = await api("/api/chat", { method: "POST", json: { message: q, history } });
      const m = { role: "assistant", content: r.answer, trace: r.trace, model: r.model };
      chatHistory.push(m);
      $("#typing")?.remove();
      msgs.insertAdjacentHTML("beforeend", bubble(m));
      refreshBadge();
    } catch (e) {
      $("#typing")?.remove();
      chatHistory.pop();
      toast(e.message, true);
    }
    msgs.scrollTop = msgs.scrollHeight;
  }
}

const TOOL_ICON = { search_documents: "🔎", get_document: "📄", list_deadlines: "📅", web_research: "🌐", add_task: "⏰", remember: "🧠" };
function bubble(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.content)}</div>`;
  const trace = (m.trace || []).map((t) => `<span class="chip" title="${esc(JSON.stringify(t.args))}">${TOOL_ICON[t.tool] || "🔧"} ${esc(t.tool)} → ${esc(t.result)}</span>`).join("");
  return `<div class="msg assistant">${trace ? `<div class="trace">${trace}</div>` : ""}${md(m.content)}${m.model ? `<div class="muted small" style="margin-top:6px">${esc(m.model)}</div>` : ""}</div>`;
}

// ---------- memory ----------
async function renderMemory() {
  const facts = await api("/api/memory");
  app.innerHTML = `<h1>What Paperwise remembers</h1>
    <p class="muted">Long-term memory learned from your letters and conversations. It's used to connect new mail to old mail and answer your questions. You're in control — delete anything.</p>
    <div class="card">${facts.length ? facts.map((f) => `<div class="fact"><span>🧠</span><div style="flex:1">${esc(f.fact)}${f.doc_id ? ` <a class="small" href="#/doc/${esc(f.doc_id)}">source</a>` : ` <span class="chip">from chat</span>`}</div>
      <button class="btn sm ghost" data-forget="${esc(f.id)}" aria-label="Forget">Forget</button></div>`).join("") : `<div class="empty">Nothing yet.</div>`}</div>
    <div class="card"><h2>🔒 Your data</h2><p class="small">Your letters live in your private workspace on this Paperwise server, and are processed by open-weight NVIDIA Nemotron models on Nebius Token Factory. Wipe everything at any time:</p>
      <button class="btn danger" id="wipe">Delete all my data</button></div>`;
  app.querySelectorAll("[data-forget]").forEach((b) => (b.onclick = async () => { await api(`/api/memory/${b.dataset.forget}`, { method: "DELETE" }); renderMemory(); }));
  $("#wipe").onclick = async () => {
    if (!confirm("Permanently delete all letters, deadlines, drafts and memory?")) return;
    await api("/api/workspace", { method: "DELETE" });
    chatHistory.length = 0;
    toast("Everything deleted"); location.hash = "#/";
  };
}

// ---------- settings ----------
const LANGS = ["English", "Español", "中文 (Chinese)", "Tiếng Việt", "Tagalog", "العربية (Arabic)", "हिन्दी (Hindi)", "Français", "Português", "Русский", "한국어 (Korean)", "Polski", "Українська", "فارسی (Farsi)", "Kreyòl ayisyen", "Deutsch", "Italiano", "日本語 (Japanese)", "Türkçe", "বাংলা (Bengali)", "اردو (Urdu)", "Kiswahili"];
async function renderSettings() {
  const [p, h] = await Promise.all([api("/api/profile"), api("/api/health").catch(() => null)]);
  app.innerHTML = `<h1>Settings</h1>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
      <form class="card" id="pf">
        <h2>About you</h2>
        <p class="muted small">Used to explain letters in your language, find the rights that apply where you live, and sign your replies.</p>
        <div class="field"><label for="language">Explain my letters in</label><select id="language">${[...new Set([p.language, ...LANGS])].map((l) => `<option ${l === p.language ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
        <div class="field"><label for="reading_level">Explanation style</label><select id="reading_level">
          ${[["simple", "Simple — short sentences, no jargon"], ["standard", "Standard"], ["detailed", "Detailed — legal terms OK"]].map(([v, l]) => `<option value="${v}" ${v === p.reading_level ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="row"><div class="field" style="flex:1"><label for="country">Country</label><input id="country" value="${esc(p.country)}" /></div>
          <div class="field" style="flex:1"><label for="region">State / province</label><input id="region" value="${esc(p.region)}" placeholder="e.g. California" /></div></div>
        <div class="field"><label for="name">Your name (for replies)</label><input id="name" value="${esc(p.name)}" /></div>
        <div class="field"><label for="address">Your address (for replies)</label><input id="address" value="${esc(p.address)}" /></div>
        <button class="btn primary">Save</button>
      </form>
      <div class="card">
        <h2>Under the hood</h2>
        ${h ? `<div class="status-grid">
          <span>Vision</span><code>${esc(h.models.vision || "not available")}</code>
          <span>Reasoning</span><code>${esc(h.models.reasoning)}</code>
          <span>Fast</span><code>${esc(h.models.fast)}</code>
          <span>Inference</span><code>${esc(h.base_url)}</code>
          <span>Nebius key</span><span>${h.nebius_key ? "✅ configured" : "❌ missing"}</span>
          <span>Tavily</span><span>${h.tavily ? "✅ live web research" : "❌ disabled"}</span></div>` : "Status unavailable"}
      </div>
    </div>`;
  $("#pf").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(["language", "reading_level", "country", "region", "name", "address"].map((k) => [k, $("#" + k).value]));
    await api("/api/profile", { method: "PUT", json: body });
    toast("Saved — new letters will use these settings");
  };
}

route();
