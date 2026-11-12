/* Internship Radar — single-page interface.  All text coming from the web is escaped with esc(). */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (value) => (/^https?:\/\//i.test(String(value || "")) ? esc(value) : "#");
const link = (url, label) => `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;

const S = {
  view: "today", state: null, offers: [], contacts: [], applications: [], directory: [], launchpad: [], agents: null,
  filters: { q: "", tier: "all", country: "", grade: "all", status: "open", kind: "all", clean: false, sort: "score", limit: 60 },
  people: { q: "", kind: "all", tier: "all", email: false, limit: 60 },
  dir: { q: "", tier: "all", kind: "all" },
  poll: null,
};

const TIERS = { tier1: "France · Italy · Belgium", tier2: "Germany · Luxembourg", tier3: "Rest of Europe", mena: "Egypt · Gulf" };
const STAGES = [
  ["to_review", "To review"], ["ready", "Needs attention"], ["scheduled", "Approved — sending soon"], ["sent", "Sent"], ["follow_up", "Follow-up due"],
  ["interview", "Interview"], ["offer", "Offer"], ["rejected", "Closed"],
];
const VIEWS = [
  ["today", "Today", "M3 12l9-8 9 8M5 10v10h14V10"],
  ["offers", "Offers", "M4 6h16M4 12h16M4 18h10"],
  ["pipeline", "Pipeline", "M4 5h4v14H4zM10 5h4v9h-4zM16 5h4v6h-4z"],
  ["people", "People", "M16 19v-1a4 4 0 0 0-8 0v1M12 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"],
  ["directory", "Directory", "M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"],
  ["launchpad", "Launchpad", "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"],
  ["agents", "Agents", "M12 3v3M5.6 5.6l2.1 2.1M3 12h3M18 12h3M16.3 7.7l2.1-2.1M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM7 21h10"],
  ["settings", "Profile & priorities", "M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4"],
];

/* ------------------------------------------------------------------ helpers */
async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.method = options.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({ error: "Unexpected response." }));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function toast(message, error = false) {
  const node = document.createElement("div");
  node.className = "toast" + (error ? " err" : "");
  node.textContent = message;
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), error ? 7000 : 3600);
}
const grade = (score) => (score >= 85 ? "A" : score >= 72 ? "B" : score >= 58 ? "C" : "D");
const country = (code) => (S.state?.countries?.[code] || code || "Remote");
const scoreRing = (score) => `<div class="score g${grade(score)}" style="--p:${Math.max(4, score)}" title="Fit score ${score}/100 — grade ${grade(score)}"><span>${score}</span></div>`;
const daysAgo = (iso) => (iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 864e5) : null);
const isNew = (offer) => daysAgo(offer.first_seen) !== null && daysAgo(offer.first_seen) <= 2;
function ago(iso) {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 6e4);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}
function deadlineTag(deadline) {
  if (!deadline) return "";
  const left = Math.ceil((new Date(deadline).getTime() - Date.now()) / 864e5);
  if (left < 0) return `<span class="tag bad">deadline passed</span>`;
  return `<span class="tag ${left <= 14 ? "warn" : ""}">apply by ${esc(deadline)}</span>`;
}
const tierOf = (code) => {
  const tiers = S.state?.priorities?.tiers || {};
  return Object.keys(tiers).find((name) => tiers[name].countries.includes(code)) || "tier3";
};
function debounce(fn, wait = 180) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
}

/* --------------------------------------------------------------------- data */
async function loadState() { S.state = await api("/api/state"); renderChrome(); }
async function loadOffers() { S.offers = (await api("/api/offers")).offers; }
async function loadContacts() { S.contacts = (await api("/api/contacts")).contacts; }
async function loadApplications() { S.applications = (await api("/api/applications")).applications; }

async function refreshAll() {
  await Promise.all([loadState(), loadOffers(), loadApplications()]);
  render();
}

/* ------------------------------------------------------------------- chrome */
function renderChrome() {
  const state = S.state;
  const counts = {
    offers: state.offers.total, pipeline: Object.values(state.stages).reduce((a, b) => a + b, 0),
    people: Object.values(state.contacts).reduce((a, b) => a + b, 0),
  };
  $("#nav").innerHTML = VIEWS.map(([id, label, path]) => `
    <button data-view="${id}" class="${S.view === id ? "on" : ""}">
      <svg viewBox="0 0 24 24"><path d="${path}"/></svg>${label}
      ${counts[id] ? `<span class="count">${counts[id]}</span>` : ""}
    </button>`).join("");
  const run = state.run;
  document.body.classList.toggle("running", !!run.running);
  $("#runbox").innerHTML = run.running
    ? `<b><span class="pulse"></span>${esc(run.agent || "starting")} agent working</b>${esc(run.progress || "")}`
    : `<b>Agents idle</b>Last run ${esc(ago(state.last_run?.finished_at || state.last_run?.started_at))} · ${state.health.ok}/${state.health.total} sources healthy`;
  $("#runAll").disabled = !!run.running;
  $("#runAll").textContent = run.running ? "Running…" : "Run all agents";
  const profile = state.profile;
  $("#brandSub").textContent = `PFE · ${profile.availability_start.slice(0, 7)} → ${profile.availability_end.slice(0, 7)}`;
  if (run.running && !S.poll) startPolling();
}
function startPolling() {
  clearInterval(S.poll);
  S.poll = setInterval(async () => {
    try {
      await loadState();
      if (S.view === "agents") renderAgentsLog();
      if (!S.state.run.running) {
        clearInterval(S.poll); S.poll = null;
        toast("Agents finished — results updated.");
        await Promise.all([loadOffers(), loadApplications()]);
        if (S.view === "people") await loadContacts();
        if (S.view === "agents") S.agents = await api("/api/agents");
        render();
      }
    } catch (error) { /* the server may be restarting; keep polling */ }
  }, 2500);
}
async function runAgents(agents, force = false) {
  try {
    const result = await api("/api/run", { body: { agents, force } });
    toast(result.started ? "Agents started." : "A run is already in progress.");
    await loadState();
    startPolling();
  } catch (error) { toast(error.message, true); }
}

/* ------------------------------------------------------------------- render */
function setHeader(title, sub, actions = "") {
  $("#viewTitle").textContent = title;
  $("#viewSub").textContent = sub;
  $("#topActions").innerHTML = actions;
}
async function go(view, updateHash = true) {
  S.view = view;
  if (updateHash && location.hash.slice(1).split("/")[0] !== view) history.pushState(null, "", `#${view}`);
  closeDrawer();
  closeModal();
  renderChrome();
  try {
    if (view === "people" && !S.contacts.length) await loadContacts();
    if (view === "directory" && !S.directory.length) S.directory = (await api("/api/directory")).directory;
    if (view === "launchpad" && !S.launchpad.length) S.launchpad = (await api("/api/launchpad")).boards;
    if (view === "agents") S.agents = await api("/api/agents");
  } catch (error) { toast(error.message, true); }
  render();
  window.scrollTo(0, 0);
}
function render() {
  ({ today: viewToday, offers: viewOffers, pipeline: viewPipeline, people: viewPeople, directory: viewDirectory,
     launchpad: viewLaunchpad, agents: viewAgents, settings: viewSettings }[S.view])();
}

