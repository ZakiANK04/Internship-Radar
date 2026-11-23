/* Internship Radar — single-page interface.  All text coming from the web is escaped with esc(). */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (value) => (/^https?:\/\//i.test(String(value || "")) ? esc(value) : "#");
const link = (url, label) => `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;

const S = {
  docOpen: new Set(),
  view: "session", state: null, offers: [], contacts: [], applications: [], directory: [], launchpad: [], agents: null,
  documents: [], sources: [], sourceFilter: "all",
  filters: { q: "", tier: "all", country: "", grade: "AB", eligible: true, status: "open", kind: "all", clean: false, pay: "any", sort: "score", limit: 60 },
  people: { q: "", kind: "all", tier: "all", email: false, starred: false, ready: true, limit: 60 },
  dir: { q: "", tier: "all", kind: "all" },
  poll: null,
};

const DOC_TYPES = {
  cv: "CV", transcript: "Transcript", recommendation: "Recommendation letter", reference: "References",
  cover_letter: "Cover letter", certificate: "Certificate", language_test: "Language test", portfolio: "Portfolio", other: "Other",
};
const docType = (type) => DOC_TYPES[type] || type;
const TIERS = { tier1: "France · Italy · Belgium", tier2: "Germany · Luxembourg", tier3: "Rest of Europe", mena: "Egypt · Gulf" };
const STAGES = [
  ["to_review", "To review"], ["ready", "Needs attention"], ["scheduled", "Approved — sending soon"], ["sent", "Sent"], ["follow_up", "Follow-up due"],
  ["interview", "Interview"], ["offer", "Offer"], ["rejected", "Closed"],
];
const VIEWS = [
  ["session", "Session", "M5 4h14v16H5zM9 9h6M9 13h6M9 17h3"],
  ["outreach", "Outreach", "M4 6h16v12H4zM4 7l8 6 8-6"],
  ["people", "Researchers", "M16 19v-1a4 4 0 0 0-8 0v1M12 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"],
  ["offers", "Offers", "M4 6h16M4 12h16M4 18h10"],
  ["-", "Explore & set up"],
  ["directory", "Lab directory", "M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"],
  ["launchpad", "Job boards", "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"],
  ["agents", "Sources & agents", "M12 3v3M5.6 5.6l2.1 2.1M3 12h3M18 12h3M16.3 7.7l2.1-2.1M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM7 21h10"],
  ["settings", "Settings", "M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4"],
];
const VIEW_ALIAS = { today: "session", pipeline: "outreach" };

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

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/* --------------------------------------------------------------------- data */
async function loadState() { S.state = await api("/api/state"); renderChrome(); }
async function loadOffers() { S.offers = (await api("/api/offers")).offers; }
async function loadContacts() { S.contacts = (await api("/api/contacts")).contacts; }
async function loadApplications() { S.applications = (await api("/api/applications")).applications; S.session = null; }
async function loadDocuments() { S.documents = (await api("/api/documents")).documents; }
async function loadSourcesStatus() { S.sources = (await api("/api/sources")).sources; }

async function refreshAll() {
  await Promise.all([loadState(), loadOffers(), loadApplications(), loadDocuments(), loadSourcesStatus()]);
  render();
}

/* ------------------------------------------------------------------- chrome */
function renderChrome() {
  const state = S.state;
  // once the session is loaded the badge is exactly the number of items listed on that screen
  const counts = { session: S.session ? sessionTotal(S.session) : state.todo, offers: state.offers.total, outreach: state.session_stats.waiting };
  $("#nav").innerHTML = VIEWS.map(([id, label, path]) => (id === "-" ? `<div class="nav-sep">${label}</div>` : `
    <button data-view="${id}" class="${S.view === id ? "on" : ""}">
      <svg viewBox="0 0 24 24"><path d="${path}"/></svg>${label}
      ${counts[id] ? `<span class="count ${id === "session" ? "hot" : ""}">${counts[id]}</span>` : ""}
    </button>`)).join("");
  const run = state.run;
  document.body.classList.toggle("running", !!run.running);
  $("#runbox").innerHTML = run.running
    ? `<b><span class="pulse"></span>${esc(run.agent || "starting")} agent working</b>${esc(run.progress || "")}`
    : `<b>Agents idle</b>Last run ${esc(ago(state.last_run?.finished_at || state.last_run?.started_at))} · ${state.health.ok}/${state.health.total} sources healthy`;
  $("#runAll").disabled = !!run.running;
  $("#runAll").textContent = run.running ? "Scanning…" : "Scan now";
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
        toast("Scan finished — results updated.");
        S.session = null;
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
  view = VIEW_ALIAS[view] || view;
  S.view = view;
  S.sel.clear();
  if (updateHash && location.hash.slice(1).split("/")[0] !== view) history.pushState(null, "", `#${view}`);
  closeDrawer();
  closeModal();
  renderChrome();
  try {
    if (view === "session") await loadSession();
    if (view === "outreach") await loadApplications();
    if (view === "people" && !S.contacts.length) await loadContacts();
    if (view === "directory" && !S.directory.length) S.directory = (await api("/api/directory")).directory;
    if (view === "launchpad" && !S.launchpad.length) S.launchpad = (await api("/api/launchpad")).boards;
    if (view === "agents") S.agents = await api("/api/agents");
  } catch (error) { toast(error.message, true); }
  render();
  window.scrollTo(0, 0);
}
function render() {
  ({ session: viewSession, offers: viewOffers, outreach: viewOutreach, people: viewPeople, directory: viewDirectory,
     launchpad: viewLaunchpad, agents: viewAgents, settings: viewSettings }[S.view] || viewSession)();
  renderBulkBar();
}

