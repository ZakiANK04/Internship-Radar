/* Review session, outreach table, batch approval and application kits.  Loaded after app.js (shares its helpers). */
"use strict";

S.session = null;
S.sel = new Set();          // selected e-mails (application ids)
S.selPeople = new Set();    // selected researchers (contact ids)
S.outreach = { tab: "drafts", q: "" };
S.noesOpen = false;         // the folded "looks like a no" replies
document.addEventListener("toggle", (event) => {
  if (event.target.dataset && event.target.dataset.fold === "noes") S.noesOpen = event.target.open;
}, true);

const TONES = {
  positive: ["new", "Looks positive"], unfunded: ["warn", "Would host — no funding"], redirect: ["info", "Asks you to apply another way"],
  declined: ["bad", "Looks like a no"], neutral: ["", "To read"],
};
const OUTREACH_TABS = [
  ["drafts", "To approve", (a) => ["to_review", "ready"].includes(a.stage)],
  ["scheduled", "Scheduled", (a) => ["scheduled", "sending"].includes(a.stage)],
  ["waiting", "Waiting for a reply", (a) => a.stage === "sent"],
  ["follow_up", "Follow-up due", (a) => a.stage === "follow_up"],
  ["replied", "Replied", (a) => a.stage === "replied"],
  ["interview", "Interview / offer", (a) => ["interview", "offer"].includes(a.stage)],
  ["closed", "Closed", (a) => ["rejected", "closed", "bounced"].includes(a.stage)],
];
const isOutreach = (a) => a.stage !== "kit" && !(a.kind === "offer" && a.sent_from === "employer website");
const gmailSearch = (address) => `https://mail.google.com/mail/#search/${encodeURIComponent("from:" + address)}`;
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
/* What is shown of a reply: no separator lines, no quoted "On … wrote" tail, no attachment placeholders. */
const cleanReply = (text) => (text || "").replace(/￼/g, "").replace(/[-_=*]{4,}/g, " ")
  .replace(/\s(?:On|Le|Il giorno|Am)\s[\s\S]{4,110}?\b20\d\d\b[\s\S]{0,40}?\d{1,2}\s?[:h]\s?\d{2}[\s\S]*$/, "").replace(/\n{2,}/g, "\n").trim();