function offerRow(offer) {
  const flags = offer.flags || [];
  const tags = (offer.tags || []).filter((tag) => !["Machine learning", "Data & analytics", "Software"].includes(tag)).slice(0, 3);
  return `
  <div class="offer ${offer.status === "dismissed" || !offer.active ? "dim" : ""}" data-offer="${offer.id}">
    ${scoreRing(offer.score)}
    <div style="min-width:0">
      <div class="o-title">${esc(offer.title)}</div>
      <div class="o-meta">
        <span>${esc(offer.org || "—")}</span><span class="sep">•</span>
        <span>${esc(offer.city ? offer.city + ", " : "")}${esc(country(offer.country))}</span>
        ${tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}
        ${offer.reviewed ? `<span class="tag info" title="Reviewed by the AI analyst">AI reviewed</span>` : ""}
        ${deadlineTag(offer.deadline)}
      </div>
    </div>
    <div class="o-side">
      ${flags.length ? `<span class="tag warn" title="${esc(flags.join(" · "))}">${flags.length} check${flags.length > 1 ? "s" : ""}</span>` : ""}
      ${offer.contact_count ? `<span class="tag" title="Named contact available">${offer.contact_count} contact${offer.contact_count > 1 ? "s" : ""}</span>` : ""}
      ${isNew(offer) ? `<span class="tag new">new</span>` : ""}
      ${offer.status !== "new" ? `<span class="tag ${offer.status === "dismissed" || offer.status === "rejected" ? "bad" : "info"}">${esc(offer.status)}</span>` : ""}
      ${!offer.active ? `<span class="tag bad" title="No longer listed by the source">closed</span>` : ""}
    </div>
  </div>`;
}