function payTag(offer) {
  const info = offer.pay || {};
  if (info.source === "stated") return `<span class="tag pay" title="${esc(info.text || "stated in the posting")}${info.currency && info.currency !== "EUR" ? " — converted to euros, approximate" : ""}">${esc(offer.pay_label)}</span>`;
  if (info.source === "legal_min") return `<span class="tag" title="Pay not stated; French law sets this minimum for internships over 2 months">≥ ${esc(info.monthly_eur)} €/mo</span>`;
  if (info.source === "unpaid") return `<span class="tag bad">unpaid</span>`;
  return "";
}
const monthlyPay = (offer) => (offer.pay && offer.pay.source === "stated" ? offer.pay.monthly_eur || 0 : -1);

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
        ${(offer.places || []).length > 1 ? `<span class="tag" title="${esc(offer.places.join(", "))}">${offer.places.length} locations</span>` : ""}
        ${tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}
        ${deadlineTag(offer.deadline)}
      </div>
    </div>
    <div class="o-side">
      ${payTag(offer)}
      ${flags.length ? `<span class="tag warn" title="${esc(flags.join(" · "))}">${flags.length} check${flags.length > 1 ? "s" : ""}</span>` : ""}
      ${offer.contact_count ? `<span class="tag" title="Named contact available">${offer.contact_count} contact${offer.contact_count > 1 ? "s" : ""}</span>` : ""}
      ${isNew(offer) ? `<span class="tag new">new</span>` : ""}
      ${offer.application_stage === "kit" ? `<span class="tag info">kit ready</span>` : ""}
      ${offer.status !== "new" ? `<span class="tag ${offer.status === "dismissed" || offer.status === "rejected" ? "bad" : "info"}">${esc(offer.status)}</span>` : ""}
      ${!offer.active ? `<span class="tag bad" title="No longer listed by the source">closed</span>` : ""}
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
    if (f.grade === "AB" ? offer.score < 72 : f.grade !== "all" && grade(offer.score) !== f.grade) return false;
    if (f.eligible && offer.ineligible) return false;
    if (f.status === "kit" && offer.application_stage !== "kit") return false;
    if (f.kind !== "all" && offer.source_kind !== f.kind && !(f.kind === "research" && offer.source_kind === "programme")) return false;
    if (f.clean && (offer.flags || []).length) return false;
    if (f.pay === "stated" && monthlyPay(offer) < 0) return false;
    if (f.pay === "1000" && monthlyPay(offer) < 1000) return false;
    if (f.pay === "1500" && monthlyPay(offer) < 1500) return false;
    if (query && !`${offer.title} ${offer.org} ${offer.city} ${country(offer.country)} ${(offer.tags || []).join(" ")}`.toLowerCase().includes(query)) return false;
    return true;
  });
  if (f.sort === "recent") rows = rows.slice().sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || b.score - a.score);
  if (f.sort === "pay") rows = rows.slice().sort((a, b) => monthlyPay(b) - monthlyPay(a) || b.score - a.score);
  if (f.sort === "deadline") rows = rows.slice().sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999") || b.score - a.score);
  return rows;
}
function viewOffers() {
  const f = S.filters;
  const countries = [...new Set(S.offers.map((offer) => offer.country).filter(Boolean))].sort((a, b) => country(a).localeCompare(country(b)));
  const pay = S.state.pay.regions || {};
  const paid = Object.entries(TIERS).filter(([tier]) => pay[tier]).map(([tier, label]) => `${label.split(" · ")[0]} ${pay[tier].median.toLocaleString("fr-FR")} €`).join(" · ");
  setHeader("Offers", `Posted internships, best match first. Grade C/D and offers you are not eligible for are hidden (${S.state.offers.hidden} hidden) — use the chips to show them.${paid ? ` Median stated pay per month: ${paid}.` : ""}`,
    `<button class="btn" id="exportCsv">Export CSV</button>`);
  $("#view").innerHTML = `
    <div class="toolbar">
      <input class="input search" id="q" placeholder="Search title, employer, city, skill…" value="${esc(f.q)}">
      <select class="select" id="fCountry"><option value="">All countries</option>${countries.map((code) => `<option value="${code}" ${f.country === code ? "selected" : ""}>${esc(country(code))}</option>`).join("")}</select>
      <select class="select" id="fKind">
        ${[["all", "All sources"], ["company", "Employers"], ["research", "Research labs & programmes"], ["board", "Job boards"]].map(([value, label]) => `<option value="${value}" ${f.kind === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      <select class="select" id="fSort">
        ${[["score", "Best fit first"], ["recent", "Newest first"], ["deadline", "Deadline first"], ["pay", "Highest pay first"]].map(([value, label]) => `<option value="${value}" ${f.sort === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
    </div>
    <div class="toolbar">
      <div class="chips" id="tierChips">${[["all", "All regions"], ...Object.entries(TIERS)].map(([value, label]) => `<button class="chip ${f.tier === value ? "on" : ""}" data-tier="${value}">${label}</button>`).join("")}</div>
      <div class="chips" id="gradeChips">${[["AB", "Grade A & B"], ["A", "A only"], ["C", "C"], ["all", "All grades"]].map(([value, label]) => `<button class="chip ${f.grade === value ? "on" : ""}" data-grade="${value}">${label}</button>`).join("")}</div>
      <button class="chip ${f.eligible ? "on" : ""}" id="eligibleChip" title="Hide offers with a nationality, visa, language or apprenticeship blocker">Eligible only</button>
      <div class="chips" id="statusChips">${[["open", "Open"], ["new", "New"], ["shortlisted", "Shortlisted"], ["kit", "Kit prepared"], ["applied", "Applied"], ["dismissed", "Dismissed"], ["any", "Everything"]].map(([value, label]) => `<button class="chip ${f.status === value ? "on" : ""}" data-status="${value}">${label}</button>`).join("")}</div>
      <div class="chips" id="payChips">${[["any", "Any pay"], ["stated", "Pay stated"], ["1000", "≥ 1 000 €/mo"], ["1500", "≥ 1 500 €/mo"]].map(([value, label]) => `<button class="chip ${f.pay === value ? "on" : ""}" data-pay="${value}">${label}</button>`).join("")}</div>
      <button class="chip ${f.clean ? "on" : ""}" id="cleanChip" title="Hide offers with eligibility or timing warnings">No warnings</button>
    </div>
    <div class="card"><div class="list" id="offerList"></div></div>`;
  renderOfferList();
}
function renderOfferList() {
  // The same posting in several cities is one line.
  const groups = new Map();
  filteredOffers().forEach((offer) => {
    const key = `${offer.org}|${offer.title}`.toLowerCase();
    if (groups.has(key)) groups.get(key).places.push(offer.city || country(offer.country));
    else groups.set(key, { ...offer, places: [offer.city || country(offer.country)] });
  });
  const rows = [...groups.values()];
  const shown = rows.slice(0, S.filters.limit);
  $("#offerList").innerHTML = `<div class="card-head"><h2>${rows.length} offer${rows.length === 1 ? "" : "s"}</h2><span class="hint">A ≥ 85 · B ≥ 72 · C ≥ 58</span></div>`
    + (shown.map(offerRow).join("") || `<div class="empty">No offer matches these filters.</div>`)
    + (rows.length > shown.length ? `<div class="more"><button class="btn" id="moreOffers">Show ${Math.min(60, rows.length - shown.length)} more</button></div>` : "");
}
function exportCsv() {
  const rows = filteredOffers();
  const cell = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [["score", "title", "employer", "country", "city", "pay (EUR/month)", "pay basis", "deadline", "status", "warnings", "url"].join(",")]
    .concat(rows.map((o) => [o.score, o.title, o.org, country(o.country), o.city, (o.pay || {}).monthly_eur ?? "", ({ stated: "stated", legal_min: "French legal minimum", unpaid: "unpaid" })[(o.pay || {}).source] || "not stated", o.deadline, o.status, (o.flags || []).join(" | "), o.url].map(cell).join(",")));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const anchor = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `internship-radar-${S.state.today}.csv` });
  anchor.click();
  URL.revokeObjectURL(anchor.href);
}

/* ------------------------------------------------------------------- drawer */
function closeDrawer() { $("#drawer").classList.remove("on"); $("#scrim").classList.remove("on"); }
function personCard(person, offerId = null) {
  const papers = (person.evidence || []).filter((item) => (item.type || item.kind) === "paper" || item.text).slice(0, 2);
  const conf = person.confidence !== undefined ? Math.round(person.confidence) : 50;
  return `
  <div class="person">
    <div style="min-width:0">
      <div style="display:flex;align-items:center;gap:6px">
        <button class="star-btn ${person.is_starred ? "starred" : ""}" data-star="${person.id}" title="${person.is_starred ? "Unstar" : "Star"}">★</button>
        <div class="n">${esc(person.name)}</div>
        <span class="badge ${conf >= 80 ? "verified" : "inferred"}" title="Match confidence">${conf}% confidence</span>
        ${person.role_verified ? `<span class="badge verified" title="Role verified from source">verified role</span>` : ""}
        ${person.offer_role ? `<span class="tag info">${esc(person.offer_role)}${person.offer_relevance ? ` (${person.offer_relevance}%)` : ""}</span>` : ""}
      </div>
      <div class="small muted">${esc(person.role || "")}${person.org ? " · " + esc(person.org) : ""}</div>
      ${person.email ? `<div class="small"><a href="mailto:${esc(person.email)}">${esc(person.email)}</a>  <span class="faint">— ${/^https?:/.test(person.email_source || "") ? link(person.email_source, "source") : esc(person.email_source || "")}</span></div>` : ""}
      ${papers.map((p) => `<div class="ev">${p.url ? link(p.url, p.text) : esc(p.text)} ${esc(p.year ? `(${p.year})` : "")}${p.retrieved_at ? ` · <span class="faint">${esc(p.retrieved_at)}</span>` : ""}</div>`).join("")}
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
      <button class="btn small" data-draft-contact="${person.id}" ${offerId ? `data-for-offer="${offerId}"` : ""}>Draft outreach</button>
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
        <button class="btn primary" data-kit="${offer.id}">Application kit</button>
        <a class="btn" href="${safeUrl(offer.url)}" target="_blank" rel="noopener noreferrer">Open posting</a>
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
          <dt>Pay</dt><dd>${esc(offer.pay_label)}${(offer.pay || {}).source === "stated" && offer.pay.text ? ` <span class="faint">— “${esc(offer.pay.text)}”${offer.pay.currency && offer.pay.currency !== "EUR" ? `, converted from ${esc(offer.pay.currency)} (approximate)` : ""}</span>` : ""}${(offer.pay || {}).source === "legal_min" ? ` <span class="faint">— the posting states no amount</span>` : ""}</dd>
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
/* When each scheduled e-mail really leaves: its time, unless the daily limit pushes it to a later day. */
function queueDay(id) {
  const mail = S.state.mail;
  const queue = S.applications.filter((a) => ["scheduled", "sending"].includes(a.stage)).sort((a, b) => (a.send_at || "").localeCompare(b.send_at || "") || a.id - b.id);
  const position = queue.findIndex((a) => a.id === id);
  const room = Math.max(0, mail.daily_cap - mail.sent_today);
  if (position < 0 || position < room) return 0;
  return 1 + Math.floor((position - room) / mail.daily_cap);
}
function sendWhen(item) {
  const day = queueDay(item.id);
  if (!day) return sendCountdown(item.send_at);
  return day === 1 ? "tomorrow (today's limit is reached)" : `in ${day} days (daily limit)`;
}
function sendCountdown(iso) {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 6e4);
  if (minutes <= 0) return "within 15 minutes";
  return minutes < 60 ? `in ${minutes} min` : `in ${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}
async function openApplication(id) {
  const item = S.applications.find((row) => row.id === Number(id));
  if (!item) return;
  if (!S.documents.length) await loadDocuments();
  const recipient = item.recipient || item.contact_email || "";
  const spontaneous = item.kind === "spontaneous";
  const mail = S.state.mail;
  const scheduled = item.stage === "scheduled" || item.stage === "sending";
  const alreadySent = !!item.sent_at;
  if (!item.selected_documents) {
    try { item.selected_documents = (await api(`/api/applications/${item.id}/documents`)).documents; } catch (error) { item.selected_documents = []; }
  }
  const selectedDocIds = new Set((item.selected_documents || []).map((d) => d.id));
  if (!selectedDocIds.size) {
    const def = S.documents.find((d) => d.doc_type === "cv" && d.is_default && (d.language === item.language || d.language === "ALL"));
    if (def) selectedDocIds.add(def.id);
  }
  const banner = scheduled
    ? `<div class="checklist ok"><b>Approved.</b> This e-mail leaves automatically ${esc(sendWhen(item))} from your mailbox. You can still edit it or cancel until then.</div>`
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
        ${mail.ready ? "" : `<div class="checklist">E-mail sending is not set up yet. <a href="#settings" data-go="settings">Connect your mailbox</a> to let the Radar send approved applications.</div>`}
        <label class="field">To<input class="input" id="appTo" value="${esc(recipient)}" placeholder="recipient e-mail — required for automatic sending"></label>
        <label class="field">Subject<input class="input" id="appSubject" value="${esc(item.subject || "")}"></label>
        <label class="field">E-mail message<textarea class="input" id="appBody" rows="9">${esc(item.body || "")}</textarea></label>
        <label class="field">Cover letter — attached as a PDF<textarea class="input" id="appLetter" rows="12">${esc(item.letter || "")}</textarea></label>
        <div class="field" style="margin-top:6px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span style="font-weight:600">Attached Documents</span>
            <span class="small muted">Total attachment limit: 18 MB (Gmail limit)</span>
          </div>
          <div class="doc-checklist" id="appDocsChecklist">
            ${S.documents.map((doc) => `
              <label class="doc-item">
                <input type="checkbox" name="app_doc" value="${doc.id}" ${selectedDocIds.has(doc.id) ? "checked" : ""} data-size="${doc.file_size}">
                <span class="badge ${esc(doc.doc_type)}">${esc(docType(doc.doc_type))}</span>
                <span class="tag">${esc(doc.language)}</span>
                <b>${esc(doc.title)}</b>
                <span class="meta">
                  <span>${esc(doc.original_filename)}</span>
                  <span>(${formatBytes(doc.file_size)})</span>
                  <a href="/api/documents/${doc.id}/file" target="_blank" rel="noopener" class="small">Preview</a>
                </span>
              </label>`).join("") || `<div class="empty">No documents in library. Add your CV under Profile & priorities.</div>`}
          </div>
          <div class="small muted" style="margin-top:4px">Cover letter is generated dynamically. Check transcripts only if explicitly required by the recipient.</div>
        </div>
        <div class="small muted">PDF preview: <a href="/api/applications/${item.id}/letter" target="_blank" rel="noopener">${item.language === "FR" ? "Lettre de motivation" : "Cover letter"} (save first)</a></div>
        <label class="field">Notes<input class="input" id="appNotes" value="${esc(item.notes || "")}" placeholder="e.g. applied through the portal on …"></label>
      </div>
      <div class="modal-foot">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" data-app-save="${item.id}">Save</button>
          <a class="btn" href="/api/applications/${item.id}/eml" title="The exact e-mail, with selected attachments, as an unsent file">Download .eml</a>
          ${["sent", "follow_up"].includes(item.stage) ? `<button class="btn" data-app-followup="${item.id}">Write follow-up</button>` : ""}
          <button class="btn danger" data-app-delete="${item.id}">Delete</button>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${scheduled ? `<button class="btn danger" data-app-cancel="${item.id}">Cancel sending</button>` : ""}
          ${alreadySent ? "" : `<button class="btn" data-app-sent="${item.id}">I applied myself</button>`}
          ${alreadySent ? "" : `<button class="btn" data-app-sendnow="${item.id}">Send now</button>`}
          ${alreadySent || scheduled ? "" : `<button class="btn primary" data-app-approve="${item.id}">${Number(mail.delay_hours) ? `Approve — send in ${esc(mail.delay_hours)} h` : "Approve and send"}</button>`}
        </div>
      </div>
    </div>`;
  $("#modal").classList.add("on");
  $("#scrim").classList.add("on");
}
function closeModal() { $("#modal").classList.remove("on"); if (!$("#drawer").classList.contains("on")) $("#scrim").classList.remove("on"); }
async function saveApplication(id, extra = {}, reload = true) {
  const docCheckboxes = $$('#appDocsChecklist input[name="app_doc"]:checked');
  const docIds = docCheckboxes.map((cb) => Number(cb.value));
  await api(`/api/applications/${id}`, { body: { recipient: $("#appTo").value.trim(), subject: $("#appSubject").value, body: $("#appBody").value, letter: $("#appLetter").value, notes: $("#appNotes").value, ...extra } });
  if (docCheckboxes.length > 0 || docIds.length >= 0) {
    await api(`/api/applications/${id}/documents`, { body: { document_ids: docIds } });
  }
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
    if (f.starred && !person.is_starred) return false;
    if (f.kind !== "all" && person.kind !== f.kind) return false;
    if (f.tier !== "all" && tierOf(person.country) !== f.tier) return false;
    if (typeof placeMatches === "function" && !placeMatches(person)) return false;
    if (f.email && !person.email) return false;
    if (f.ready && !(person.email && person.status === "new" && !person.needs_checking && !person.application_stage)) return false;
    if (query && !`${person.name} ${person.org} ${person.city || ""} ${person.region || ""} ${(person.topics || []).join(" ")} ${(person.evidence || []).map((item) => item.text || item.kind).join(" ")}`.toLowerCase().includes(query)) return false;
    return true;
  });
}
function viewPeople() {
  const f = S.people;
  const counts = S.state.contacts;
  setHeader("Researchers", "Who to write to, and why. Tick people and prepare their e-mails together; each person is backed by a source, and addresses are only shown when published.",
    `<button class="btn primary" data-add-person="one">+ Add a researcher</button>
     <label class="btn" title="Excel list with a name and e-mail column; outreach history columns are kept">Import a researcher list (.xlsx)<input type="file" id="importResearchers" accept=".xlsx" hidden></label>
     <button class="btn" id="findSimilar" title="Look for researchers who publish on the same topics as the people on your list">Find similar researchers</button>
     <button class="btn" data-run="contacts" data-force="1">Refresh from OpenAlex</button>`);
  $("#view").innerHTML = `
    <div class="toolbar">
      <input class="input search" id="pq" placeholder="Search name, lab, topic, paper…" value="${esc(f.q)}">
      <div class="chips" id="pKind">${[["all", "Everyone", Object.values(counts).reduce((a, b) => a + b, 0)], ["manual", "Added by you", counts.manual || 0], ["imported", "From your list", counts.imported || 0], ["similar", "Similar to your list", counts.similar || 0], ["offer", "Named in offers", counts.offer || 0], ["alumni", "ENP-linked researchers", counts.alumni || 0], ["expert", "Topic experts", counts.expert || 0]]
        .map(([value, label, n]) => `<button class="chip ${f.kind === value ? "on" : ""}" data-kind="${value}">${label}<small>${n}</small></button>`).join("")}</div>
    </div>
    <div class="toolbar">
      <div class="chips" id="pTier">${[["all", "All regions"], ...Object.entries(TIERS)].map(([value, label]) => `<button class="chip ${f.tier === value ? "on" : ""}" data-tier="${value}">${label}</button>`).join("")}</div>
      <button class="chip ${f.ready ? "on" : ""}" id="pReady" title="Has a published address, not contacted yet, no address warning">Ready to contact</button>
      <button class="chip ${f.email ? "on" : ""}" id="pEmail">With e-mail</button>
      <button class="chip ${f.starred ? "on" : ""}" id="pStarred">★ Starred</button>
      <button class="btn small select-ready" id="pSelectReady">Select all ready to write to</button>
    </div>
    ${typeof placeBar === "function" ? placeBar() : ""}
    <div class="card" id="peopleCard"></div>`;
  renderPeople();
}
function renderPeople() {
  const rows = filteredPeople();
  const shown = rows.slice(0, S.people.limit);
  const kindLabel = { manual: "Added by you", offer: "Named in offer", alumni: "ENP-linked", expert: "Topic expert", imported: "Your list", similar: "Similar to your list" };
  const statusTag = { contacted: ["info", "already contacted"], replied: ["new", "replied"], declined: ["bad", "declined"] };
  $("#peopleCard").innerHTML = shown.length ? `
    <table><thead><tr><th class="ck"><input type="checkbox" id="pSelAll" title="Select everyone shown on this page" aria-label="Select everyone shown"></th><th>Person</th><th>Where</th><th>Why them</th><th>Contact</th><th>Actions</th></tr></thead><tbody>
    ${shown.map((person) => {
      const evidence = (person.evidence || []).slice(0, person.kind === "imported" && person.notes ? 0 : 1);
      return `<tr>
        <td class="ck"><input type="checkbox" data-selp="${person.id}" ${S.selPeople.has(person.id) ? "checked" : ""} ${person.email ? "" : "disabled"} aria-label="Select"></td>
        <td>
          <div style="display:flex;align-items:center;gap:4px">
            <button class="star-btn ${person.is_starred ? "starred" : ""}" data-star="${person.id}" title="${person.is_starred ? "Unstar" : "Star"}">★</button>
            <b>${esc(person.name)}</b>
            <span class="tag ${person.kind === "alumni" || person.kind === "manual" ? "new" : person.kind === "offer" ? "info" : ""}">${kindLabel[person.kind] || esc(person.kind)}</span>
          </div>
          <div class="small muted clamp1" title="${esc(person.role || "")}">${esc(person.role || "")}</div>
          <div class="ptags">
            ${person.application_stage ? `<span class="tag info">${esc(person.application_stage.replace("_", " "))}</span>` : ""}
            ${statusTag[person.status] ? `<span class="tag ${statusTag[person.status][0]}">${statusTag[person.status][1]}</span>` : ""}
            ${person.needs_checking ? `<span class="tag warn" title="Your note: ${esc(person.needs_checking)}">address to verify</span> <button class="btn small ghost" data-verify="${person.id}">Mark address verified</button>` : ""}
          </div>
        </td>
        <td>${person.org ? esc(person.org) : `<span class="faint">lab not stated</span>`}<div class="small muted">${[person.city, person.city && person.region && person.country === "FR" ? "" : "", country(person.country)].filter(Boolean).map(esc).join(", ")}${person.area === "cote_azur" ? ` <span class="tag new" title="Nice · Côte d'Azur">near Nice</span>` : ""}</div></td>
        <td style="max-width:360px">
          <div class="chips one-line">${(person.topics || []).slice(0, 3).map((topic) => `<span class="tag">${esc(topic)}</span>`).join(" ")}</div>
          ${person.kind === "imported" && person.notes ? `<div class="ev" title="${esc(person.notes)}">${esc(person.notes.split("\n")[0].replace(/^From your list “[^”]*”\. ?/, "")).slice(0, 160)}</div>` : ""}
          ${evidence.map((item) => `<div class="ev">${item.url ? link(item.url, item.text || item.kind) : esc(item.text || item.kind)}${item.year ? ` (${esc(item.year)})` : ""}${item.retrieved_at ? ` · <span class="faint">${esc(item.retrieved_at)}</span>` : ""}</div>`).join("")}
        </td>
        <td>
          ${person.email ? `<div><a href="mailto:${esc(person.email)}">${esc(person.email)}</a></div>`
            : `<button class="btn small ghost" data-enrich="${person.id}">Find published e-mail</button>`}
          <div class="small faint" style="margin-top:2px">${[
            /^https?:/.test(person.email_source || "") ? link(person.email_source, "address source") : (person.email && person.email_source ? esc(person.email_source) : ""),
            person.url ? link(person.url, person.kind === "offer" ? "offer page" : "profile") : "",
            person.orcid ? link("https://orcid.org/" + person.orcid, "ORCID") : ""].filter(Boolean).join(" · ")}</div>
        </td>
        <td style="white-space:nowrap">
          <button class="btn small primary" data-draft-contact="${person.id}" ${statusTag[person.status] ? `data-already="${esc(statusTag[person.status][1])}"` : ""}>Draft outreach</button>
          <button class="btn small ghost" data-ignore="${person.id}" title="Hide this person">Hide</button>
        </td>
      </tr>`; }).join("")}
    </tbody></table>
    ${rows.length > shown.length ? `<div class="more"><button class="btn" id="morePeople">Show more (${rows.length - shown.length} left)</button></div>` : ""}`
    : `<div class="empty">No contact matches. Run the Contacts agent to collect supervisors and researchers.</div>`;
  if (typeof refreshSelectReady === "function") refreshSelectReady();
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
  const allSources = S.sources || [];
  const failed = allSources.filter((source) => source.status === "error");
  const enabledCount = allSources.filter((s) => s.enabled).length;
  const groups = {};
  data.sources.forEach((source) => {
    const group = (source.kind || "|").split("|")[0] || "Other";
    (groups[group] ||= { total: 0, ok: 0, kept: 0 });
    groups[group].total += 1; groups[group].ok += source.last_error ? 0 : 1; groups[group].kept += source.kept || 0;
  });
  const filteredSources = allSources.filter((s) => {
    if (S.sourceFilter === "ok") return s.status === "ok";
    if (S.sourceFilter === "error") return s.status === "error";
    if (S.sourceFilter === "empty") return s.status === "empty";
    if (S.sourceFilter === "disabled") return s.status === "disabled";
    return true;
  });
  setHeader("Agents", "Seven agents run in sequence. They run automatically while this app is open, and every morning once the scheduled task is installed.",
    `<button class="btn" data-run="scout,analyst">Scan sources now</button>`);
  $("#view").innerHTML = `
    <div class="grid two">
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>The team</h2><span class="hint">run in this order</span></div>
          <table><tbody>${Object.entries(AGENT_INFO).map(([id, [name, text]]) => `
            <tr><td style="width:110px"><b>${name}</b></td><td class="muted">${text}</td>
            <td style="width:70px"><button class="btn small" data-run="${id}" ${id === "contacts" || id === "directory" ? `data-force="1"` : ""}>Run</button></td></tr>`).join("")}</tbody></table>
        </div>
        <div class="card">
          <div class="card-head"><h2>Live log</h2><span class="hint" id="logHint"></span></div>
          <div class="log" id="agentLog"></div>
        </div>
        <div class="card pad">
          <h3>Run every morning without opening the app</h3>
          <p class="muted small" style="margin:6px 0 8px">Installs a Windows scheduled task that runs the whole pipeline daily at 07:30. Run once in a terminal from the project folder:</p>
          <code>.\\venv\\Scripts\\python.exe radar.py schedule</code>
          <p class="muted small" style="margin:8px 0 0">Remove it with <code>radar.py schedule --remove</code>. While this window is open the agents also refresh every ${esc(S.state.priorities.refresh_hours)} hours.</p>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="card-head">
            <h2>Source Registry (${enabledCount}/${allSources.length} active)</h2>
            <span class="hint">${allSources.length - failed.length}/${allSources.length} healthy</span>
          </div>
          <div style="padding:8px 12px;border-bottom:1px solid var(--line-soft)">
            <div class="chips" id="sourceStatusChips">
              ${[["all", "All"], ["ok", "Healthy"], ["error", "Error / Unavailable"], ["empty", "0 matches"], ["disabled", "Disabled"]]
                .map(([val, label]) => `<button class="chip ${S.sourceFilter === val ? "on" : ""}" data-source-filter="${val}">${label}</button>`).join("")}
            </div>
          </div>
          <div style="max-height:360px;overflow-y:auto">
            <table><thead><tr><th>Source</th><th>Type</th><th>Kept</th><th>Status</th><th>Toggle</th></tr></thead><tbody>
              ${filteredSources.map((s) => `
                <tr class="${!s.enabled ? "dim" : ""}">
                  <td>
                    <b>${esc(s.label)}</b>
                    <div class="small faint">${esc(s.group_name)}</div>
                    ${s.last_error ? `<div class="small" style="color:var(--red)">${esc(s.error_category || "error")}: ${esc(s.last_error)}</div>` : ""}
                  </td>
                  <td><span class="tag">${esc(s.ats || s.kind)}</span></td>
                  <td><b>${s.kept}</b></td>
                  <td><span class="status-dot ${s.status === "ok" ? "ok" : s.status === "error" ? "dead" : s.status === "disabled" ? "" : "blocked"}"></span>${esc(s.status)}</td>
                  <td style="white-space:nowrap">
                    <button class="btn small ${s.enabled ? "ghost" : "primary"}" data-toggle-source="${esc(s.source_id)}" data-enable="${s.enabled ? "0" : "1"}">
                      ${s.enabled ? "Disable" : "Enable"}
                    </button>
                    ${s.is_custom ? `<button class="btn small danger" data-delete-source="${esc(s.source_id)}">×</button>` : ""}
                  </td>
                </tr>`).join("") || `<tr><td colspan="5" class="empty">No sources match this filter.</td></tr>`}
            </tbody></table>
          </div>
          <div style="padding:12px;border-top:1px solid var(--line-soft);background:var(--panel-2)">
            <h3>Add a source — a lab page, a school, a feed or an employer board</h3>
            <p class="small muted" style="margin:4px 0 0">For a lab or school: paste the address of its internship page (links to offers or PDF subjects), its RSS feed, or its WordPress site. Offers are filtered to internships in your field automatically.</p>
            <div class="form-grid" style="grid-template-columns:130px minmax(0,1fr) minmax(0,1fr) 90px;margin-top:8px">
              <label class="field">Type
                <select class="input select" id="customAtsType">
                  <option value="page">Lab / school internship page</option>
                  <option value="rss">RSS feed</option>
                  <option value="wordpress">WordPress lab site</option>
                  <option value="recruitee">Recruitee board</option>
                  <option value="greenhouse">Greenhouse</option>
                  <option value="lever">Lever</option>
                  <option value="lever_eu">Lever (EU)</option>
                  <option value="ashby">Ashby</option>
                  <option value="smartrecruiters">SmartRecruiters</option>
                  <option value="workday">Workday</option>
                </select>
              </label>
              <label class="field">Address or board ID
                <input class="input" id="customAtsToken" placeholder="https://lab.example.fr/stages  ·  or a board ID such as anthropic"></label>
              <label class="field">Name
                <input class="input" id="customAtsName" placeholder="e.g. SAMOVAR — Télécom SudParis"></label>
              <button class="btn primary" id="addCustomSourceBtn" style="align-self:end">Add board</button>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Recent runs</h2></div>
          <table><thead><tr><th>Started</th><th>Trigger</th><th>Result</th></tr></thead><tbody>
          ${data.runs.map((run) => `<tr><td>${esc(ago(run.started_at))}</td><td>${esc(run.trigger || "")}</td>
            <td>${run.status === "running" ? `<span class="spinner"></span>running` : esc(run.status)}${run.summary?.scout ? ` · ${run.summary.scout.kept} offers, ${run.summary.scout.new} new` : ""}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No run yet.</td></tr>`}
          </tbody></table>
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
  setHeader("Settings", "How the Radar works for you. Everything stays on this computer.");
  const jumps = [["automationCard", "Sessions & notifications"], ["mailCard", "E-mail sending"], ["documentsCard", "Documents"], ["setProfile", "Profile"], ["setRegions", "Regions"], ["setScan", "Scanning"]];
  $("#view").innerHTML = `
    <nav class="stepbar plain">${jumps.map(([id, label]) => `<button data-jump="${id}">${label}</button>`).join("")}</nav>
    <div class="stack settings">
      ${automationCard()}
      ${mailCard()}
      <div class="card pad" id="documentsCard">
        <div class="row-between" style="margin-bottom:10px">
          <h2>Documents you can attach</h2>
          <span class="hint">${(() => {
            const perMail = ["FR", "EN"].map((lang) => S.documents.filter((doc) => doc.is_default && (doc.language === lang || doc.language === "ALL")).reduce((sum, doc) => sum + doc.file_size, 0));
            const heavy = S.documents.filter((doc) => doc.file_size > 1500000 && !doc.has_original && /\.(pdf|jpe?g)$/i.test(doc.original_filename || ""));
            return `Each e-mail carries about <b>${formatBytes(Math.max(...perMail))}</b> of attachments`
              + (heavy.length ? ` · <button class="btn small" id="compressAllDocs">Compress ${heavy.length} heavy file${heavy.length === 1 ? "" : "s"}</button>` : "");
          })()}</span>
        </div>
        <p class="muted small" style="margin:0 0 12px">One default CV per language, plus anything marked “sent by default”, is attached to each new e-mail — you can still untick files per e-mail. Files stay on this computer in <code>data/documents</code>.</p>
        ${documentGroups().map(([type, docs]) => `
          <details class="docgroup" data-docgroup="${type}" ${docGroupOpen(type, docs) ? "open" : ""}>
            <summary><b>${esc(DOC_GROUP_LABEL[type] || docType(type))}</b><span class="count">${docs.length}</span>
              <span class="hint">${docs.filter((doc) => doc.is_default).length ? docs.filter((doc) => doc.is_default).length + " sent by default" : "none sent by default"}</span></summary>
            <table class="docs"><tbody>${docs.map((doc) => `
                <tr>
                  <td>
                    <b>${esc(doc.title)}</b>
                    <div class="small muted">${esc(doc.original_filename)}${doc.notes ? " · " + esc(doc.notes) : ""}</div>
                  </td>
                  <td><span class="tag">${esc(doc.language)}</span></td>
                  <td>${esc(doc.version || "—")}</td>
                  <td class="small ${doc.file_size > 1500000 ? "size-heavy" : "muted"}" title="${doc.file_size > 1500000 ? "Heavy: every e-mail carrying it uses this much of your Google storage" : ""}">${formatBytes(doc.file_size)}${doc.has_original ? `<div class="faint">compressed</div>` : ""}</td>
                  <td>
                    <button class="btn small ${doc.is_default ? "primary" : "ghost"}" data-toggle-doc-default="${doc.id}" data-on="${doc.is_default ? 1 : 0}"
                      title="${doc.doc_type === "cv" ? "The CV attached to applications in this language" : "Attached to every new application in this language (or all languages). You can still untick it per application."}">
                      ${doc.is_default ? (doc.doc_type === "cv" ? `★ Default CV (${esc(doc.language)})` : "✓ Sent by default") : (doc.doc_type === "cv" ? "Use as default CV" : "Send by default")}
                    </button>
                  </td>
                  <td style="white-space:nowrap">
                    <a class="btn small" href="/api/documents/${doc.id}/file" target="_blank" rel="noopener">Preview</a>
                    ${doc.file_size > 1500000 && /\.(pdf|jpe?g)$/i.test(doc.original_filename || "") ? `<button class="btn small" data-compress-doc="${doc.id}" title="Make it lighter; the original is kept">Compress</button>` : ""}
                    ${doc.has_original ? `<button class="btn small ghost" data-restore-doc="${doc.id}" title="Put the original, uncompressed file back">Original</button>` : ""}
                    <button class="btn small danger" data-delete-doc="${doc.id}">Delete</button>
                  </td>
                </tr>`).join("")}</tbody></table>
          </details>`).join("") || `<div class="empty">No documents in the library yet. Add your CV and transcripts below.</div>`}
        <details class="docgroup add" data-docgroup="add" ${S.docOpen.has("add") ? "open" : ""}>
          <summary><b>+ Add a document</b></summary>
          <div class="form-grid" style="margin-top:10px">
            <label class="field">Select file from computer
              <input class="input" type="file" id="newDocFile" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg"></label>
            <label class="field">Or workspace relative path
              <input class="input" id="newDocPath" placeholder="e.g. Aouanouk_CV_Job_FR.pdf"></label>
            <label class="field">Document title
              <input class="input" id="newDocTitle" placeholder="e.g. Job CV (FR)"></label>
            <label class="field">Type
              <select class="input select" id="newDocType">
                <option value="cv">CV / Resume</option>
                <option value="transcript">Transcript (Relevé de notes)</option>
                <option value="recommendation">Recommendation letter</option>
                <option value="reference">References (referees and contacts)</option>
                <option value="language_test">Language test (TCF, EF SET…)</option>
                <option value="certificate">Certificate</option>
                <option value="portfolio">Portfolio</option>
                <option value="cover_letter">Cover Letter template</option>
                <option value="other">Other supporting document</option>
              </select>
            </label>
            <label class="field">Language
              <select class="input select" id="newDocLang">
                <option value="FR">French (FR)</option>
                <option value="EN">English (EN)</option>
                <option value="ALL">All languages (ALL)</option>
                <option value="OTHER">Other</option>
              </select>
            </label>
            <label class="field">Version / Date
              <input class="input" id="newDocVersion" placeholder="e.g. 2026.1"></label>
          </div>
          <div style="display:flex;gap:12px;align-items:center;margin-top:10px;flex-wrap:wrap">
            <label class="field" style="flex:1">Notes
              <input class="input" id="newDocNotes" placeholder="Optional notes (e.g. updated with PFE topics)"></label>
            <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;margin-top:14px">
              <input type="checkbox" id="newDocDefault"> Send with every new application (for a CV: make it the default CV for this language)
            </label>
            <button class="btn primary" id="addDocBtn" style="margin-top:14px">Upload & save</button>
          </div>
        </details>
      </div>
      <div class="card pad" id="setProfile"><h2>Your profile</h2>
        <div class="form-grid" style="margin-top:12px">
          ${[["full_name", "Full name"], ["email", "E-mail"], ["phone", "Phone"], ["availability_start", "Available from (YYYY-MM-DD)"], ["availability_end", "Available until (YYYY-MM-DD)"], ["duration_months", "Duration (months)"], ["linkedin", "LinkedIn"], ["github", "GitHub"], ["portfolio", "Portfolio"]]
            .map(([key, label]) => `<label class="field">${label}<input class="input" data-profile="${key}" value="${esc(profile[key])}"></label>`).join("")}
        </div>
        <label class="field" style="margin-top:12px">Skills (comma separated — used for the skills-overlap score)
          <input class="input" data-profile="skills" value="${esc((profile.skills || []).join(", "))}"></label>
      </div>
      <div class="card pad" id="setRegions"><h2>Where to look, and how much each region counts</h2>
        <p class="muted small" style="margin:4px 0 12px">Location is worth up to 20 points of the score. Countries are ISO codes separated by commas.</p>
        <div class="stack" style="gap:10px">${Object.entries(priorities.tiers).map(([name, tier]) => `
          <div class="form-grid" style="grid-template-columns:200px 110px minmax(0,1fr)">
            <label class="field">Region<input class="input" data-tier-label="${name}" value="${esc(tier.label)}"></label>
            <label class="field">Points (0–20)<input class="input" type="number" min="0" max="20" data-tier-weight="${name}" value="${esc(tier.weight)}"></label>
            <label class="field">Countries<input class="input" data-tier-countries="${name}" value="${esc(tier.countries.join(", "))}"></label>
          </div>`).join("")}</div>
      </div>
      <div class="card pad" id="setScan"><h2>Scanning and ranking</h2>
        <div class="form-grid" style="margin-top:12px">
          ${[["min_score", "Never show offers scoring below"], ["refresh_hours", "Refresh every (hours, 0 = manual)"], ["auto_draft_per_run", "Offer e-mails prepared per scan"], ["follow_up_days", "Follow up after (days)"], ["contact_refresh_days", "Refresh researchers every (days)"]]
            .map(([key, label]) => `<label class="field">${label}<input class="input" type="number" min="0" data-priority="${key}" value="${esc(priorities[key])}"></label>`).join("")}
        </div>
        <label class="field" style="margin-top:12px">Research topics used to find experts (one per line)
          <textarea class="input" rows="6" id="expertTopics">${esc((priorities.expert_topics || []).join("\n"))}</textarea></label>
      </div>
      <div class="savebar"><button class="btn primary" id="saveSettings">Save profile, regions and scanning</button><span class="hint">Offers are re-ranked on the next scan.</span></div>
    </div>`;
}
const DOC_GROUP_ORDER = ["cv", "transcript", "cover_letter", "recommendation", "reference", "language_test", "portfolio", "certificate", "other"];
const DOC_GROUP_LABEL = { cv: "CVs", transcript: "Transcripts", cover_letter: "Cover-letter templates", recommendation: "Recommendation letters", reference: "References",
  language_test: "Language tests", portfolio: "Portfolio", certificate: "Certificates", other: "Other documents" };
function documentGroups() {
  const groups = new Map();
  S.documents.forEach((doc) => { if (!groups.has(doc.doc_type)) groups.set(doc.doc_type, []); groups.get(doc.doc_type).push(doc); });
  const rank = (type) => { const index = DOC_GROUP_ORDER.indexOf(type); return index < 0 ? 99 : index; };
  return [...groups.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
}
function docGroupOpen(type, docs) {
  if (S.docOpen.has(type)) return true;
  if (S.docOpen.has("-" + type)) return false;
  return docs.length <= 6;  // long groups (certificates) start folded
}
document.addEventListener("toggle", (event) => {
  const group = event.target.dataset && event.target.dataset.docgroup;
  if (!group) return;
  S.docOpen.delete(group); S.docOpen.delete("-" + group);
  S.docOpen.add(event.target.open ? group : "-" + group);
}, true);
async function saveMail(test) {
  const accounts = S.state.mail.accounts.map((_, index) => ({
    address: $(`[data-mail-address="${index}"]`).value.trim(), password: $(`[data-mail-password="${index}"]`).value,
  }));
  try {
    await api("/api/mail", { body: { accounts, from_alias: $("#mailAlias").value.trim(), delay_hours: Number($("#mailDelay").value), daily_cap: Number($("#mailCap").value) } });
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
    if (data.draftContact && data.already && !confirm(`According to your records this person has ${data.already === "already contacted" ? "already been contacted" : data.already}. Write to them again?`)) return;
    if (data.draftContact) return draft({ contact_id: Number(data.draftContact), offer_id: data.forOffer ? Number(data.forOffer) : undefined }, target);
    if (data.enrich) {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Searching…`;
      const result = await api(`/api/contacts/${data.enrich}/enrich`, { body: {} });
      toast(result.found ? `Found ${result.email}` : result.retry ? "The publication index is at its daily limit — try again tomorrow." : "No published e-mail found — check the profile page.", !result.found);
      await loadContacts(); await loadState();
      if (S.view === "people") renderPeople(); else target.textContent = result.found ? result.email : "Not found";
      return;
    }
    if (data.verify) {
      const person = S.contacts.find((row) => row.id === Number(data.verify));
      if (!confirm(`Confirm that ${person.email} is ${person.name}'s real address (checked on their page or a paper)?`)) return;
      await api(`/api/contacts/${data.verify}`, { body: { notes: `${person.notes || ""}\n[Address verified by you on ${S.state.today}]` } });
      await loadContacts(); toast("Marked verified — you can now draft and send."); return renderPeople();
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
      const result = await api(`/api/applications/${data.appFollowup}/follow-up`, { body: {} });
      await Promise.all([loadApplications(), loadState()]);
      closeModal();
      toast("Follow-up draft created — review it, then approve to send.");
      return openApplication(result.application_id);
    }
    if (target.id === "exportCsv") return exportCsv();
    if (target.id === "moreOffers") { S.filters.limit += 60; return renderOfferList(); }
    if (target.id === "morePeople") { S.people.limit += 60; return renderPeople(); }
    if (target.id === "saveSettings") return saveSettings();
    if (target.id === "findSimilar") {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Searching…`;
      await api("/api/researchers/similar", { body: {} });
      toast("Looking for similar researchers — results appear under “Similar to your list” in a few minutes.");
      target.disabled = false; target.textContent = "Find similar researchers";
      return startPolling();
    }
    if (target.id === "cleanChip") { S.filters.clean = !S.filters.clean; return viewOffers(); }
    if (target.id === "pEmail") { S.people.email = !S.people.email; return viewPeople(); }
    if (target.id === "pStarred") { S.people.starred = !S.people.starred; return viewPeople(); }
    if (data.star) {
      const person = S.contacts.find((p) => p.id === Number(data.star));
      const nextVal = person ? (person.is_starred ? 0 : 1) : 1;
      await api(`/api/contacts/${data.star}`, { body: { is_starred: nextVal } });
      await loadContacts();
      if (S.view === "people") renderPeople();
      return;
    }
    if (data.toggleDocDefault) {
      const turnOn = data.on !== "1";
      await api(`/api/documents/${data.toggleDocDefault}`, { body: { is_default: turnOn ? 1 : 0 } });
      await loadDocuments();
      viewSettings();
      return toast(turnOn ? "Will be attached to new applications by default." : "No longer attached by default.");
    }
    if (data.compressDoc || target.id === "compressAllDocs") {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Compressing…`;
      const result = await api("/api/documents/compress", { body: data.compressDoc ? { id: Number(data.compressDoc) } : {} });
      S.documents = result.documents; viewSettings(); $("#documentsCard")?.scrollIntoView({ block: "start" });
      return toast(result.saved > 0 ? `${formatBytes(result.saved)} saved — the originals are kept on this computer.` : "Already as light as it gets (a drawn document, not a scan).", result.saved <= 0);
    }
    if (data.restoreDoc) {
      if (!confirm("Put the original, uncompressed file back? It will be attached as it is, at its full size.")) return;
      const result = await api(`/api/documents/${data.restoreDoc}/restore`, { body: {} });
      S.documents = result.documents; viewSettings(); $("#documentsCard")?.scrollIntoView({ block: "start" });
      return toast("Original restored.");
    }
    if (data.deleteDoc) {
      if (!confirm("Delete this document from your library and storage?")) return;
      await api(`/api/documents/${data.deleteDoc}`, { method: "DELETE" });
      await loadDocuments();
      viewSettings();
      return toast("Document deleted.");
    }
    if (target.id === "addDocBtn") {
      const fileInput = $("#newDocFile");
      const pathInput = $("#newDocPath").value.trim();
      const title = $("#newDocTitle").value.trim();
      const doc_type = $("#newDocType").value;
      const language = $("#newDocLang").value;
      const version = $("#newDocVersion").value.trim();
      const notes = $("#newDocNotes").value.trim();
      const is_default = $("#newDocDefault").checked;

      target.disabled = true;
      target.innerHTML = `<span class="spinner"></span>Saving…`;

      const handlePayload = async (payload) => {
        try {
          await api("/api/documents", { body: payload });
          await loadDocuments();
          toast("Document added to library.");
          viewSettings();
        } catch (err) {
          toast(err.message, true);
          target.disabled = false;
          target.textContent = "Upload & save";
        }
      };

      if (fileInput.files && fileInput.files[0]) {
        const file = fileInput.files[0];
        const reader = new FileReader();
        reader.onload = () => {
          const b64 = reader.result.split(",")[1];
          handlePayload({
            content_base64: b64,
            filename: file.name,
            title: title || file.name,
            doc_type,
            language,
            version,
            notes,
            is_default,
          });
        };
        reader.onerror = () => {
          toast("Failed to read file.", true);
          target.disabled = false;
          target.textContent = "Upload & save";
        };
        reader.readAsDataURL(file);
      } else if (pathInput) {
        await handlePayload({
          filepath: pathInput,
          title: title || pathInput,
          doc_type,
          language,
          version,
          notes,
          is_default,
        });
      } else {
        toast("Please select a file or provide a workspace path.", true);
        target.disabled = false;
        target.textContent = "Upload & save";
      }
      return;
    }
    if (data.toggleSource) {
      const enable = data.enable === "1";
      await api(`/api/sources/${encodeURIComponent(data.toggleSource)}/toggle`, { body: { enabled: enable } });
      await loadSourcesStatus();
      toast(enable ? "Source enabled." : "Source disabled.");
      viewAgents();
      return;
    }
    if (data.deleteSource) {
      if (!confirm(`Delete custom source ${data.deleteSource}?`)) return;
      await api(`/api/sources/${encodeURIComponent(data.deleteSource)}`, { method: "DELETE" });
      await loadSourcesStatus();
      toast("Source deleted.");
      viewAgents();
      return;
    }
    if (data.sourceFilter) {
      S.sourceFilter = data.sourceFilter;
      viewAgents();
      return;
    }
    if (target.id === "addCustomSourceBtn") {
      const ats = $("#customAtsType").value;
      const token = $("#customAtsToken").value.trim();
      const name = $("#customAtsName").value.trim() || token;
      if (!token) return toast("An address or board ID is required.", true);
      target.disabled = true;
      target.innerHTML = `<span class="spinner"></span>Adding…`;
      try {
        await api("/api/sources/add", { body: { ats, token, name } });
        await loadSourcesStatus();
        toast(`Added ${name} (${ats}).`);
        viewAgents();
      } catch (err) {
        toast(err.message, true);
        target.disabled = false;
        target.textContent = "Add board";
      }
      return;
    }
    const chipGroup = target.closest("#tierChips, #gradeChips, #statusChips, #payChips, #pKind, #pTier, #dTier, #dKind");
    if (chipGroup && target.classList.contains("chip")) {
      const store = chipGroup.id.startsWith("p") ? S.people : chipGroup.id.startsWith("d") ? S.dir : S.filters;
      if (data.tier) store.tier = data.tier;
      if (data.grade) store.grade = data.grade;
      if (data.status) store.status = data.status;
      if (data.kind) store.kind = data.kind;
      if (data.pay) store.pay = data.pay;
      store.limit = 60;
      return render();
    }
    if (data.go) return go(data.go);
    if (target.classList.contains("region") && data.tier) { S.filters.tier = data.tier; S.filters.status = "open"; S.filters.pay = data.payFilter || "any"; return go("offers"); }
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
document.addEventListener("change", async (event) => {
  const id = event.target.id;
  if (id === "importResearchers" && event.target.files[0]) {
    const file = event.target.files[0];
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    try {
      const result = await api("/api/researchers/import", { body: { filename: file.name, data } });
      toast(`Imported ${result.created} new and refreshed ${result.updated} researcher(s). Looking for similar ones in the background.`);
      await Promise.all([loadContacts(), loadState()]);
      S.people.kind = "imported";
      viewPeople();
      startPolling();
    } catch (error) { toast(error.message, true); }
    event.target.value = "";
    return;
  }
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
    await go(VIEWS.some(([id]) => id === (VIEW_ALIAS[hashView] || hashView)) ? hashView : "session", false);
    if (hashView === "offers" && Number(hashId)) openOffer(Number(hashId));
    if (["pipeline", "outreach"].includes(hashView) && Number(hashId)) openApplication(Number(hashId));
    if (hashView === "kit" && Number(hashId)) openKit(Number(hashId));
  } catch (error) {
    $("#view").innerHTML = `<div class="card empty">Could not reach the local server: ${esc(error.message)}</div>`;
  }
})();
// Approved e-mails are sent in the background: keep the board in step while any is waiting.
setInterval(async () => {
  if (!S.applications.some((item) => item.stage === "scheduled" || item.stage === "sending")) return;
  try {
    await Promise.all([loadApplications(), loadState()]);
    if (["outreach", "session"].includes(S.view) && !$("#modal").classList.contains("on")) render();
  } catch (error) { /* server restarting */ }
}, 60000);
window.addEventListener("popstate", () => {
  const view = VIEW_ALIAS[location.hash.slice(1).split("/")[0]] || location.hash.slice(1).split("/")[0] || "session";
  if (VIEWS.some(([id]) => id === view) && view !== S.view) go(view, false);
});