/* "votre équipe au laboratoire DAVID, UVSQ" is a phrase written for the letter; a list shows the lab itself. */
const labName = (org) => (org || "")
  .replace(/^(?:votre équipe|your team|your group)\s+(?:au sein (?:du\s+|de la\s+|de l[’'])|au laboratoire\s+|au\s+|à l[’']\s*|à la\s+|à\s+|du\s+|de la\s+|de l[’']\s*|de\s+|at\s+(?:the\s+)?)/i, "")
  .replace(/^the\s+/i, "");
const sessionTotal = (s) => s.replies.length + s.bounced.length + s.failed.length + s.follow_ups.length + s.drafts.length + s.to_verify.length;
function replyCard(item) {
  const tone = item.reply_tone || "neutral";
  const [cls, label] = TONES[tone] || TONES.neutral;
  const no = tone === "declined";
  return `<article class="reply tone-${esc(tone)}" data-expand>
    <div class="reply-head"><b>${esc(item.contact_name || item.reply_from)}</b><span class="muted small reply-org">${esc(labName(item.contact_org))}</span>
      <span class="tag ${cls}">${label}</span><span class="faint small">${esc(item.replied_at || "")}</span>
      <div class="reply-actions">
        <a class="btn small ${no ? "ghost" : "primary"}" href="${gmailSearch(item.reply_from || item.recipient)}" target="_blank" rel="noopener noreferrer">${no ? "Open in Gmail" : "Answer in Gmail"}</a>
        <span class="seg" role="group" aria-label="File this reply">
          <button data-outcome="interview" data-id="${item.id}" title="Move to Interview / positive lead">Positive</button><button data-outcome="declined" data-id="${item.id}" title="They said no">Declined</button><button data-outcome="closed" data-id="${item.id}" title="Nothing more to do">Done</button>
        </span>
      </div></div>
    <p class="reply-text" title="Click to read the whole excerpt">${esc(cleanReply(item.reply_snippet) || "(no text could be read — open it in Gmail)")}</p>
  </article>`;
}

async function loadSession() { S.session = await api("/api/session"); if (S.state) renderChrome(); }
async function reloadWork() {
  await Promise.all([loadSession(), loadApplications(), loadState()]);
  S.sel = new Set([...S.sel].filter((id) => S.applications.some((a) => a.id === id && !a.sent_at)));
  render();
}

/* ------------------------------------------------------------- shared rows */
function mailTable(items, { empty = "Nothing here.", detail = null } = {}) {
  if (!items.length) return `<div class="step-empty">${empty}</div>`;
  return `<table class="mail"><tbody>${items.map((item) => {
    const who = item.contact_name || item.offer_org || item.recipient || "—";
    const where = [labName(item.contact_org) || item.offer_title, item.contact_country ? country(item.contact_country) : ""].filter(Boolean).join(" · ");
    const info = detail ? detail(item) : esc(item.why || item.subject || "");
    return `<tr data-open-app="${item.id}">
      <td class="ck"><input type="checkbox" data-sel="${item.id}" ${S.sel.has(item.id) ? "checked" : ""} aria-label="Select"></td>
      <td class="who"><b>${esc(who)}</b><div class="small muted">${esc(where)}</div></td>
      <td class="lang"><span class="tag">${esc(item.language || "")}</span></td>
      <td class="why small muted">${info}</td>
      <td class="att small faint">${(item.attachments || []).length ? esc(item.attachments.join(" · ")) : esc(item.recipient || "")}</td>
    </tr>`; }).join("")}</tbody></table>`;
}
const TONE_ORDER = ["positive", "unfunded", "redirect", "neutral", "declined"];
function step(number, title, count, body, { hint = "", actions = "", done = "Nothing to do." } = {}) {
  return `<section class="step ${count ? "" : "done"}" id="step${number}">
    <header class="step-head"><span class="step-n">${count ? number : "✓"}</span><h2>${title}</h2>
      ${count ? `<span class="badge-n">${count}</span>` : ""}<span class="hint">${hint}</span><div class="step-actions">${count ? actions : ""}</div></header>
    ${count ? body : `<div class="step-empty">${done}</div>`}
  </section>`;
}
function renderBulkBar() {
  let bar = $("#bulkbar");
  if (!bar) { bar = document.createElement("div"); bar.id = "bulkbar"; document.body.appendChild(bar); }
  const people = S.view === "people" && S.selPeople.size;
  const mails = ["session", "outreach"].includes(S.view) && S.sel.size;
  if (!people && !mails) { bar.className = ""; bar.innerHTML = ""; return; }
  bar.className = "on";
  if (people) {
    const mail = S.state.mail;
    const days = Math.max(1, Math.ceil(S.selPeople.size / mail.daily_cap));
    bar.innerHTML = `<b>${plural(S.selPeople.size, "researcher")} selected</b>
      <span class="small muted">${mail.ready ? `${mail.daily_cap} a day · ${days > 1 ? `about ${plural(days, "day")}` : "all within a day"}, ${Number(mail.delay_hours) ? `first in ${mail.delay_hours} h` : "first right away"}` : "no mailbox connected yet"}</span>
      <button class="btn primary" data-send-people ${mail.ready ? "" : `disabled title="Connect a mailbox in Settings › E-mail sending first"`}>Write &amp; send to all ${S.selPeople.size}</button>
      <button class="btn" data-bulk-people="prepare" title="Write the e-mails and keep them in Session for you to read before approving">Write only — review first</button>
      <button class="btn ghost" data-bulk-clear>Clear</button>`;
    return;
  }
  const selected = S.applications.filter((a) => S.sel.has(a.id));
  const drafts = selected.every((a) => ["to_review", "ready"].includes(a.stage));
  const scheduled = selected.every((a) => a.stage === "scheduled");
  const sent = selected.every((a) => !!a.sent_at);
  const capacity = S.session?.capacity || { daily_cap: S.state.mail.daily_cap, sent_today: S.state.mail.sent_today, queued: 0 };
  const days = Math.max(1, Math.ceil((capacity.queued + S.sel.size - Math.max(0, capacity.daily_cap - capacity.sent_today)) / capacity.daily_cap) + 1);
  bar.innerHTML = `<b>${plural(S.sel.size, "e-mail")} selected</b>
    ${drafts ? `<span class="small muted">sent ${capacity.daily_cap}/day · about ${plural(days, "day")}</span>
      <button class="btn primary" data-bulk="approve">Approve &amp; schedule</button><button class="btn danger" data-bulk="delete">Delete</button>` : ""}
    ${scheduled ? `<button class="btn" data-bulk="unapprove">Cancel sending</button>` : ""}
    ${sent ? `<button class="btn" data-bulk="interview">Move to interview</button><button class="btn" data-bulk="declined">Mark declined</button><button class="btn" data-bulk="closed">Close</button>` : ""}
    <button class="btn ghost" data-bulk-clear>Clear</button>`;
}

/* A researcher the Radar can write to now: published address, not contacted, no warning in your notes. */
const readyToSend = (person) => !!person.email && person.status === "new" && !person.needs_checking
  && (!person.application_stage || ["to_review", "ready"].includes(person.application_stage));
function refreshSelectReady() {
  const button = $("#pSelectReady");
  if (!button) return;
  const ready = filteredPeople().filter(readyToSend);
  const allOn = ready.length && ready.every((person) => S.selPeople.has(person.id));
  button.textContent = allOn ? `Unselect all ${ready.length}` : `Select all ${ready.length} ready to write to`;
  button.disabled = !ready.length;
  const head = $("#pSelAll");
  if (head) {
    const boxes = $$("#peopleCard input[data-selp]:not(:disabled)");
    head.checked = boxes.length > 0 && boxes.every((box) => box.checked);
  }
}

/* ---------------------------------------------------------------- session */
function viewSession() {
  const s = S.session;
  if (!s) {
    setHeader("Session", "Gathering what needs you…");
    $("#view").innerHTML = `<div class="card empty"><span class="spinner"></span>Loading</div>`;
    loadSession().then(() => S.view === "session" && viewSession()).catch((error) => toast(error.message, true));
    return;
  }
  const { stats, capacity } = s;
  s.replies.sort((a, b) => TONE_ORDER.indexOf(a.reply_tone || "neutral") - TONE_ORDER.indexOf(b.reply_tone || "neutral") || (b.replied_at || "").localeCompare(a.replied_at || ""));
  const noes = s.replies.filter((item) => item.reply_tone === "declined");
  const worth = s.replies.filter((item) => item.reply_tone !== "declined");
  const jump = [["Replies", s.replies.length], ["Problems", s.bounced.length + s.failed.length], ["Follow-ups", s.follow_ups.length],
    ["To approve", s.drafts.length], ["To verify", s.to_verify.length], ["Offers", s.kits.length]];
  const room = Math.max(0, capacity.daily_cap - capacity.sent_today - capacity.queued);
  const total = sessionTotal(s);
  setHeader("Session", total
    ? `${plural(total, "item")} to go through, top to bottom. Tick e-mails and approve them together; nothing is sent without your tick.`
    : "You're up to date. The Radar keeps scanning and will e-mail you when a reply arrives.",
    `<span class="small muted">${s.inbox.enabled ? `Mailbox checked ${esc(ago(s.inbox.checked_at))}` : "Mailbox reading is off"}</span>
     <button class="btn" id="checkInbox">Check for replies</button>`);
  const kpi = (value, label, cls = "") => `<div class="kpi2"><div class="v ${cls}">${value}</div><div class="l">${label}</div></div>`;
  $("#view").innerHTML = `
    <div class="kpis2">
      ${kpi(stats.sent, "e-mails sent")}${kpi(`${stats.replied} <small>${stats.reply_rate}%</small>`, "replies")}
      ${kpi(stats.positive + stats.interviews, "positive leads", "accent")}${kpi(stats.waiting, "waiting for a reply")}
      ${kpi(capacity.queued, "scheduled to send")}${kpi(stats.web_applications, "applied on websites")}
    </div>
    <nav class="stepbar">${jump.map(([label, count], index) => `
      <button class="${count ? "" : "zero"}" data-jump="step${index + 1}"><span class="step-n">${count ? index + 1 : "✓"}</span>${label}${count ? `<b>${count}</b>` : ""}</button>`).join("")}</nav>
    ${s.inbox.error ? `<div class="checklist bad" style="margin-bottom:12px">The mailbox could not be read: ${esc(s.inbox.error)}</div>` : ""}

    ${step(1, "Replies", s.replies.length, `<div class="replies">
      ${worth.map(replyCard).join("")}
      ${noes.length ? `<details class="noes" data-fold="noes" ${S.noesOpen || !worth.length ? "open" : ""}>
        <summary><b>${plural(noes.length, "reply", "replies")} that ${noes.length === 1 ? "looks" : "look"} like a no</b>
          <span class="hint">open to check them — the tone is only a guess from the wording</span>
          <button class="btn small" data-decline-all="${noes.map((item) => item.id).join(",")}">File ${noes.length === 1 ? "it" : "all " + noes.length} as declined</button></summary>
        ${noes.map(replyCard).join("")}</details>` : ""}</div>`,
      { hint: worth.length ? `${plural(worth.length, "reply", "replies")} worth answering first — answer from Gmail, then file each one` : "nothing to answer — file them when you have read them",
        done: "No new reply. Replies are detected automatically and you get an e-mail when one arrives." })}

    ${step(2, "Problems to fix", s.bounced.length + s.failed.length, `<div class="replies">
      ${s.bounced.map((item) => `<article class="reply"><div class="reply-head"><b>${esc(item.contact_name || item.recipient)}</b>
          <span class="tag bad">address bounced</span><span class="muted small">${esc(item.recipient)}</span></div>
        <div class="reply-actions">${item.contact_url ? link(item.contact_url, "Open their profile") : ""}
          <button class="btn small ghost" data-outcome="closed" data-id="${item.id}">Dismiss</button></div></article>`).join("")}
      ${s.failed.map((item) => `<article class="reply" data-open-app="${item.id}"><div class="reply-head"><b>${esc(item.contact_name || item.offer_title || item.recipient)}</b>
          <span class="tag bad">could not be sent</span><span class="small muted">${esc(item.recipient || "")}</span>
          <div class="reply-actions"><button class="btn small" data-open-app="${item.id}">Open to send again</button></div></div>
        <p class="reply-text">${esc(/not connected|getaddrinfo|timed out|10060/i.test(item.send_error || "") ? "The connection to Gmail dropped while sending — nothing was sent. Open it and approve again." : item.send_error)}</p></article>`).join("")}</div>`,
      { done: "No bounced address and no failed send." })}

    ${step(3, "Follow-ups to approve", s.follow_ups.length, mailTable(s.follow_ups, { detail: (item) => esc((item.notes || "").replace(/^Follow-up to application #\d+ /, "")) }),
      { hint: "people who have not answered after your follow-up delay",
        actions: `<button class="btn small" data-select="follow_ups">Select all</button>`,
        done: "No follow-up is due yet." })}

    ${step(4, "New e-mails to approve", s.drafts.length, mailTable(s.drafts),
      { hint: `ready with CV, transcript and cover letter in the right language · ${room} can still go today`,
        actions: `<button class="btn small" data-select="drafts">Select all</button>
          ${room && s.drafts.length > room ? `<button class="btn small" data-select="drafts" data-count="${room}">Select today's ${room}</button>` : ""}
          ${s.ready_targets ? `<button class="btn small" data-prepare="25">Prepare ${Math.min(25, s.ready_targets)} more</button>` : ""}
          <button class="btn small" data-add-person="one">+ Add someone</button>`,
        done: s.ready_targets ? `No draft waiting. <button class="btn small" data-prepare="25">Prepare e-mails for ${Math.min(25, s.ready_targets)} researchers</button>`
          : "No draft waiting, and every researcher with a known address has been handled. Find more under Researchers." })}

    ${step(5, "Addresses to verify", s.to_verify.length, `<table class="mail"><tbody>${s.to_verify.map((person) => `
      <tr><td class="who"><b>${esc(person.name)}</b><div class="small muted">${esc(person.org || "")}</div></td>
        <td class="small">${esc(person.email)}</td><td class="why small muted">${esc(person.note || person.flag)}</td>
        <td class="att">${person.url ? link(person.url, "profile") : ""}
          <button class="btn small" data-verify-contact="${person.id}" data-name="${esc(person.name)}" data-email="${esc(person.email)}">Address is right</button></td></tr>`).join("")}</tbody></table>`,
      { hint: "your notes say these addresses were guessed — they are held back until you confirm", done: "No address is waiting for confirmation." })}

    ${step(6, "Best offers to apply to on the employer's site", s.kits.length, `<div class="list">${s.kits.map((offer) => `
      <div class="offer" data-offer="${offer.id}">${scoreRing(offer.score)}
        <div style="min-width:0"><div class="o-title">${esc(offer.title)}</div>
          <div class="o-meta"><span>${esc(offer.org || "—")}</span><span class="sep">•</span><span>${esc(offer.city ? offer.city + ", " : "")}${esc(country(offer.country))}</span>${deadlineTag(offer.deadline)}</div></div>
        <div class="o-side">${offer.pay && offer.pay.source === "stated" ? `<span class="tag pay">${esc(offer.pay.monthly_eur)} €/mo</span>` : ""}
          <button class="btn small primary" data-kit="${offer.id}">Application kit</button></div></div>`).join("")}</div>`,
      { hint: "grade A and B, no eligibility blocker", actions: `<button class="btn small" data-go="offers">All offers</button>`, done: "No open offer matches right now." })}`;
  renderBulkBar();
}

/* --------------------------------------------------------------- outreach */
function viewOutreach() {
  const mails = S.applications.filter(isOutreach);
  const query = S.outreach.q.trim().toLowerCase();
  const tab = OUTREACH_TABS.find(([id]) => id === S.outreach.tab) || OUTREACH_TABS[0];
  const rows = mails.filter(tab[2]).filter((a) => !query || `${a.contact_name} ${a.contact_org} ${a.offer_title} ${a.offer_org} ${a.recipient} ${a.subject}`.toLowerCase().includes(query));
  const cap = S.state.mail;
  const queued = mails.filter((a) => ["scheduled", "sending"].includes(a.stage)).length;
  const held = Number(cap.delay_hours) > 0 && mails.some((a) => a.stage === "scheduled" && new Date(a.send_at) > Date.now());
  setHeader("Outreach", `Every e-mail, by stage. ${cap.ready ? `${cap.sent_today} of ${cap.daily_cap} sent today${cap.sent_today >= cap.daily_cap && queued ? " — today's limit is reached, the queue continues tomorrow" : ""}. ${Number(cap.delay_hours) ? `${cap.delay_hours} h wait after approval.` : "Sent right after approval."}` : "E-mail sending is not set up — see Settings."}`,
    `${held ? `<button class="btn" id="sendSooner" title="Remove the waiting time of the ${queued} scheduled e-mails">Send scheduled e-mails now</button>` : ""}
     <button class="btn" id="raiseCap" title="Change how many e-mails may leave per day">Daily limit: ${cap.daily_cap}</button>
     <button class="btn" data-prepare="25">Prepare more e-mails</button>`);
  const detail = (a) => {
    if (a.stage === "scheduled" || a.stage === "sending") { const later = queueDay(a.id); return `<span class="tag ${later ? "warn" : "new"}">sends ${esc(sendWhen(a))}</span>`; }
    if (a.stage === "replied") { const [cls, label] = TONES[a.reply_tone] || TONES.neutral; return `<span class="tag ${cls}">${label}</span> ${esc((a.reply_snippet || "").slice(0, 110))}`; }
    if (a.stage === "bounced") return `<span class="tag bad">address bounced</span>`;
    if (a.send_error) return `<span class="tag bad" title="${esc(a.send_error)}">sending failed</span>`;
    if (a.sent_at) return `sent ${esc(a.sent_at)}${a.sent_from ? " · " + esc(a.sent_from) : ""}${a.stage === "sent" && a.follow_up_at ? " · follow-up " + esc(a.follow_up_at) : ""}`;
    return esc(a.subject || "");
  };
  $("#view").innerHTML = `
    <div class="tabs" id="outreachTabs">${OUTREACH_TABS.map(([id, label, test]) => `
      <button class="tab ${tab[0] === id ? "on" : ""}" data-otab="${id}">${label}<small>${mails.filter(test).length}</small></button>`).join("")}</div>
    <div class="toolbar"><input class="input search" id="oq" placeholder="Search a person, a lab, an address…" value="${esc(S.outreach.q)}">
      <button class="btn small" data-select="outreach">Select all ${rows.length}</button></div>
    <div class="card">${mailTable(rows.slice(0, 300), { empty: "No e-mail at this stage.", detail })}</div>`;
  renderBulkBar();
}

/* -------------------------------------------------------------------- kit */
async function openKit(offerId, language) {
  let kit;
  try { kit = await api(`/api/offers/${offerId}/kit`, { body: language ? { language } : {} }); } catch (error) { return toast(error.message, true); }
  closeDrawer();
  const other = kit.language === "FR" ? "EN" : "FR";
  $("#modal").innerHTML = `
    <div class="modal-card">
      <div class="modal-head"><div><h2>Application kit — ${esc(kit.offer.title)}</h2>
        <div class="small muted">${esc(kit.offer.org || "")}${kit.offer.deadline ? " · apply by " + esc(kit.offer.deadline) : ""}${kit.applied_at ? " · applied on " + esc(kit.applied_at) : ""}</div></div>
        <button class="btn ghost small" data-close-modal>Close</button></div>
      <div class="modal-body kit">
        <div class="kit-step"><span class="step-n">1</span><div><b>Open the posting</b> and start the employer's form.
          <div style="margin-top:8px"><a class="btn primary" href="${safeUrl(kit.offer.url)}" target="_blank" rel="noopener noreferrer">Open posting</a></div></div></div>
        <div class="kit-step"><span class="step-n">2</span><div style="flex:1;min-width:0"><b>Cover letter</b> <span class="tag">${esc(kit.language)}</span>
          <button class="btn small ghost" data-kit="${offerId}" data-lang="${other}">Rewrite in ${other}</button>
          <textarea class="input" id="kitLetter" rows="11" style="margin-top:8px">${esc(kit.letter)}</textarea>
          <div style="display:flex;gap:8px;margin-top:8px"><button class="btn" id="kitCopy">Copy the letter</button>
            <button class="btn" id="kitPdf" data-id="${kit.application_id}">Download as PDF</button></div></div></div>
        <div class="kit-step"><span class="step-n">3</span><div><b>Files to upload</b> <span class="small muted">— the ${esc(kit.language)} set; change them in Settings → Documents</span>
          <div class="chips" style="margin-top:8px">${kit.documents.map((doc) => `<a class="chip" href="/api/documents/${doc.id}/file" target="_blank" rel="noopener">${esc(doc.title)} ↓</a>`).join("") || `<span class="muted small">No default document for this language.</span>`}</div></div></div>
        <div class="kit-step"><span class="step-n">4</span><div><b>Once submitted</b>, record it so it leaves your list and gets a reminder in two weeks.
          <div style="margin-top:8px">${kit.applied_at ? `<span class="tag new">Applied ${esc(kit.applied_at)}</span>` : `<button class="btn primary" data-kit-applied="${offerId}">I applied</button>`}</div></div></div>
      </div></div>`;
  $("#modal").dataset.kitApp = kit.application_id;
  $("#modal").classList.add("on");
  $("#scrim").classList.add("on");
}
async function saveKitLetter() {
  const id = $("#modal").dataset.kitApp;
  if (id && $("#kitLetter")) await api(`/api/applications/${id}`, { body: { letter: $("#kitLetter").value } });
}

/* ------------------------------------------------- settings: automation card */
function automationCard() {
  const { priorities, automation, view_defaults: view } = S.state;
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const check = (id, on, label) => `<label class="switch"><input type="checkbox" id="${id}" ${on ? "checked" : ""}> ${label}</label>`;
  return `<div class="card pad" id="automationCard"><h2>Sessions, mailbox and notifications</h2>
    <p class="muted small" style="margin:4px 0 12px">The Radar works in the background (every 15 minutes while your PC is on) and prepares everything for your review sessions.</p>
    <div class="field">Review days — you get a summary e-mail that morning
      <div class="chips" style="margin-top:6px">${days.map((day) => `<label class="chip ${(priorities.session_days || []).includes(day) ? "on" : ""}"><input type="checkbox" data-session-day="${day}" ${(priorities.session_days || []).includes(day) ? "checked" : ""} hidden>${day}</label>`).join("")}</div></div>
    <div class="stack" style="gap:8px;margin-top:12px">
      ${check("autoInbox", automation.inbox_enabled, "Read my mailbox for replies and bounces — only messages from people I e-mailed are opened; nothing is marked as read")}
      ${check("autoNotify", automation.notify_enabled, "E-mail me when something needs me: a reply, a bounced address, a deadline within a week")}
      ${check("hideLow", view.min_score >= 72, "Hide grade C and D offers by default")}
      ${check("hideIneligible", view.hide_ineligible, "Hide offers I am not eligible for (nationality, visa, required language, apprenticeship)")}
    </div>
    <div class="form-grid" style="margin-top:12px">
      <label class="field">Send notifications to<input class="input" id="notifyTo" value="${esc(automation.notify_to)}"></label>
      <label class="field">Researcher e-mails prepared per scan<input class="input" type="number" min="0" max="60" id="draftsPerRun" value="${esc(priorities.outreach_drafts_per_run ?? 20)}"></label>
    </div>
    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap"><button class="btn primary" id="saveAutomation">Save</button>
      <button class="btn" id="testSummary">Send me a test summary</button>
      <span class="small muted" style="align-self:center">Mailbox last checked ${esc(ago(automation.inbox_checked_at))}${automation.inbox_error ? " — " + esc(automation.inbox_error) : ""}</span></div>
  </div>`;
}
async function saveAutomation() {
  const session_days = $$("[data-session-day]").filter((input) => input.checked).map((input) => input.dataset.sessionDay);
  await api("/api/automation", { body: { inbox_enabled: $("#autoInbox").checked, notify_enabled: $("#autoNotify").checked, notify_to: $("#notifyTo").value.trim() } });
  await api("/api/settings", { body: { priorities: { session_days, hide_low_grades: $("#hideLow").checked, hide_ineligible: $("#hideIneligible").checked,
    outreach_drafts_per_run: Number($("#draftsPerRun").value) || 0 } } });
  await Promise.all([loadState(), loadOffers()]);
}

/* ----------------------------------------------------------------- events */
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, a, label.chip, [data-open-app]") || event.target;
  const data = target.dataset || {};
  try {
    if (event.target.matches("input[data-sel], input[data-selp], td.ck")) return;   // handled on "change"
    if (data.bulkClear !== undefined) { S.sel.clear(); S.selPeople.clear(); return render(); }
    if (data.select) {
      const pool = data.select === "outreach" ? $$("#view input[data-sel]").map((input) => Number(input.dataset.sel)) : S.session[data.select].map((item) => item.id);
      const wanted = pool.slice(0, Number(data.count) || pool.length);
      const allOn = wanted.every((id) => S.sel.has(id));
      wanted.forEach((id) => (allOn ? S.sel.delete(id) : S.sel.add(id)));
      return render();
    }
    if (data.bulk) {
      const ids = [...S.sel];
      if (data.bulk === "delete" && !confirm(`Delete ${plural(ids.length, "draft")}? The researchers stay in your list.`)) return;
      target.disabled = true;
      const result = await api("/api/applications/bulk", { body: { ids, action: data.bulk } });
      S.sel.clear();
      const problems = (result.failed || []).length ? ` ${result.failed.length} could not be processed: ${result.failed[0].error}` : "";
      toast(data.bulk === "approve"
        ? `${plural(result.done, "e-mail")} approved. They leave ${result.daily_cap} a day — the last one in about ${plural(result.days, "day")}.${problems}`
        : `${plural(result.done, "e-mail")} updated.${problems}`, !!problems && !result.done);
      return reloadWork();
    }
    if (target.id === "sendSooner") {
      target.disabled = true;
      const result = await api("/api/outreach/sooner", { body: {} });
      toast(`${plural(result.rescheduled, "e-mail")} will leave from now on, a minute apart — ${result.room_today} more today, then ${result.daily_cap} a day.`);
      return reloadWork();
    }
    if (target.id === "raiseCap") {
      const answer = prompt(`How many e-mails may leave per day? (now ${S.state.mail.daily_cap})\n\nGmail accepts far more, but sending many similar e-mails a day from one address raises the risk of landing in spam. 20–40 is a sensible range.`, String(S.state.mail.daily_cap));
      if (answer === null) return;
      const value = Math.round(Number(answer));
      if (!value || value < 1 || value > 60) return toast("Choose a number between 1 and 60.", true);
      await api("/api/mail", { body: { daily_cap: value } });
      toast(`Up to ${value} e-mails a day from now on.`);
      return reloadWork();
    }
    if (data.sendPeople !== undefined) {
      const ids = [...S.selPeople];
      const mail = S.state.mail;
      const sender = (mail.accounts.find((account) => (account.google && account.google.connected) || account.has_password) || {}).address || "your mailbox";
      if (!confirm(`Write and send ${plural(ids.length, "e-mail")}?\n\nEach one is personalised for the person and leaves from ${sender}, `
        + `at most ${mail.daily_cap} a day, ${Number(mail.delay_hours) ? `starting in ${mail.delay_hours} h` : "starting right away"}.\n\nUntil each one leaves you can cancel it in Outreach › Scheduled.`)) return;
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Writing ${ids.length}…`;
      const result = await api("/api/outreach/send", { body: { contact_ids: ids } });
      S.selPeople.clear();
      const notes = [];
      if (result.skipped.length) notes.push(`${plural(result.skipped.length, "person", "people")} skipped (${result.skipped[0].name}: ${result.skipped[0].reason})`);
      if (result.failed.length) notes.push(`${result.failed.length} not approved: ${result.failed[0].error}`);
      toast(result.approved
        ? `${plural(result.approved, "e-mail")} scheduled — ${result.daily_cap} leave each day, the last in about ${plural(result.days, "day")}. Cancel any in Outreach › Scheduled.${notes.length ? " " + notes.join(" · ") : ""}`
        : `Nothing was scheduled. ${notes.join(" · ")}`, !result.approved);
      await Promise.all([loadContacts(), loadApplications(), loadState()]); S.session = null;
      return render();
    }
    if (target.id === "pSelectReady") {
      const ready = filteredPeople().filter(readyToSend).map((person) => person.id);
      const allOn = ready.length && ready.every((id) => S.selPeople.has(id));
      ready.forEach((id) => (allOn ? S.selPeople.delete(id) : S.selPeople.add(id)));
      return renderPeople(), renderBulkBar(), refreshSelectReady();
    }
    if (data.bulkPeople) {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Writing…`;
      const result = await api("/api/outreach/prepare", { body: { contact_ids: [...S.selPeople] } });
      S.selPeople.clear();
      toast(`${plural(result.created, "e-mail")} prepared.${result.skipped.length ? ` ${result.skipped.length} skipped (${result.skipped[0].reason}).` : ""} Review them in Session.`);
      await Promise.all([loadContacts(), loadApplications(), loadState()]); S.session = null;
      return render();
    }
    if (data.prepare) {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Writing…`;
      const result = await api("/api/outreach/prepare", { body: { limit: Number(data.prepare) } });
      toast(result.created ? `${plural(result.created, "e-mail")} prepared for the best-matching researchers.` : "No more researcher with a verified address — find more under Researchers.", !result.created);
      return reloadWork();
    }
    if (data.outcome) {
      await api("/api/applications/bulk", { body: { ids: [Number(data.id)], action: data.outcome } });
      toast({ interview: "Moved to Interview — good luck.", declined: "Filed as declined.", closed: "Done." }[data.outcome]);
      return reloadWork();
    }
    if (data.declineAll) {
      await api("/api/applications/bulk", { body: { ids: data.declineAll.split(",").map(Number), action: "declined" } });
      toast("Filed as declined."); return reloadWork();
    }
    if (data.verifyContact) {
      if (!confirm(`Confirm that ${data.email} is ${data.name}'s real address (seen on their page or a paper)?`)) return;
      if (!S.contacts.length) await loadContacts();
      const person = S.contacts.find((row) => row.id === Number(data.verifyContact));
      await api(`/api/contacts/${data.verifyContact}`, { body: { notes: `${person?.notes || ""}\n[Address verified by you on ${S.state.today}]` } });
      await loadContacts(); toast("Verified — an e-mail can now be prepared."); return reloadWork();
    }
    if (target.id === "checkInbox") {
      target.disabled = true; target.innerHTML = `<span class="spinner"></span>Checking…`;
      const result = await api("/api/inbox/check", { body: {} });
      toast(result.error ? `Mailbox problem: ${result.error}` : `${plural(result.replies, "new reply", "new replies")}, ${plural(result.bounces, "bounce")}.`, !!result.error);
      return reloadWork();
    }
    if (data.kit) return openKit(Number(data.kit), data.lang);
    if (target.id === "kitCopy") { await saveKitLetter(); await navigator.clipboard.writeText($("#kitLetter").value); return toast("Letter copied."); }
    if (target.id === "kitPdf") { await saveKitLetter(); window.open(`/api/applications/${data.id}/letter`, "_blank", "noopener"); return; }
    if (data.kitApplied) {
      await saveKitLetter();
      await api(`/api/offers/${data.kitApplied}/applied`, { body: {} });
      toast("Recorded as applied — reminder set for two weeks from now.");
      closeModal(); await loadOffers(); return reloadWork();
    }
    if (data.jump) { document.getElementById(data.jump)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    if (data.otab) { S.outreach.tab = data.otab; S.sel.clear(); return viewOutreach(); }
    if (target.id === "pReady") { S.people.ready = !S.people.ready; S.people.limit = 60; return viewPeople(); }
    if (target.id === "eligibleChip") { S.filters.eligible = !S.filters.eligible; return viewOffers(); }
    if (target.id === "saveAutomation") { await saveAutomation(); toast("Saved."); return viewSettings(); }
    if (target.id === "testSummary") {
      await saveAutomation(); target.disabled = true;
      await api("/api/automation", { body: { send_test: true } });
      toast(`Test summary sent to ${S.state.automation.notify_to}.`); target.disabled = false; return;
    }
    if (data.openApp && (event.target.closest("button[data-open-app]") || !event.target.closest("a, button, input, label"))) return openApplication(Number(data.openApp));
    const expandable = event.target.closest("[data-expand]");
    if (expandable && !event.target.closest("a, button")) expandable.classList.toggle("open");
  } catch (error) { toast(error.message, true); target.disabled = false; }
});
document.addEventListener("change", (event) => {
  const input = event.target;
  if (input.dataset.sel) { const id = Number(input.dataset.sel); input.checked ? S.sel.add(id) : S.sel.delete(id); renderBulkBar(); }
  if (input.dataset.selp) { const id = Number(input.dataset.selp); input.checked ? S.selPeople.add(id) : S.selPeople.delete(id); renderBulkBar(); refreshSelectReady(); }
  if (input.id === "pSelAll") {
    $$("#peopleCard input[data-selp]:not(:disabled)").forEach((box) => {
      box.checked = input.checked;
      input.checked ? S.selPeople.add(Number(box.dataset.selp)) : S.selPeople.delete(Number(box.dataset.selp));
    });
    renderBulkBar(); refreshSelectReady();
  }
  if (input.dataset.sessionDay) input.closest(".chip").classList.toggle("on", input.checked);
});
document.addEventListener("input", debounce((event) => {
  if (event.target.id === "oq") {
    S.outreach.q = event.target.value; viewOutreach();
    const box = $("#oq"); box.focus(); box.setSelectionRange(box.value.length, box.value.length);
  }
}));


/* ------------------------------------------------------- e-mail accounts */
const G_LOGO = `<svg class="g-logo" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17z"/><path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.2-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z"/></svg>`;
const GOOGLE_STEPS = [
  [`Open <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener noreferrer">Google Cloud › New project</a> signed in with your <b>personal Gmail</b>, name it “Internship Radar”, and create it.`],
  [`Enable the <a href="https://console.cloud.google.com/apis/library/gmail.googleapis.com" target="_blank" rel="noopener noreferrer">Gmail API</a> for that project.`],
  [`Open <a href="https://console.cloud.google.com/auth/overview" target="_blank" rel="noopener noreferrer">Google Auth Platform</a> › <b>Get started</b>: app name “Internship Radar”, your e-mail, audience <b>External</b>.`],
  [`In <a href="https://console.cloud.google.com/auth/audience" target="_blank" rel="noopener noreferrer">Audience</a>, click <b>Publish app</b>. Otherwise Google withdraws the permission every 7 days. Google will call the app “unverified”: that is normal for an app only you use.`],
  [`In <a href="https://console.cloud.google.com/auth/clients" target="_blank" rel="noopener noreferrer">Clients</a> › <b>Create client</b>, choose type <b>Desktop app</b>, create it and click <b>Download JSON</b>.`],
  [`Load that file here:`],
];
function googleSetup(mail) {
  const gc = mail.google_client || {};
  if (gc.configured) {
    return `<div class="gsetup done"><span class="status-dot ok"></span><span>Google sign-in is set up${gc.project ? ` (project <b>${esc(gc.project)}</b>)` : ""}.</span>
      <button class="btn small ghost" id="googleClientRemove" title="Remove the client ID and secret from this computer">Change</button></div>`;
  }
  return `<div class="gsetup"><div class="gsetup-head"><span class="step-n">1</span><b>One-time setup — about 5 minutes</b>
      <span class="hint">Google needs to know the Radar before it shows its sign-in screen. You do this once; then each mailbox is one click.</span></div>
    <ol class="gsteps">${GOOGLE_STEPS.map(([text]) => `<li>${text}</li>`).join("")}</ol>
    <div class="gload"><label class="btn primary" for="googleClientFile">Load the downloaded JSON file</label>
      <input type="file" id="googleClientFile" accept=".json,application/json" hidden>
      <details class="gmanual"><summary class="small">or paste the client ID and secret</summary>
        <div class="form-grid" style="margin-top:8px"><label class="field">Client ID<input class="input" id="googleClientId" placeholder="….apps.googleusercontent.com"></label>
          <label class="field">Client secret<input class="input" type="password" autocomplete="off" id="googleClientSecret"></label></div>
        <button class="btn small" id="googleClientSave" style="margin-top:8px">Save</button></details></div>
  </div>`;
}
function accountRow(account, index, mail) {
  const g = account.google || {};
  const ready = (mail.google_client || {}).configured;
  const status = g.connected && !g.expired
    ? `<span class="pill ok">✓ Connected with Google</span><span class="small muted">${g.read ? "sending and reading replies" : "sending only"}</span>`
    : g.expired ? `<span class="pill warn">Authorisation expired — reconnect</span>`
    : account.has_password ? `<span class="pill">App password stored</span>`
    : `<span class="pill off">Not connected</span>`;
  const actions = g.connected
    ? `<button class="btn gbtn" data-google-connect="${index}" data-read="${g.read ? 1 : 0}">${G_LOGO}Reconnect</button>
       <button class="btn small ghost" data-google-disconnect="${index}">Disconnect</button>`
    : `<label class="switch small" title="Lets the Radar find the answers to your e-mails (only messages from people you wrote to)"><input type="checkbox" data-google-read="${index}" checked> Also read replies</label>
       <button class="btn gbtn" data-google-connect="${index}" ${ready ? "" : `disabled title="Do the one-time setup above first"`}>${G_LOGO}Connect with Google</button>`;
  return `<div class="acct">
    <div class="acct-role">${index === 0 ? "Send from" : "Fallback"}</div>
    <input class="input" data-mail-address="${index}" value="${esc(account.address)}" placeholder="${index === 0 ? "your school address" : "your personal Gmail"}" aria-label="${index === 0 ? "Send from" : "Fallback account"}">
    <div class="acct-status">${status}</div>
    <div class="acct-actions">${actions}</div>
    <details class="pw"><summary class="small faint">use an app password instead</summary>
      <input class="input" type="password" autocomplete="new-password" data-mail-password="${index}" placeholder="${account.has_password ? "stored — leave empty to keep" : "xxxx xxxx xxxx xxxx"}"></details>
  </div>`;
}
function mailCard() {
  const mail = S.state.mail;
  return `<div class="card pad" id="mailCard"><h2>E-mail sending</h2>
    <p class="muted small" style="margin:4px 0 12px">Approved e-mails leave from the first mailbox; the second is used if the first refuses.
      Mailboxes are connected with Google's own sign-in screen. No password is stored, and you can withdraw the permission at any time at
      <a href="https://myaccount.google.com/permissions" target="_blank" rel="noopener noreferrer">myaccount.google.com/permissions</a>.</p>
    ${googleSetup(mail)}
    <div class="accts">${mail.accounts.map((account, index) => accountRow(account, index, mail)).join("")}</div>
    <div class="form-grid" style="margin-top:14px">
      <label class="field">Sender address shown<input class="input" id="mailAlias" value="${esc(mail.from_alias || "")}" placeholder="optional — leave empty to show the mailbox itself"></label>
      <label class="field">Wait after approval (hours)<input class="input" type="number" min="0" max="72" step="0.5" id="mailDelay" value="${esc(mail.delay_hours)}"></label>
      <label class="field">Maximum e-mails per day<input class="input" type="number" min="1" max="60" id="mailCap" value="${esc(mail.daily_cap)}"></label>
    </div>
    <div style="display:flex;gap:8px;margin-top:12px"><button class="btn primary" id="saveMail">Save e-mail settings</button><button class="btn" id="testMail">Save and test the connection</button></div>
    <div id="mailResult" class="small" style="margin-top:10px"></div>
  </div>`;
}
async function refreshMail(message) {
  await loadState();
  if (S.view === "settings") { viewSettings(); $("#mailCard")?.scrollIntoView({ block: "start" }); }
  if (message) toast(message);
}
async function saveGoogleClient(body) {
  try { S.state.mail = await api("/api/google/client", { body }); } catch (error) { return toast(error.message, true); }
  refreshMail("Google sign-in is set up. Now click “Connect with Google” next to each address.");
}
let googleWatch = null;
function watchGoogle(index, before) {
  clearInterval(googleWatch);
  const started = Date.now();
  googleWatch = setInterval(async () => {
    if (Date.now() - started > 5 * 60 * 1000) return clearInterval(googleWatch);
    let mail;
    try { mail = await api("/api/mail"); } catch (error) { return; }
    const account = mail.accounts.find((item) => item.google && item.google.connected && item.google.connected_at !== before[item.address]);
    if (account) { clearInterval(googleWatch); refreshMail(`${account.address} is connected with Google.`); }
  }, 2000);
}
window.addEventListener("message", (event) => {
  if (event.origin === location.origin && event.data && "radarGoogle" in event.data) refreshMail(event.data.radarGoogle ? "" : "");
});
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, label[for]");
  if (!target) return;
  const data = target.dataset;
  try {
    if (data.googleConnect !== undefined) {
      const index = Number(data.googleConnect);
      const read = data.read !== undefined ? data.read === "1" : !!$(`[data-google-read="${index}"]`)?.checked;
      const popup = window.open("about:blank", "_blank");  // opened now, while the click still counts, so it is not blocked
      const accounts = S.state.mail.accounts.map((_, i) => ({ address: $(`[data-mail-address="${i}"]`).value.trim() }));
      await api("/api/mail", { body: { accounts } });
      const before = Object.fromEntries(S.state.mail.accounts.map((item) => [item.address, (item.google || {}).connected_at]));
      const { url } = await api("/api/google/connect", { body: { index, read } });
      if (popup) popup.location.href = url; else location.href = url;
      toast("Finish on Google's page — choose the account and tick the boxes. This page updates by itself.");
      return watchGoogle(index, before);
    }
    if (data.googleDisconnect !== undefined) {
      if (!confirm("Disconnect this mailbox? The Radar will stop sending from it, and its permission is withdrawn at Google.")) return;
      S.state.mail = await api("/api/google/disconnect", { body: { index: Number(data.googleDisconnect) } });
      return refreshMail("Disconnected.");
    }
    if (target.id === "googleClientRemove") {
      if (!confirm("Remove the Google client from this computer? Connected mailboxes stop working until you load a client again.")) return;
      return saveGoogleClient({ remove: true });
    }
    if (target.id === "googleClientSave") {
      return saveGoogleClient({ client_id: $("#googleClientId").value, client_secret: $("#googleClientSecret").value });
    }
  } catch (error) { toast(error.message, true); }
});
document.addEventListener("change", async (event) => {
  if (event.target.id !== "googleClientFile" || !event.target.files[0]) return;
  const file = event.target.files[0];
  if (file.size > 20000) return toast("That file is too large to be the client JSON.", true);
  saveGoogleClient({ json: await file.text() });
});