/* -------------------------------------------------------------------- today */
function viewToday() {
  const state = S.state;
  const floor = state.priorities.min_score || 0;
  const open = S.offers.filter((offer) => offer.active && ["new", "shortlisted"].includes(offer.status) && offer.score >= floor);
  const best = open.slice(0, 8);
  const fresh = open.filter(isNew).slice(0, 6);
  const todo = S.applications.filter((item) => ["to_review", "ready", "follow_up"].includes(item.stage));
  const inFlight = ["sent", "follow_up", "interview"].reduce((sum, stage) => sum + (state.stages[stage] || 0), 0);
  const maxTier = Math.max(1, ...Object.values(state.by_tier));
  setHeader("Today", `${state.offers.total} open internships match your profile across ${state.by_country.length} countries — ranked for a ${state.profile.duration_months}-month PFE starting ${state.profile.availability_start}.`,
    `<button class="btn" data-go="offers">Browse all offers</button>`);
  $("#view").innerHTML = `
    <div class="grid kpis">
      <div class="card kpi" data-go="offers"><div class="v accent">${state.offers.grade_a}</div><div class="l">Grade A matches (85+)</div></div>
      <div class="card kpi" data-go="offers"><div class="v">${state.offers.total}</div><div class="l">Open matching offers</div></div>
      <div class="card kpi" data-go="offers"><div class="v">${state.offers.fresh}</div><div class="l">New in the last 48 h</div></div>
      <div class="card kpi" data-go="people"><div class="v">${Object.values(state.contacts).reduce((a, b) => a + b, 0)}</div><div class="l">Contacts · ${state.email_count} with a published e-mail</div></div>
      <div class="card kpi" data-go="pipeline"><div class="v">${inFlight}</div><div class="l">Applications in flight · ${todo.length} waiting for you</div></div>
    </div>
    <div class="grid two" style="margin-top:14px">
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>Best matches right now</h2><span class="hint">domain · type · location · timing · skills</span></div>
          <div class="list">${best.map(offerRow).join("") || `<div class="empty">No offers yet — press “Run all agents”.</div>`}</div>
        </div>
        <div class="card">
          <div class="card-head"><h2>New since your last visit</h2><span class="hint">${fresh.length ? "first seen within 48 h" : ""}</span></div>
          <div class="list">${fresh.map(offerRow).join("") || `<div class="empty">Nothing new in the last 48 hours.</div>`}</div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>Waiting for you</h2><button class="btn small" data-go="pipeline">Open pipeline</button></div>
          <div class="list">${todo.slice(0, 6).map((item) => `
            <div class="offer" style="grid-template-columns:minmax(0,1fr) auto" data-app="${item.id}">
              <div style="min-width:0"><div class="o-title">${esc(item.offer_title || item.contact_name || item.subject)}</div>
              <div class="o-meta">${esc(item.offer_org || item.contact_org || "")}</div></div>
              <span class="tag ${item.stage === "follow_up" ? "warn" : "info"}">${esc(STAGES.find(([id]) => id === item.stage)[1])}</span>
            </div>`).join("") || `<div class="empty">No drafts or follow-ups pending.</div>`}</div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Where the offers are</h2><span class="hint">click to filter</span></div>
          <div class="regions">${Object.entries(TIERS).map(([tier, label]) => `
            <div class="region" data-tier="${tier}"><span class="rl">${label}</span>
              <div class="bar"><i style="width:${Math.round(((state.by_tier[tier] || 0) / maxTier) * 100)}%"></i></div>
              <b>${state.by_tier[tier] || 0}</b></div>`).join("")}
            <div class="small muted" style="margin-top:4px">${state.by_country.slice(0, 9).map((row) => `${esc(country(row.country))} ${row.n}`).join(" · ")}</div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Closing soon</h2><span class="hint">next 21 days</span></div>
          <div class="list">${state.closing.map((item) => `
            <div class="offer" style="grid-template-columns:minmax(0,1fr) auto" data-offer="${item.id}">
              <div style="min-width:0"><div class="o-title">${esc(item.title)}</div><div class="o-meta">${esc(item.org)}</div></div>
              <span class="tag warn">${esc(item.deadline)}</span></div>`).join("") || `<div class="empty">No published deadline in the next three weeks.</div>`}</div>
        </div>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------- offers */
function filteredOffers() {
  const f = S.filters;
  const floor = S.state.priorities.min_score || 0;
  const query = f.q.trim().toLowerCase();
  let rows = S.offers.filter((offer) => {
    if (f.status === "open" && !(offer.active && ["new", "shortlisted"].includes(offer.status) && offer.score >= floor)) return false;
    if (f.status === "shortlisted" && offer.status !== "shortlisted") return false;
    if (f.status === "applied" && !["applied", "interview", "offer"].includes(offer.status)) return false;
    if (f.status === "dismissed" && offer.status !== "dismissed") return false;
    if (f.status === "new" && !(isNew(offer) && offer.active && offer.status === "new")) return false;
    if (f.tier !== "all" && offer.tier !== f.tier) return false;
    if (f.country && offer.country !== f.country) return false;
    if (f.grade !== "all" && grade(offer.score) !== f.grade) return false;
    if (f.kind !== "all" && offer.source_kind !== f.kind && !(f.kind === "research" && offer.source_kind === "programme")) return false;
    if (f.clean && (offer.flags || []).length) return false;
    if (query && !`${offer.title} ${offer.org} ${offer.city} ${country(offer.country)} ${(offer.tags || []).join(" ")}`.toLowerCase().includes(query)) return false;
    return true;
  });
  if (f.sort === "recent") rows = rows.slice().sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || b.score - a.score);
  if (f.sort === "deadline") rows = rows.slice().sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999") || b.score - a.score);
  return rows;
}
function viewOffers() {
  const f = S.filters;
  const countries = [...new Set(S.offers.map((offer) => offer.country).filter(Boolean))].sort((a, b) => country(a).localeCompare(country(b)));
  setHeader("Offers", "Every posting comes from a live source and links back to it. Scores are explained when you open an offer.",
    `<button class="btn" id="exportCsv">Export CSV</button>`);
  $("#view").innerHTML = `
    <div class="toolbar">
      <input class="input search" id="q" placeholder="Search title, employer, city, skill…" value="${esc(f.q)}">
      <select class="select" id="fCountry"><option value="">All countries</option>${countries.map((code) => `<option value="${code}" ${f.country === code ? "selected" : ""}>${esc(country(code))}</option>`).join("")}</select>
      <select class="select" id="fKind">
        ${[["all", "All sources"], ["company", "Employers"], ["research", "Research labs & programmes"], ["board", "Job boards"]].map(([value, label]) => `<option value="${value}" ${f.kind === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      <select class="select" id="fSort">
        ${[["score", "Best fit first"], ["recent", "Newest first"], ["deadline", "Deadline first"]].map(([value, label]) => `<option value="${value}" ${f.sort === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
    </div>
    <div class="toolbar">
      <div class="chips" id="tierChips">${[["all", "All regions"], ...Object.entries(TIERS)].map(([value, label]) => `<button class="chip ${f.tier === value ? "on" : ""}" data-tier="${value}">${label}</button>`).join("")}</div>
      <div class="chips" id="gradeChips">${["all", "A", "B", "C"].map((value) => `<button class="chip ${f.grade === value ? "on" : ""}" data-grade="${value}">${value === "all" ? "Any grade" : "Grade " + value}</button>`).join("")}</div>
      <div class="chips" id="statusChips">${[["open", "Open"], ["new", "New"], ["shortlisted", "Shortlisted"], ["applied", "Applied"], ["dismissed", "Dismissed"], ["any", "Everything"]].map(([value, label]) => `<button class="chip ${f.status === value ? "on" : ""}" data-status="${value}">${label}</button>`).join("")}</div>
      <button class="chip ${f.clean ? "on" : ""}" id="cleanChip" title="Hide offers with eligibility or timing warnings">No warnings</button>
    </div>
    <div class="card"><div class="list" id="offerList"></div></div>`;
  renderOfferList();
}
function renderOfferList() {
  const rows = filteredOffers();
  const shown = rows.slice(0, S.filters.limit);
  $("#offerList").innerHTML = `<div class="card-head"><h2>${rows.length} offer${rows.length === 1 ? "" : "s"}</h2><span class="hint">A ≥ 85 · B ≥ 72 · C ≥ 58</span></div>`
    + (shown.map(offerRow).join("") || `<div class="empty">No offer matches these filters.</div>`)
    + (rows.length > shown.length ? `<div class="more"><button class="btn" id="moreOffers">Show ${Math.min(60, rows.length - shown.length)} more</button></div>` : "");
}
function exportCsv() {
  const rows = filteredOffers();
  const cell = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [["score", "title", "employer", "country", "city", "deadline", "status", "warnings", "url"].join(",")]
    .concat(rows.map((o) => [o.score, o.title, o.org, country(o.country), o.city, o.deadline, o.status, (o.flags || []).join(" | "), o.url].map(cell).join(",")));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const anchor = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `internship-radar-${S.state.today}.csv` });
  anchor.click();
  URL.revokeObjectURL(anchor.href);
}

/* ------------------------------------------------------------------- drawer */
function closeDrawer() { $("#drawer").classList.remove("on"); $("#scrim").classList.remove("on"); }
function personCard(person, offerId = null) {
  const papers = (person.evidence || []).filter((item) => item.type === "paper").slice(0, 2);
  return `
  <div class="person">
    <div style="min-width:0">
      <div class="n">${esc(person.name)}</div>
      <div class="small muted">${esc(person.role || "")}${person.org ? " · " + esc(person.org) : ""}</div>
      ${person.email ? `<div class="small"><a href="mailto:${esc(person.email)}">${esc(person.email)}</a> <span class="faint">— ${/^https?:/.test(person.email_source || "") ? link(person.email_source, "source") : esc(person.email_source || "")}</span></div>` : ""}
      ${papers.map((paper) => `<div class="ev">${link(paper.url, paper.text)} ${esc(paper.year ? `(${paper.year})` : "")}</div>`).join("")}
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
      <button class="btn small" data-draft-contact="${person.id}" ${offerId ? `data-for-offer="${offerId}"` : ""}>Draft e-mail</button>
      ${person.email ? "" : `<button class="btn small ghost" data-enrich="${person.id}">Find e-mail</button>`}
    </div>
  </div>`;
}
async function openOffer(id) {
  let offer;
  try { offer = (await api(`/api/offers/${id}`)).offer; } catch (error) { return toast(error.message, true); }
  const ai = offer.llm && offer.llm.summary ? offer.llm : null;
  const facts = [["Start", ai?.start], ["Duration", ai?.duration], ["Pay", ai?.paid], ["Language", ai?.language]].filter(([, value]) => value);
  $("#drawer").innerHTML = `
    <div class="d-head">
      <div class="row-between">
        <div style="display:flex;gap:13px;align-items:center;min-width:0">${scoreRing(offer.score)}
          <div style="min-width:0"><h2>${esc(offer.title)}</h2>
          <div class="o-meta">${esc(offer.org || "")} · ${esc(offer.location_raw || country(offer.country))} · <span class="tag ${offer.tier === "tier1" ? "t1" : offer.tier === "mena" ? "mena" : ""}">${esc(TIERS[offer.tier] || "Remote")}</span></div></div>
        </div>
        <button class="btn ghost small" id="closeDrawer">Close</button>
      </div>
      <div class="d-actions">
        <a class="btn primary" href="${safeUrl(offer.url)}" target="_blank" rel="noopener noreferrer">Open posting</a>
        <button class="btn" data-draft-offer="${offer.id}" data-lang="${offer.lang === "fr" ? "FR" : "EN"}">Draft application (${offer.lang === "fr" ? "FR" : "EN"})</button>
        <button class="btn" data-draft-offer="${offer.id}" data-lang="${offer.lang === "fr" ? "EN" : "FR"}">${offer.lang === "fr" ? "EN" : "FR"}</button>
        <button class="btn" data-set-status="${offer.status === "shortlisted" ? "new" : "shortlisted"}" data-id="${offer.id}">${offer.status === "shortlisted" ? "Remove from shortlist" : "Shortlist"}</button>
        <button class="btn" data-set-status="applied" data-id="${offer.id}">Mark applied</button>
        <button class="btn danger" data-set-status="${offer.status === "dismissed" ? "new" : "dismissed"}" data-id="${offer.id}">${offer.status === "dismissed" ? "Restore" : "Dismiss"}</button>
      </div>
    </div>
    <div class="d-body">
      ${ai ? `<div><div class="section-title">AI analyst — second opinion (${ai.fit}/10)</div>
        <div class="ai">${esc(ai.summary)}
          ${ai.why?.length ? `<ul>${ai.why.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>` : ""}
          ${ai.concerns?.length ? `<div class="small" style="margin-top:8px"><b>To check:</b> ${ai.concerns.map(esc).join(" · ")}</div>` : ""}
          ${facts.length ? `<dl class="kv" style="margin:10px 0 0">${facts.map(([key, value]) => `<dt>${key}</dt><dd>${esc(value)}</dd>`).join("")}</dl>` : ""}
        </div></div>` : ""}
      ${offer.flags.length ? `<div><div class="section-title">Check before applying</div><div class="flags">${offer.flags.map((flag) => `<div class="flagline">${esc(flag)}</div>`).join("")}</div></div>` : ""}
      <div><div class="section-title">Why this score</div>
        <div class="bars">${offer.breakdown.map((part) => `
          <span class="muted">${esc(part.label)}</span>
          <div class="bar ${part.value < 0 ? "neg" : ""}"><i style="width:${part.max ? Math.round((Math.abs(part.value) / part.max) * 100) : Math.min(100, Math.abs(part.value) * 2)}%"></i></div>
          <b>${part.value}${part.max && part.value >= 0 ? `<span class="faint">/${part.max}</span>` : ""}</b>`).join("")}</div>
        <div class="chips" style="margin-top:10px">${offer.tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</div>
      </div>
      <div><div class="section-title">Details</div>
        <dl class="kv">
          <dt>Source</dt><dd>${esc(offer.source)} · seen ${esc(ago(offer.last_seen))}${offer.active ? "" : " · no longer listed"}</dd>
          ${offer.posted_at ? `<dt>Posted</dt><dd>${esc(offer.posted_at)}</dd>` : ""}
          ${offer.deadline ? `<dt>Deadline</dt><dd>${esc(offer.deadline)}</dd>` : ""}
          <dt>Contract</dt><dd>${esc(offer.contract || "internship")}${offer.remote ? " · remote possible" : ""}</dd>
        </dl></div>
      ${offer.contacts.length || offer.related_contacts.length ? `<div><div class="section-title">People</div><div class="stack" style="gap:8px">
        ${offer.contacts.map((person) => personCard(person, offer.id)).join("")}
        ${offer.related_contacts.length ? `<div class="small muted">Researchers at the same organisation, found through their recent publications:</div>` : ""}
        ${offer.related_contacts.map((person) => personCard(person)).join("")}</div></div>` : ""}
      <div><div class="section-title">Your notes</div>
        <textarea class="input" id="offerNotes" rows="2" placeholder="Anything to remember about this offer…">${esc(offer.notes || "")}</textarea>
        <button class="btn small" style="margin-top:6px" data-save-notes="${offer.id}">Save notes</button></div>
      <div><div class="section-title">Posting</div><div class="desc">${esc(offer.description || "The source did not provide a description — open the posting.")}</div></div>
    </div>`;
  $("#drawer").classList.add("on");
  $("#scrim").classList.add("on");
}

/* ----------------------------------------------------------------- pipeline */
function viewPipeline() {
  const groups = Object.fromEntries(STAGES.map(([id]) => [id, []]));
  S.applications.forEach((item) => (groups[{ closed: "rejected", sending: "scheduled" }[item.stage] || item.stage] || groups.to_review).push(item));
  const mail = S.state.mail;
  setHeader("Pipeline", mail.ready
    ? `Review a draft and approve it: the e-mail leaves from your mailbox ${mail.delay_hours} hours later with your cover letter and CV attached. Nothing is sent without your approval, and you can cancel until it goes. ${mail.sent_today}/${mail.daily_cap} sent today.`
    : "Drafts are prepared for your best matches. To let the Radar send the ones you approve, add your e-mail app password under Profile & priorities.",
    `<button class="btn" data-run="writer">Prepare drafts for top matches</button>`);
  $("#view").innerHTML = S.applications.length ? `<div class="board">${STAGES.map(([id, label]) => `
    <div class="col" data-stage="${id}">
      <div class="col-head"><span>${label}</span><span class="faint">${groups[id].length}</span></div>
      <div class="col-body">${groups[id].map((item) => `
        <div class="app" draggable="true" data-app="${item.id}">
          <div class="t">${esc(item.offer_title || item.contact_name || item.subject || "Application")}</div>
          <div class="m">${esc(item.offer_org || item.contact_org || "")}</div>
          <div class="m">${item.kind === "spontaneous" ? `<span class="tag">spontaneous</span> ` : ""}<span class="tag">${esc(item.language)}</span>
            ${item.sent_at ? (item.sent_from ? ` <span class="tag new" title="Sent by the Radar from ${esc(item.sent_from)}">e-mailed ${esc(item.sent_at)}</span>` : ` <span class="tag" title="You recorded this as sent yourself; the Radar did not e-mail it">marked sent ${esc(item.sent_at)}</span>`) : ""}${item.stage === "sent" && item.follow_up_at ? ` · follow up ${esc(item.follow_up_at)}` : ""}
            ${item.stage === "scheduled" && item.send_at ? ` <span class="tag new">sends ${esc(sendCountdown(item.send_at))}</span>` : ""}
            ${item.send_error ? ` <span class="tag bad" title="${esc(item.send_error)}">sending failed</span>` : ""}
            ${!item.recipient && !item.sent_at ? ` <span class="tag warn" title="No recipient address: apply through the posting, or add an address">no address</span>` : ""}</div>
        </div>`).join("") || `<div class="small faint" style="padding:8px 4px">Empty</div>`}</div>
    </div>`).join("")}</div>`
    : `<div class="card empty">No application yet. Open an offer and choose “Draft application”, or let the Writer agent prepare drafts for your grade-A matches.</div>`;
}
function sendCountdown(iso) {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 6e4);
  if (minutes <= 0) return "any minute now";
  return minutes < 60 ? `in ${minutes} min` : `in ${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}
async function openApplication(id) {
  const item = S.applications.find((row) => row.id === Number(id));
  if (!item) return;
  const recipient = item.recipient || item.contact_email || "";
  const spontaneous = item.kind === "spontaneous";
  const mail = S.state.mail;
  const scheduled = item.stage === "scheduled" || item.stage === "sending";
  const alreadySent = !!item.sent_at;
  const cvName = (S.state.profile.cv_files || {})[item.language] || "CV";
  const banner = scheduled
    ? `<div class="checklist ok"><b>Approved.</b> This e-mail leaves automatically ${esc(sendCountdown(item.send_at))} from your mailbox. You can still edit it or cancel until then.</div>`
    : alreadySent
      ? `<div class="checklist ok">Sent on ${esc(item.sent_at)}${item.sent_from ? " from " + esc(item.sent_from) : ""}. Follow-up planned for ${esc(item.follow_up_at || "—")}.</div>`
      : `<div class="checklist">${spontaneous
          ? "Before approving: read the paper cited in the draft, confirm the person still works there, and adjust one sentence so it is clearly written for them."
          : "Before approving: re-read the posting and check the recipient. If the employer only accepts applications through a web form, apply there and use “I applied myself”."}</div>`;
  $("#modal").innerHTML = `
    <div class="modal-card">
      <div class="modal-head">
        <div><h2>${esc(item.offer_title || item.contact_name || "Application")}</h2>
          <div class="small muted">${esc(item.offer_org || item.contact_org || "")} · ${esc(item.language)} · written by ${esc((item.generator || "template").startsWith("gemini") ? "Gemini from your verified profile" : "the built-in template")}
          ${item.offer_url ? " · " + link(item.offer_url, "open posting") : ""}</div></div>
        <button class="btn ghost small" data-close-modal>Close</button>
      </div>
      <div class="modal-body">
        ${banner}
        ${item.send_error ? `<div class="checklist bad"><b>Last attempt failed:</b> ${esc(item.send_error)}</div>` : ""}
        ${mail.ready ? "" : `<div class="checklist">E-mail sending is not set up yet. <a href="#settings" data-go="settings">Add your app password</a> to let the Radar send approved applications.</div>`}
        <label class="field">To<input class="input" id="appTo" value="${esc(recipient)}" placeholder="recipient e-mail — required for automatic sending"></label>
        <label class="field">Subject<input class="input" id="appSubject" value="${esc(item.subject || "")}"></label>
        <label class="field">E-mail message<textarea class="input" id="appBody" rows="9">${esc(item.body || "")}</textarea></label>
        <label class="field">Cover letter — attached as a PDF<textarea class="input" id="appLetter" rows="12">${esc(item.letter || "")}</textarea></label>
        <div class="small muted">Attachments: <a href="/api/applications/${item.id}/letter" target="_blank" rel="noopener">${item.language === "FR" ? "Lettre de motivation" : "Cover letter"} (PDF preview — save first)</a> · ${esc(cvName)}</div>
        <label class="field">Notes<input class="input" id="appNotes" value="${esc(item.notes || "")}" placeholder="e.g. applied through the portal on …"></label>
      </div>
      <div class="modal-foot">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" data-app-save="${item.id}">Save</button>
          <a class="btn" href="/api/applications/${item.id}/eml" title="The exact e-mail, with both attachments, as an unsent file">Download .eml</a>
          ${["sent", "follow_up"].includes(item.stage) ? `<button class="btn" data-app-followup="${item.id}">Write follow-up</button>` : ""}
          <button class="btn danger" data-app-delete="${item.id}">Delete</button>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${scheduled ? `<button class="btn danger" data-app-cancel="${item.id}">Cancel sending</button>` : ""}
          ${alreadySent ? "" : `<button class="btn" data-app-sent="${item.id}">I applied myself</button>`}
          ${alreadySent ? "" : `<button class="btn" data-app-sendnow="${item.id}">Send now</button>`}
          ${alreadySent || scheduled ? "" : `<button class="btn primary" data-app-approve="${item.id}">Approve — send in ${esc(mail.delay_hours)} h</button>`}
        </div>
      </div>
    </div>`;
  $("#modal").classList.add("on");
  $("#scrim").classList.add("on");
}
function closeModal() { $("#modal").classList.remove("on"); if (!$("#drawer").classList.contains("on")) $("#scrim").classList.remove("on"); }
async function saveApplication(id, extra = {}, reload = true) {
  await api(`/api/applications/${id}`, { body: { recipient: $("#appTo").value.trim(), subject: $("#appSubject").value, body: $("#appBody").value, letter: $("#appLetter").value, notes: $("#appNotes").value, ...extra } });
  if (reload) await Promise.all([loadApplications(), loadState(), loadOffers()]);
}
async function draft(body, button) {
  const label = button.textContent;
  button.disabled = true;
  button.innerHTML = `<span class="spinner"></span>Writing…`;
  try {
    const result = await api("/api/draft", { body });
    await Promise.all([loadApplications(), loadState(), loadOffers()]);
    closeDrawer();
    if (S.view === "pipeline" || S.view === "today") render();
    openApplication(result.application_id);
  } catch (error) { toast(error.message, true); }
  button.disabled = false;
  button.textContent = label;
}

/* ------------------------------------------------------------------- people */
function filteredPeople() {
  const f = S.people;
  const query = f.q.trim().toLowerCase();
  return S.contacts.filter((person) => {
    if (person.status === "ignored") return false;
    if (f.kind !== "all" && person.kind !== f.kind) return false;
    if (f.tier !== "all" && tierOf(person.country) !== f.tier) return false;
    if (f.email && !person.email) return false;
    if (query && !`${person.name} ${person.org} ${(person.topics || []).join(" ")} ${(person.evidence || []).map((item) => item.text).join(" ")}`.toLowerCase().includes(query)) return false;
    return true;
  });
}
function viewPeople() {
  const f = S.people;
  const counts = S.state.contacts;
  setHeader("People", "Who to write to, and why. Every person is backed by a source: an offer that names them, or their own recent publications. E-mail addresses are only shown when published on a page you can open.",
    `<button class="btn" data-run="contacts" data-force="1">Refresh from OpenAlex</button>`);
  $("#view").innerHTML = `
    <div class="toolbar">
      <input class="input search" id="pq" placeholder="Search name, lab, topic, paper…" value="${esc(f.q)}">
      <div class="chips" id="pKind">${[["all", "Everyone", Object.values(counts).reduce((a, b) => a + b, 0)], ["offer", "Named in offers", counts.offer || 0], ["alumni", "ENP-linked researchers", counts.alumni || 0], ["expert", "Topic experts", counts.expert || 0]]
        .map(([value, label, n]) => `<button class="chip ${f.kind === value ? "on" : ""}" data-kind="${value}">${label}<small>${n}</small></button>`).join("")}</div>
    </div>
    <div class="toolbar">
      <div class="chips" id="pTier">${[["all", "All regions"], ...Object.entries(TIERS)].map(([value, label]) => `<button class="chip ${f.tier === value ? "on" : ""}" data-tier="${value}">${label}</button>`).join("")}</div>
      <button class="chip ${f.email ? "on" : ""}" id="pEmail">With e-mail</button>
    </div>
    <div class="card" id="peopleCard"></div>`;
  renderPeople();
}
function renderPeople() {
  const rows = filteredPeople();
  const shown = rows.slice(0, S.people.limit);
  const kindLabel = { offer: "Named in an offer", alumni: "ENP-linked", expert: "Topic expert" };
  $("#peopleCard").innerHTML = shown.length ? `
    <table><thead><tr><th>Person</th><th>Where</th><th>Why them</th><th>Contact</th><th></th></tr></thead><tbody>
    ${shown.map((person) => {
      const evidence = (person.evidence || []).slice(0, 2);
      return `<tr>
        <td><b>${esc(person.name)}</b><div class="small muted">${esc(person.role || "")}</div>
          <span class="tag ${person.kind === "alumni" ? "new" : person.kind === "offer" ? "info" : ""}">${kindLabel[person.kind] || esc(person.kind)}</span>
          ${person.application_stage ? `<span class="tag info">${esc(person.application_stage.replace("_", " "))}</span>` : ""}</td>
        <td>${esc(person.org || "—")}<div class="small muted">${esc(country(person.country))}</div></td>
        <td style="max-width:360px">${(person.topics || []).slice(0, 3).map((topic) => `<span class="tag">${esc(topic)}</span>`).join(" ")}
          ${evidence.map((item) => `<div class="ev">${item.url ? link(item.url, item.text) : esc(item.text)}${item.year ? ` (${esc(item.year)})` : ""}</div>`).join("")}</td>
        <td>${person.email ? `<a href="mailto:${esc(person.email)}">${esc(person.email)}</a><div class="small faint">${/^https?:/.test(person.email_source || "") ? link(person.email_source, "where it is published") : esc(person.email_source || "")}</div>`
          : `<button class="btn small ghost" data-enrich="${person.id}">Find published e-mail</button>`}
          <div class="small">${person.url ? link(person.url, person.kind === "offer" ? "offer page" : "publication profile") : ""}${person.orcid ? " · " + link("https://orcid.org/" + person.orcid, "ORCID") : ""}</div></td>
        <td style="white-space:nowrap"><button class="btn small" data-draft-contact="${person.id}">Draft e-mail</button>
          <button class="btn small ghost" data-ignore="${person.id}" title="Hide this person">Hide</button></td>
      </tr>`; }).join("")}
    </tbody></table>
    ${rows.length > shown.length ? `<div class="more"><button class="btn" id="morePeople">Show more (${rows.length - shown.length} left)</button></div>` : ""}`
    : `<div class="empty">No contact matches. Run the Contacts agent to collect supervisors and researchers.</div>`;
}

/* ---------------------------------------------------------------- directory */
function viewDirectory() {
  const f = S.dir;
  const query = f.q.trim().toLowerCase();
  const rows = S.directory.filter((entry) => (f.tier === "all" || entry.tier === f.tier) && (f.kind === "all" || entry.kind === f.kind)
    && (!query || `${entry.name} ${entry.focus} ${entry.city} ${country(entry.country)} ${entry.lead || ""}`.toLowerCase().includes(query)));
  const statusText = { ok: "link verified", blocked: "opens in a browser only", dead: "page moved — search the site", unreachable: "site did not answer", unchecked: "not checked yet" };
  setHeader("Directory", "Labs, programmes and employers worth a spontaneous application. Most final-year research internships in France are filled this way, before any posting appears. Links are re-checked every week by the Directory agent.",
    `<button class="btn" data-run="directory" data-force="1">Re-check links</button>`);
  $("#view").innerHTML = `
    <div class="toolbar">
      <input class="input search" id="dq" placeholder="Search lab, topic, city…" value="${esc(f.q)}">
      <div class="chips" id="dTier">${[["all", "All regions"], ...Object.entries(TIERS)].map(([value, label]) => `<button class="chip ${f.tier === value ? "on" : ""}" data-tier="${value}">${label}</button>`).join("")}</div>
      <div class="chips" id="dKind">${[["all", "All"], ["lab", "Labs"], ["programme", "Funded programmes"], ["company", "Employers"], ["admin", "Visa & admin"]].map(([value, label]) => `<button class="chip ${f.kind === value ? "on" : ""}" data-kind="${value}">${label}</button>`).join("")}</div>
    </div>
    <div class="linkgrid">${rows.map((entry) => `
      <div class="card linkcard">
        <div class="row-between"><h3>${link(entry.url, entry.name)}</h3><span class="tag ${entry.tier === "tier1" ? "t1" : entry.tier === "mena" ? "mena" : ""}">${esc(country(entry.country))}</span></div>
        <div class="small muted">${esc(entry.city || "")}${entry.lead ? " · led by " + esc(entry.lead) : ""}</div>
        <div style="margin-top:7px">${esc(entry.focus)}</div>
        ${entry.note ? `<div class="small muted" style="margin-top:6px">${esc(entry.note)}</div>` : ""}
        <div class="small faint" style="margin-top:8px"><span class="status-dot ${esc(entry.link_status)}"></span>${statusText[entry.link_status] || esc(entry.link_status)}</div>
      </div>`).join("") || `<div class="card empty">Nothing matches.</div>`}</div>`;
}

/* ---------------------------------------------------------------- launchpad */
function viewLaunchpad() {
  setHeader("Launchpad", "LinkedIn, Welcome to the Jungle, Indeed, Wuzzuf and Bayt do not allow automated reading, so the Radar does not scrape them. These are pre-filled internship searches: one click each.");
  const groups = {};
  S.launchpad.forEach((board) => (groups[board.group] ||= []).push(board));
  $("#view").innerHTML = Object.entries(groups).map(([group, boards]) => `
    <div class="section-title" style="margin-top:6px">${esc(group)}</div>
    <div class="linkgrid" style="margin-bottom:18px">${boards.map((board) => `
      <div class="card linkcard"><h3>${esc(board.name)}</h3>
        <div class="links">${board.links.map((item) => `<a class="chip" href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.label)}</a>`).join("")}</div>
      </div>`).join("")}</div>`).join("");
}

/* ------------------------------------------------------------------- agents */
const AGENT_INFO = {
  scout: ["Scout", "Reads every live source and keeps postings that are internships in your countries."],
  analyst: ["Analyst", "Scores each offer and asks Gemini for a structured second opinion on the strongest ones."],
  contacts: ["Contacts", "Collects supervisors named in offers, ENP-linked researchers and topic experts, then looks for published e-mails."],
  directory: ["Directory", "Re-checks every curated link so a dead page is flagged instead of trusted."],
  writer: ["Writer", "Prepares review-ready application drafts for new grade-A offers."],
  followup: ["Follow-up", "Moves applications with no reply to “follow-up due” and watches deadlines."],
  reporter: ["Reporter", "Writes the daily digest to data/digests."],
};
function viewAgents() {
  const data = S.agents || { runs: [], sources: [], llm: "" };
  const failed = data.sources.filter((source) => source.last_error);
  const groups = {};
  data.sources.forEach((source) => {
    const group = (source.kind || "|").split("|")[0] || "Other";
    (groups[group] ||= { total: 0, ok: 0, kept: 0 });
    groups[group].total += 1; groups[group].ok += source.last_error ? 0 : 1; groups[group].kept += source.kept || 0;
  });
  setHeader("Agents", "Seven agents run in sequence. They run automatically while this app is open, and every morning once the scheduled task is installed.",
    `<button class="btn" data-run="scout,analyst">Scan sources now</button>`);
  $("#view").innerHTML = `
    <div class="grid two">
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>The team</h2><span class="hint">run in this order</span></div>
          <div class="small muted" style="padding:10px 16px 2px"><b>AI writing:</b> ${esc(data.llm)}</div>
          <table><tbody>${Object.entries(AGENT_INFO).map(([id, [name, text]]) => `
            <tr><td style="width:110px"><b>${name}</b></td><td class="muted">${text}</td>
            <td style="width:70px"><button class="btn small" data-run="${id}" ${id === "contacts" || id === "directory" ? `data-force="1"` : ""}>Run</button></td></tr>`).join("")}</tbody></table>
        </div>
        <div class="card">
          <div class="card-head"><h2>Live log</h2><span class="hint" id="logHint"></span></div>
          <div class="log" id="agentLog"></div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>Sources</h2><span class="hint">${data.sources.length - failed.length}/${data.sources.length} healthy</span></div>
          <table><thead><tr><th>Group</th><th>Healthy</th><th>Offers kept</th></tr></thead><tbody>
            ${Object.entries(groups).map(([group, row]) => `<tr><td>${esc(group)}</td><td>${row.ok}/${row.total}</td><td>${row.kept}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No run yet.</td></tr>`}
          </tbody></table>
          ${failed.length ? `<div style="padding:10px 14px" class="small"><b>Unavailable on the last run</b>${failed.slice(0, 12).map((source) => `<div class="ev">${esc((source.kind || "").split("|")[1] || source.source)} — ${esc(source.last_error)}</div>`).join("")}</div>` : ""}
        </div>
        <div class="card">
          <div class="card-head"><h2>Recent runs</h2></div>
          <table><thead><tr><th>Started</th><th>Trigger</th><th>Result</th></tr></thead><tbody>
          ${data.runs.map((run) => `<tr><td>${esc(ago(run.started_at))}</td><td>${esc(run.trigger || "")}</td>
            <td>${run.status === "running" ? `<span class="spinner"></span>running` : esc(run.status)}${run.summary?.scout ? ` · ${run.summary.scout.kept} offers, ${run.summary.scout.new} new` : ""}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No run yet.</td></tr>`}
          </tbody></table>
        </div>
        <div class="card pad">
          <h3>Run every morning without opening the app</h3>
          <p class="muted small" style="margin:6px 0 8px">Installs a Windows scheduled task that runs the whole pipeline daily at 07:30. Run once in a terminal from the project folder:</p>
          <code>.\\venv\\Scripts\\python.exe radar.py schedule</code>
          <p class="muted small" style="margin:8px 0 0">Remove it with <code>radar.py schedule --remove</code>. While this window is open the agents also refresh every ${esc(S.state.priorities.refresh_hours)} hours.</p>
        </div>
      </div>
    </div>`;
  renderAgentsLog();
}
async function renderAgentsLog() {
  const box = $("#agentLog");
  if (!box) return;
  try {
    const data = await api("/api/run/status");
    box.innerHTML = data.events.map((event) => `<div class="${esc(event.level)}"><span class="faint">${esc(event.ts.slice(11, 19))}</span> <span class="ag">${esc(event.agent)}</span> ${esc(event.message)}</div>`).join("")
      || `<div class="muted">No activity recorded yet.</div>`;
    box.scrollTop = box.scrollHeight;
    $("#logHint").textContent = data.run.running ? data.run.progress : "idle";
  } catch (error) { /* ignore transient errors */ }
}

/* ----------------------------------------------------------------- settings */
function viewSettings() {
  const { profile, priorities, mail } = S.state;
  setHeader("Profile & priorities", "These values drive the ranking and every draft. The writer only states what is listed here — keep it truthful.");
  $("#view").innerHTML = `
    <div class="stack">
      <div class="card pad"><h2>Availability and contact</h2>
        <div class="form-grid" style="margin-top:12px">
          ${[["full_name", "Full name"], ["email", "E-mail"], ["phone", "Phone"], ["availability_start", "Available from (YYYY-MM-DD)"], ["availability_end", "Available until (YYYY-MM-DD)"], ["duration_months", "Duration (months)"], ["linkedin", "LinkedIn"], ["github", "GitHub"], ["portfolio", "Portfolio"]]
            .map(([key, label]) => `<label class="field">${label}<input class="input" data-profile="${key}" value="${esc(profile[key])}"></label>`).join("")}
        </div>
        <label class="field" style="margin-top:12px">Skills (comma separated — used for the skills-overlap score)
          <input class="input" data-profile="skills" value="${esc((profile.skills || []).join(", "))}"></label>
      </div>
      <div class="card pad"><h2>Where to look, and how much each region counts</h2>
        <p class="muted small" style="margin:4px 0 12px">Location is worth up to 20 points of the score. Countries are ISO codes separated by commas.</p>
        <div class="stack" style="gap:10px">${Object.entries(priorities.tiers).map(([name, tier]) => `
          <div class="form-grid" style="grid-template-columns:200px 110px minmax(0,1fr)">
            <label class="field">Region<input class="input" data-tier-label="${name}" value="${esc(tier.label)}"></label>
            <label class="field">Points (0–20)<input class="input" type="number" min="0" max="20" data-tier-weight="${name}" value="${esc(tier.weight)}"></label>
            <label class="field">Countries<input class="input" data-tier-countries="${name}" value="${esc(tier.countries.join(", "))}"></label>
          </div>`).join("")}</div>
      </div>
      <div class="card pad"><h2>Automation</h2>
        <div class="form-grid" style="margin-top:12px">
          ${[["min_score", "Hide offers scoring below"], ["refresh_hours", "Refresh every (hours, 0 = manual)"], ["llm_triage_per_run", "AI reviews per run"], ["auto_draft_per_run", "Drafts prepared per run"], ["follow_up_days", "Follow up after (days)"], ["contact_refresh_days", "Refresh researchers every (days)"]]
            .map(([key, label]) => `<label class="field">${label}<input class="input" type="number" min="0" data-priority="${key}" value="${esc(priorities[key])}"></label>`).join("")}
        </div>
        <label class="field" style="margin-top:12px">Research topics used to find experts (one per line)
          <textarea class="input" rows="6" id="expertTopics">${esc((priorities.expert_topics || []).join("\n"))}</textarea></label>
      </div>
      <div><button class="btn primary" id="saveSettings">Save and re-rank on the next run</button></div>
      <div class="card pad" id="mailCard"><h2>E-mail sending</h2>
        <p class="muted small" style="margin:4px 0 12px">Approved applications are sent from the first account; the second is used if the first refuses. Each needs a Google <b>app password</b> (16 letters) — never your normal password. It is stored encrypted for your Windows user on this computer only.</p>
        ${mail.accounts.map((account, index) => `
          <div class="form-grid" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) 150px;margin-bottom:10px">
            <label class="field">${index === 0 ? "Send from" : "Fallback account"}<input class="input" data-mail-address="${index}" value="${esc(account.address)}"></label>
            <label class="field">App password<input class="input" type="password" autocomplete="new-password" data-mail-password="${index}" placeholder="${account.has_password ? "stored — leave empty to keep" : "xxxx xxxx xxxx xxxx"}"></label>
            <div class="small" style="align-self:end;padding-bottom:9px"><span class="status-dot ${account.has_password ? "ok" : "blocked"}"></span>${account.has_password ? "password stored" : "not set up"}</div>
          </div>`).join("")}
        <div class="form-grid">
          <label class="field">Wait after approval (hours)<input class="input" type="number" min="0" max="72" step="0.5" id="mailDelay" value="${esc(mail.delay_hours)}"></label>
          <label class="field">Maximum e-mails per day<input class="input" type="number" min="1" max="60" id="mailCap" value="${esc(mail.daily_cap)}"></label>
        </div>
        <div style="display:flex;gap:8px;margin-top:12px"><button class="btn primary" id="saveMail">Save e-mail settings</button><button class="btn" id="testMail">Save and test sign-in</button></div>
        <div id="mailResult" class="small" style="margin-top:10px"></div>
      </div>
    </div>`;
}
async function saveMail(test) {
  const accounts = S.state.mail.accounts.map((_, index) => ({
    address: $(`[data-mail-address="${index}"]`).value.trim(), password: $(`[data-mail-password="${index}"]`).value,
  }));
  try {
    await api("/api/mail", { body: { accounts, delay_hours: Number($("#mailDelay").value), daily_cap: Number($("#mailCap").value) } });
    await loadState();
    let results = [];
    if (test) {
      $("#mailResult").innerHTML = `<span class="spinner"></span>Signing in…`;
      results = (await api("/api/mail/test", { body: {} })).results;
    }
    viewSettings();
    $("#mailResult").innerHTML = test
      ? results.map((item) => `<div><span class="status-dot ${item.ok ? "ok" : "dead"}"></span><b>${esc(item.address)}</b> — ${esc(item.message)}</div>`).join("")
      : "Saved.";
    $("#mailCard").scrollIntoView({ block: "nearest" });
  } catch (error) { toast(error.message, true); }
}
async function saveSettings() {
  const profile = {};
  $$("[data-profile]").forEach((input) => { profile[input.dataset.profile] = input.value.trim(); });
  profile.skills = profile.skills.split(",").map((item) => item.trim()).filter(Boolean);
  profile.duration_months = Number(profile.duration_months) || 6;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(profile.availability_start) || !/^\d{4}-\d{2}-\d{2}$/.test(profile.availability_end)) return toast("Dates must look like 2027-01-25.", true);
  const priorities = { tiers: {} };
  Object.keys(S.state.priorities.tiers).forEach((name) => {
    priorities.tiers[name] = {
      label: $(`[data-tier-label="${name}"]`).value.trim(),
      weight: Math.max(0, Math.min(20, Number($(`[data-tier-weight="${name}"]`).value) || 0)),
      countries: $(`[data-tier-countries="${name}"]`).value.split(",").map((item) => item.trim().toUpperCase()).filter((item) => /^[A-Z]{2}$/.test(item)),
    };
  });
  $$("[data-priority]").forEach((input) => { priorities[input.dataset.priority] = Number(input.value) || 0; });
  priorities.expert_topics = $("#expertTopics").value.split("\n").map((item) => item.trim()).filter(Boolean);
  try {
    await api("/api/settings", { body: { profile, priorities } });
    await loadState();
    toast("Saved. Run the Scout to re-rank with the new priorities.");
  } catch (error) { toast(error.message, true); }
}

/* ------------------------------------------------------------------- events */
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, a, [data-offer], [data-app], [data-go], [data-tier]") || event.target;
  const data = target.dataset || {};
  try {
    if (target.closest("#nav") && data.view) return go(data.view);
    if (target.id === "runAll") return runAgents(null);
    if (target.id === "themeToggle") {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("radar-theme", next); } catch (error) { /* storage unavailable */ }
      return;
    }
    if (data.run !== undefined) return runAgents(data.run.split(","), !!data.force);
    if (target.id === "closeDrawer" || event.target.id === "scrim") { closeDrawer(); closeModal(); return; }
    if (data.closeModal !== undefined) return closeModal();
    if (data.setStatus) {
      await api(`/api/offers/${data.id}`, { body: { status: data.setStatus } });
      await Promise.all([loadOffers(), loadState()]);
      toast(`Offer marked ${data.setStatus}.`);
      closeDrawer(); render(); return;
    }
    if (data.saveNotes) { await api(`/api/offers/${data.saveNotes}`, { body: { notes: $("#offerNotes").value } }); return toast("Notes saved."); }
    if (data.draftOffer) return draft({ offer_id: Number(data.draftOffer), language: data.lang }, target);
    if (data.draftContact) return draft({ contact_id: Number(data.draftContact), offer_id: data.forOffer ? Number(data.forOffer) : undefined }, target);
    if (data.enrich) {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Searching…`;
      const result = await api(`/api/contacts/${data.enrich}/enrich`, { body: {} });
      toast(result.found ? `Found ${result.email}` : result.retry ? "The publication index is at its daily limit — try again tomorrow." : "No published e-mail found — check the profile page.", !result.found);
      await loadContacts(); await loadState();
      if (S.view === "people") renderPeople(); else target.textContent = result.found ? result.email : "Not found";
      return;
    }
    if (data.ignore) { await api(`/api/contacts/${data.ignore}`, { body: { status: "ignored" } }); await loadContacts(); await loadState(); return renderPeople(); }
    if (data.appSave) { await saveApplication(data.appSave); toast("Draft saved."); return render(); }
    if (data.appCopy !== undefined) { await navigator.clipboard.writeText(`${$("#appSubject").value}\n\n${$("#appBody").value}`); return toast("Copied to the clipboard."); }
    if (data.appMailto !== undefined) {
      window.location.href = `mailto:${encodeURIComponent($("#appTo").value.trim())}?subject=${encodeURIComponent($("#appSubject").value)}&body=${encodeURIComponent($("#appBody").value)}`;
      return;
    }
    if (data.appSent) { await saveApplication(data.appSent, { stage: "sent" }); toast("Marked as sent — a follow-up reminder is scheduled."); closeModal(); return render(); }
    if (data.appApprove) {
      await saveApplication(data.appApprove, {}, false);
      const result = await api(`/api/applications/${data.appApprove}/approve`, { body: {} });
      await Promise.all([loadApplications(), loadState()]);
      toast(`Approved — it will be sent ${sendCountdown(result.send_at)}.`);
      closeModal(); return render();
    }
    if (data.appSendnow) {
      if (!confirm(`Send this e-mail to ${$("#appTo").value.trim() || "the recipient"} now?`)) return;
      await saveApplication(data.appSendnow, {}, false);
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Sending…`;
      const result = await api(`/api/applications/${data.appSendnow}/send-now`, { body: {} }).finally(async () => { await Promise.all([loadApplications(), loadState(), loadOffers()]); });
      toast(`Sent to ${result.to} from ${result.from}.`);
      closeModal(); return render();
    }
    if (data.appCancel) { await api(`/api/applications/${data.appCancel}`, { body: { stage: "to_review" } }); await Promise.all([loadApplications(), loadState()]); toast("Sending cancelled — the draft is back in review."); closeModal(); return render(); }
    if (target.id === "saveMail" || target.id === "testMail") return saveMail(target.id === "testMail");
    if (data.appDelete) { await api(`/api/applications/${data.appDelete}`, { method: "DELETE", body: {} }); await loadApplications(); await loadState(); closeModal(); return render(); }
    if (data.appFollowup) {
      const result = await api(`/api/applications/${data.appFollowup}/follow-up`);
      $("#appSubject").value = result.subject; $("#appBody").value = result.body;
      return toast("Follow-up written — review it, then save.");
    }
    if (target.id === "exportCsv") return exportCsv();
    if (target.id === "moreOffers") { S.filters.limit += 60; return renderOfferList(); }
    if (target.id === "morePeople") { S.people.limit += 60; return renderPeople(); }
    if (target.id === "saveSettings") return saveSettings();
    if (target.id === "cleanChip") { S.filters.clean = !S.filters.clean; return viewOffers(); }
    if (target.id === "pEmail") { S.people.email = !S.people.email; return viewPeople(); }
    const chipGroup = target.closest("#tierChips, #gradeChips, #statusChips, #pKind, #pTier, #dTier, #dKind");
    if (chipGroup && target.classList.contains("chip")) {
      const store = chipGroup.id.startsWith("p") ? S.people : chipGroup.id.startsWith("d") ? S.dir : S.filters;
      if (data.tier) store.tier = data.tier;
      if (data.grade) store.grade = data.grade;
      if (data.status) store.status = data.status;
      if (data.kind) store.kind = data.kind;
      store.limit = 60;
      return render();
    }
    if (data.go) return go(data.go);
    if (target.classList.contains("region") && data.tier) { S.filters.tier = data.tier; S.filters.status = "open"; return go("offers"); }
    const appNode = event.target.closest("[data-app]");
    if (appNode) return openApplication(appNode.dataset.app);
    const offerNode = event.target.closest("[data-offer]");
    if (offerNode && !event.target.closest("a, button")) return openOffer(offerNode.dataset.offer);
  } catch (error) { toast(error.message, true); }
});
document.addEventListener("input", debounce((event) => {
  const id = event.target.id;
  if (id === "q") { S.filters.q = event.target.value; S.filters.limit = 60; renderOfferList(); }
  if (id === "pq") { S.people.q = event.target.value; S.people.limit = 60; renderPeople(); }
  if (id === "dq") {
    S.dir.q = event.target.value; viewDirectory();
    const input = $("#dq"); input.focus(); input.setSelectionRange(input.value.length, input.value.length);
  }
}));
document.addEventListener("change", (event) => {
  const id = event.target.id;
  if (id === "fCountry") { S.filters.country = event.target.value; renderOfferList(); }
  if (id === "fKind") { S.filters.kind = event.target.value; renderOfferList(); }
  if (id === "fSort") { S.filters.sort = event.target.value; renderOfferList(); }
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeModal(); closeDrawer(); } });
document.addEventListener("dragstart", (event) => {
  const card = event.target.closest?.(".app");
  if (card) event.dataTransfer.setData("text/plain", card.dataset.app);
});
document.addEventListener("dragover", (event) => {
  const column = event.target.closest?.(".col");
  if (column) { event.preventDefault(); $$(".col.over").forEach((node) => node.classList.remove("over")); column.classList.add("over"); }
});
document.addEventListener("drop", async (event) => {
  const column = event.target.closest?.(".col");
  $$(".col.over").forEach((node) => node.classList.remove("over"));
  if (!column) return;
  event.preventDefault();
  const id = event.dataTransfer.getData("text/plain");
  if (!id) return;
  const dropped = S.applications.find((row) => row.id === Number(id));
  // Dropping on "Sent" only records it; it does not send anything. Make that impossible to miss.
  if (column.dataset.stage === "sent" && dropped && !dropped.sent_from
      && !confirm("Moving a card to “Sent” does NOT send an e-mail — it only records that you sent it yourself.\n\nTo have the Radar send it, open the card and choose “Approve” or “Send now”.\n\nRecord it as already sent?")) return;
  try {
    await api(`/api/applications/${id}`, { body: { stage: column.dataset.stage } });
    await Promise.all([loadApplications(), loadState(), loadOffers()]);
    render();
  } catch (error) { toast(error.message, true); }
});

