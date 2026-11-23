/* Researchers by place: country, region, city, and the Nice · Côte d'Azur area.  Loaded after app.js. */
"use strict";

Object.assign(S.people, { country: "", region: "", city: "", area: "" });

/* Does this person match the place filters? (used by filteredPeople) */
function placeMatches(person) {
  const f = S.people;
  if (f.area && person.area !== f.area) return false;
  if (f.country && person.country !== f.country) return false;
  if (f.region && person.region !== f.region) return false;
  if (f.city && person.city !== f.city) return false;
  return true;
}

function countBy(rows, key) {
  const counts = new Map();
  rows.forEach((row) => { if (row[key]) counts.set(row[key], (counts.get(row[key]) || 0) + 1); });
  return [...counts.entries()];
}

/* The place row of the Researchers page.  Each list only offers what exists among the people shown by the other filters. */
function placeBar() {
  const f = S.people;
  const others = S.contacts.filter((person) => person.status !== "ignored");
  const inCountry = others.filter((person) => !f.country || person.country === f.country);
  const inRegion = inCountry.filter((person) => !f.region || person.region === f.region);
  const countries = countBy(others, "country").sort((a, b) => b[1] - a[1]);
  const regions = countBy(inCountry, "region").sort((a, b) => a[0].localeCompare(b[0]));
  const cities = countBy(inRegion, "city").sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const nice = others.filter((person) => person.area === "cote_azur");
  const niceReady = nice.filter((person) => typeof readyToSend === "function" && readyToSend(person)).length;
  const option = (value, label, n, selected) => `<option value="${esc(value)}" ${selected ? "selected" : ""}>${esc(label)}${n !== undefined ? ` (${n})` : ""}</option>`;
  return `<div class="toolbar placebar">
    <button class="chip area ${f.area === "cote_azur" ? "on" : ""}" id="pNice" title="Nice, Sophia Antipolis, Antibes, Valbonne, Biot, Cannes, Monaco…">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12zm0-9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg>
      Nice · Côte d'Azur<small>${nice.length}</small></button>
    <select class="input select small-select" id="pCountry" aria-label="Country">
      ${option("", "All countries")}${countries.map(([code, n]) => option(code, country(code), n, f.country === code)).join("")}
    </select>
    ${regions.length ? `<select class="input select small-select" id="pRegion" aria-label="Region">
      ${option("", "All regions")}${regions.map(([name, n]) => option(name, name, n, f.region === name)).join("")}</select>` : ""}
    <select class="input select small-select" id="pCity" aria-label="City" ${cities.length ? "" : "disabled"}>
      ${option("", cities.length ? "All cities" : "No city known")}${cities.map(([name, n]) => option(name, name, n, f.city === name)).join("")}
    </select>
    ${f.area || f.country || f.region || f.city ? `<button class="btn small ghost" id="pPlaceClear">Clear place</button>` : ""}
    ${f.area === "cote_azur" ? `<span class="hint">${niceReady} ready to write to</span>
      <button class="btn small primary" id="pFindNice" title="Search OpenAlex for researchers publishing AI work at Université Côte d'Azur, EURECOM, Inria and I3S">Find more researchers around Nice</button>` : ""}
  </div>`;
}

document.addEventListener("change", (event) => {
  const id = event.target.id;
  if (!["pCountry", "pRegion", "pCity"].includes(id)) return;
  const f = S.people;
  if (id === "pCountry") { f.country = event.target.value; f.region = ""; f.city = ""; f.area = ""; }
  if (id === "pRegion") { f.region = event.target.value; f.city = ""; f.area = ""; }
  if (id === "pCity") { f.city = event.target.value; f.area = ""; }
  f.limit = 60; f.ready = false;
  viewPeople();
});
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  const f = S.people;
  if (target.id === "pNice") {
    const on = f.area !== "cote_azur";
    Object.assign(f, { area: on ? "cote_azur" : "", country: "", region: "", city: "", tier: "all", kind: "all", ready: false, limit: 60 });
    return viewPeople();
  }
  if (target.id === "pPlaceClear") { Object.assign(f, { area: "", country: "", region: "", city: "", limit: 60 }); return viewPeople(); }
  if (target.id === "pFindNice") {
    target.disabled = true; target.innerHTML = `<span class="spinner"></span>Searching Nice and Sophia Antipolis…`;
    try {
      const result = await api("/api/places/find", { body: { area: "cote_azur" } });
      await loadContacts(); await loadState();
      toast(result.error ? result.error
        : `${plural(result.found, "researcher")} publish AI work around Nice — ${result.created} new in your list. Their published addresses are being looked up now; refresh in a few minutes.`,
        !!result.error);
    } catch (error) { toast(error.message, true); }
    return viewPeople();
  }
});
