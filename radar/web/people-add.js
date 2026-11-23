/* "Add a researcher": one person (paste-to-fill) or a pasted list.  Loaded after session.js. */
"use strict";

const ADD_TITLES = [["", "Not sure — “Bonjour First Last,”"], ["prof_m", "Professor, Monsieur — “Monsieur le Professeur …,”"],
  ["prof_f", "Professor, Madame — “Madame la Professeure …,”"], ["prof", "Professor — “Bonjour Professeur …,”"],
  ["res_m", "Researcher, Monsieur — “Monsieur …,”"], ["res_f", "Researcher, Madame — “Madame …,”"]];
S.addTab = "one";

function countryOptions() {
  const codes = [...new Set(Object.values(S.state.priorities.tiers).flatMap((tier) => tier.countries))];
  return `<option value="">Detect from the institution</option>` +
    codes.map((code) => [code, country(code)]).sort((a, b) => a[1].localeCompare(b[1]))
      .map(([code, name]) => `<option value="${code}">${esc(name)}</option>`).join("");
}

function openAddPerson(tab = S.addTab) {
  S.addTab = tab;
  const field = (id, label, extra = "") => `<label class="field"><span class="lbl">${label}</span><input class="input" id="${id}" ${extra}></label>`;
  const one = `
      <div class="modal-body">
        <label class="field"><span class="lbl">Paste anything about them <span class="faint">— optional: an e-mail signature, a few lines of their web page,
          or “Name &lt;address&gt;”. The empty fields fill themselves.</span></span>
          <textarea class="input" id="apPaste" rows="3" placeholder="Prof. Marie Curie&#10;Laboratoire de Physique, Université Paris Cité&#10;marie.curie@u-paris.fr"></textarea></label>
        <div class="form-grid two">
          ${field("apName", `Full name <b class="req">*</b>`, `placeholder="First Last" autocomplete="off"`)}
          ${field("apEmail", "E-mail", `type="email" placeholder="leave empty if you don't have it yet" autocomplete="off"`)}
          <label class="field"><span class="lbl">How to address them</span><select class="input select" id="apTitle">${ADD_TITLES.map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("")}</select></label>
          <label class="field"><span class="lbl">Country</span><select class="input select" id="apCountry">${countryOptions()}</select></label>
          ${field("apLab", "Lab or team", `placeholder="e.g. LIX, MICC, Équipe SyNaLP"`)}
          ${field("apUniversity", "University or institution", `placeholder="e.g. École polytechnique"`)}
          ${field("apCity", "City", `placeholder="helps choose French or English"`)}
          ${field("apUrl", "Profile or lab page", `placeholder="https://…"`)}
        </div>
        ${field("apAreas", `Research topics <span class="faint">— comma separated; the e-mail says “Your research on …”</span>`, `placeholder="e.g. large language models, information retrieval"`)}
        ${field("apNote", `Your note <span class="faint">— private; “à vérifier” holds the e-mail back until you confirm the address</span>`, `placeholder="e.g. met at the AI summit, recommended by Prof. X"`)}
      </div>`;
  const list = `
      <div class="modal-body">
        <label class="field"><span class="lbl">One person per line: name and e-mail, optionally the institution and the country — in any order, separated by
          commas, semicolons or tabs (a copy from Excel works).</span>
          <textarea class="input mono" id="apList" rows="9" placeholder="Prof. Marie Curie <marie.curie@u-paris.fr>, Université Paris Cité, France&#10;Luca Rossi; luca.rossi@polimi.it; Politecnico di Milano; Italy&#10;Anna Weber, anna.weber@tum.de, TU München, DE"></textarea></label>
        <div class="small muted" id="apListCount">Paste your list above.</div>
      </div>`;
  $("#modal").innerHTML = `
    <div class="modal-card add-person">
      <div class="modal-head"><div><h2>Add a researcher</h2>
        <div class="small muted">Someone you found yourself — a talk, a paper, a recommendation. Their e-mail is written like the others and waits for your approval.</div></div>
        <button class="btn ghost small" data-close-modal>Close</button></div>
      <div class="tabs add-tabs"><button class="tab ${tab === "one" ? "on" : ""}" data-add-tab="one">One person</button>
        <button class="tab ${tab === "list" ? "on" : ""}" data-add-tab="list">Paste a list</button></div>
      ${tab === "one" ? one : list}
      <div class="modal-foot">
        <label class="switch"><input type="checkbox" id="apPrepare" checked> Write ${tab === "one" ? "their e-mail" : "their e-mails"} now
          <span class="faint small">(waits in Session for your approval)</span></label>
        <div style="display:flex;gap:8px"><button class="btn ghost" data-close-modal>Cancel</button>
          <button class="btn primary" id="apSave">${tab === "one" ? "Add this person" : "Add these people"}</button></div>
      </div>
    </div>`;
  $("#modal").classList.add("on");
  $("#scrim").classList.add("on");
  setTimeout(() => $(tab === "one" ? "#apPaste" : "#apList")?.focus(), 30);
}