/* --------------------------------------------------------------------- boot */
(async function boot() {
  let theme = null;
  try { theme = localStorage.getItem("radar-theme"); } catch (error) { /* storage unavailable */ }
  const [hashView, hashId, hashTheme] = location.hash.slice(1).split("/");
  document.documentElement.dataset.theme = hashTheme || theme || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  try {
    await refreshAll();
    if (VIEWS.some(([id]) => id === hashView)) await go(hashView, false);
    if (hashView === "offers" && Number(hashId)) openOffer(Number(hashId));
    if (hashView === "pipeline" && Number(hashId)) openApplication(Number(hashId));
  } catch (error) {
    $("#view").innerHTML = `<div class="card empty">Could not reach the local server: ${esc(error.message)}</div>`;
  }
})();
// Approved e-mails are sent in the background: keep the board in step while any is waiting.
setInterval(async () => {
  if (!S.applications.some((item) => item.stage === "scheduled" || item.stage === "sending")) return;
  try {
    await Promise.all([loadApplications(), loadState()]);
    if (["pipeline", "today"].includes(S.view) && !$("#modal").classList.contains("on")) render();
  } catch (error) { /* server restarting */ }
}, 60000);
window.addEventListener("popstate", () => {
  const view = location.hash.slice(1).split("/")[0] || "today";
  if (VIEWS.some(([id]) => id === view) && view !== S.view) go(view, false);
});