/* Fill empty fields from pasted text — only what is plainly written there; nothing is guessed from the address. */
const NAME_LINE = /^(?:(?:prof\.?|pr\.?|dr\.?|mme|m\.|mr\.?|ms\.?)\s+)?[A-ZÀ-Ý][\p{L}'’-]+(?:\s+[A-ZÀ-Ý][\p{L}'’-]+){1,3}\s*,?$/u;
const HONORIFIC = /^\s*(?:prof(?:essor|esseur|esseure)?\.?|pr\.?|dr\.?|mme|madame|m\.|monsieur|mr\.?|ms\.?)\s+/i;
function autofillPerson(text) {
  const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const set = (id, value) => { const input = $(id); if (input && !input.value.trim() && value) input.value = value.trim(); };
  set("#apEmail", (text.match(/[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/) || [])[0]);
  const angle = text.match(/^\s*([^<\n@]{3,60})<\s*[^>]+@[^>]+>/m);
  const nameLine = angle ? angle[1] : lines.find((line) => !/[@\d:/]/.test(line) && NAME_LINE.test(line.replace(HONORIFIC, "")));
  if (nameLine) set("#apName", nameLine.replace(HONORIFIC, "").replace(/[\s,;]+$/, ""));
  const professor = /\b(?:professor|professeure?|maître de conférences|maitre de conferences)\b|(?:^|\s)(?:prof|pr)\.?\s/i.test(text);
  const woman = /\b(?:madame|mme|mrs?\.?|ms\.?|professeure)\b/i.test(text) && !/\bmr\.?\b/i.test(text);
  const man = /\b(?:monsieur|mr\.?)\b|(?:^|\s)m\.\s/i.test(text);
  if (!$("#apTitle").value) $("#apTitle").value = professor ? (woman ? "prof_f" : man ? "prof_m" : "prof") : woman ? "res_f" : man ? "res_m" : "";
  const isUniversity = (line) => /universit|institut|école|ecole|school|politecnico|polytechn|cnrs|inria|ku leuven|epfl|eth z|college|fondazione|centre|center/i.test(line);
  const isLab = (line) => /\blab\b|laborato|team|équipe|equipe|group|umr\b|research unit/i.test(line);
  const plain = lines.filter((line) => !/@|https?:/.test(line) && line !== nameLine);
  const school = /universit|école|ecole|politecnico|polytechn|school|college|institut|cnrs|inria/i;
  // "MICC, Università di Firenze": what comes before the university is the lab.
  const split = plain.map((line) => line.split(",")).find((parts) => parts.length > 1 && parts.findIndex((part) => school.test(part)) > 0);
  if (split) {
    const at = split.findIndex((part) => school.test(part));
    set("#apLab", split.slice(0, at).join(","));
    set("#apUniversity", split.slice(at).join(","));
  } else {
    set("#apLab", plain.find(isLab));
    set("#apUniversity", plain.find((line) => isUniversity(line) && !isLab(line)) || plain.find(isUniversity));
  }
  set("#apUrl", (text.match(/https?:\/\/[^\s<>"]+/) || [])[0]);
}

async function savePerson(button) {
  const prepare = $("#apPrepare").checked;
  let body;
  if (S.addTab === "one") {
    const person = { name: $("#apName").value, email: $("#apEmail").value, title: $("#apTitle").value, country: $("#apCountry").value,
      lab: $("#apLab").value, university: $("#apUniversity").value, city: $("#apCity").value, url: $("#apUrl").value,
      areas: $("#apAreas").value, note: $("#apNote").value };
    if (!person.name.trim()) { $("#apName").focus(); return toast("Give the person's name.", true); }
    body = { person, prepare };
  } else {
    if (!$("#apList").value.trim()) return toast("Paste at least one line.", true);
    body = { list: $("#apList").value, prepare };
  }
  const label = button.textContent;
  button.disabled = true; button.innerHTML = `<span class="spinner"></span>Adding…`;
  let result;
  try { result = await api("/api/contacts/add", { body }); }
  catch (error) { button.disabled = false; button.textContent = label; return toast(error.message, true); }
  await Promise.all([loadContacts(), loadApplications(), loadState()]);
  S.session = null;
  const parts = [];
  if (result.added.length) parts.push(`${plural(result.added.length, "person", "people")} added`);
  if (result.existing.length) parts.push(result.message || `${result.existing.length} already in your list`);
  if (result.drafts) parts.push(`${plural(result.drafts, "e-mail")} written — approve ${result.drafts === 1 ? "it" : "them"} in Session`);
  if (result.skipped && result.skipped.length) parts.push(`${plural(result.skipped.length, "e-mail")} not written (${result.skipped[0].reason})`);
  if (result.problems.length) parts.push(`${plural(result.problems.length, "line")} not understood: “${result.problems[0].line.slice(0, 40)}” — ${result.problems[0].reason}`);
  toast(parts.join(" · ") || "Nothing was added.", !result.ids.length);
  closeModal();
  if (S.addTab === "one" && result.ids.length === 1 && result.drafts) {
    const created = S.applications.find((a) => a.contact_id === result.ids[0] && ["to_review", "ready"].includes(a.stage));
    if (created) return openApplication(created.id);  // show the e-mail straight away
  }
  if (S.view === "people") { S.people.kind = "manual"; S.people.ready = false; S.people.q = ""; viewPeople(); } else render();
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.addPerson !== undefined) return openAddPerson(target.dataset.addPerson || "one");
  if (target.dataset.addTab) return openAddPerson(target.dataset.addTab);
  if (target.id === "apSave") return savePerson(target);
});
document.addEventListener("input", (event) => {
  if (event.target.id === "apPaste") autofillPerson(event.target.value);
  if (event.target.id === "apList") {
    const lines = event.target.value.split("\n").filter((line) => line.trim());
    const people = lines.filter((line) => /@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/.test(line)).length;
    $("#apListCount").textContent = lines.length
      ? `${plural(people, "person", "people")} found${lines.length > people ? ` · ${plural(lines.length - people, "line")} without an e-mail will be skipped` : ""}`
      : "Paste your list above.";
    $("#apSave").textContent = people ? `Add ${plural(people, "person", "people")}` : "Add these people";
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && $("#apSave") && $("#modal").classList.contains("on")) savePerson($("#apSave"));
});
