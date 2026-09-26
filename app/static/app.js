"use strict";
// Plants - vanilla JS front end. No build step.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const KIND_ICON = { water: "💧", fertilize: "🧪", mist: "🌫️", repot: "🪴", rotate: "🔄", custom: "✅" };
const KINDS = [["water", "Water"], ["fertilize", "Fertilize"], ["mist", "Mist"], ["repot", "Repot"], ["rotate", "Rotate"], ["custom", "Custom"]];
// Starting intervals when a task type is picked; always editable.
const KIND_DAYS = { water: 7, fertilize: 30, mist: 3, repot: 365, rotate: 7, custom: 14 };
const feature = (name) => !state.settings || state.settings[`feature_${name}`] !== false;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const state = { me: null, settings: null, roomFilter: "all", selected: new Set(), search: "", plantRoom: "all" };

class ApiError extends Error {}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (res.status === 401 && !opts.allow401) {
    state.me = null;
    await showAuth();
    throw new ApiError("Please sign in again");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    let msg = (data && data.detail) || res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg.replace(/^Value error, /, "")).join("; ");
    throw new ApiError(msg);
  }
  return data;
}

let toastTimer;
function toast(msg, undo) {
  const el = $("#toast");
  el.innerHTML = `<span>${esc(msg)}</span>${undo ? '<button class="small" type="button">Undo</button>' : ""}`;
  el.hidden = false;
  if (undo) $("button", el).onclick = () => { el.hidden = true; undo(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), undo ? 6000 : 3000);
}

function fail(err) { toast(err.message || String(err)); }

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const now = new Date();
  return `${MONTHS[m - 1]} ${d}${y !== now.getFullYear() ? ", " + y : ""}`;
}

function whenText(days) {
  if (days < -1) return `${-days} days overdue`;
  if (days === -1) return "1 day overdue";
  if (days === 0) return "Due today";
  if (days === 1) return "Tomorrow";
  return `In ${days} days`;
}

function thumb(photo, name, href) {
  const inner = photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : "🪴";
  return href ? `<a class="thumb" href="${href}" aria-label="${esc(name)}">${inner}</a>` : `<span class="thumb">${inner}</span>`;
}

// ---------------------------------------------------------------- modal

function openModal(html, onOpen) {
  const modal = $("#modal");
  $("#sheet").innerHTML = html;
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  $$("[data-close]", modal).forEach((b) => (b.onclick = closeModal));
  if (onOpen) onOpen($("#sheet"));
  const first = $("input:not([type=hidden]):not([type=checkbox]):not([type=file]), select, textarea", $("#sheet"));
  if (first && window.innerWidth > 650) first.focus();
}

function closeModal() {
  $("#modal").hidden = true;
  $("#sheet").innerHTML = "";
  document.body.style.overflow = "";
}

$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });

// ---------------------------------------------------------------- auth

async function showAuth() {
  $("#top").hidden = true;
  const status = await api("/api/status", { allow401: true });
  const setup = status.setup_required;
  let useToken = false;
  const renderForm = () => {
    $("#view").innerHTML = `
      <form class="card auth stack" id="auth-form">
        <div class="auth-logo"><img src="/static/icon.svg" alt="" width="40" height="40">${esc(status.app_name)}</div>
        <h1>${setup ? "Set up Plants" : "Sign in"}</h1>
        ${setup ? '<p class="muted">Create the first account. It becomes the administrator.</p>' : ""}
        ${useToken ? `<div><label for="token">API token</label><input id="token" name="token" type="password" autocomplete="off" required></div>` : `
          <div><label for="username">Username</label><input id="username" name="username" autocomplete="username" required></div>
          <div><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="${setup ? "new-password" : "current-password"}" required minlength="8"></div>`}
        <p class="error" id="auth-error"></p>
        <button class="primary" type="submit">${setup ? "Create administrator" : "Sign in"}</button>
        ${!setup && status.token_login_enabled ? `<button class="ghost" id="auth-mode" type="button">${useToken ? "Use username and password" : "Use API token"}</button>` : ""}
      </form>`;
    if ($("#auth-mode")) $("#auth-mode").onclick = () => { useToken = !useToken; renderForm(); };
    $("#auth-form").onsubmit = async (e) => {
      e.preventDefault();
      const body = useToken ? { token: $("#token").value } :
        { username: $("#username").value, password: $("#password").value };
      try {
        await api(setup ? "/api/setup" : "/api/login", { method: "POST", body, allow401: true });
        await boot();
      } catch (err) {
        $("#auth-error").textContent = err.message;
      }
    };
  };
  renderForm();
}

$("#logout").onclick = async () => {
  await pushOff().catch(() => {}); // a shared device shouldn't keep getting the last person's reminders
  await api("/api/logout", { method: "POST", allow401: true });
  state.me = null;
  location.hash = "#/";
  showAuth();
};

// ---------------------------------------------------------------- routing

async function boot() {
  try {
    state.me = await api("/api/me", { allow401: true });
  } catch {
    return showAuth();
  }
  state.settings = await api("/api/settings");
  $("#top").hidden = false;
  $("#app-name").textContent = state.settings.app_name;
  document.title = state.settings.app_name;
  $("#user-badge").textContent = state.me.username;
  route();
}

async function route() {
  if (!state.me) return;
  closeModal();
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/plant\/(\d+)/);
  const tab = m || hash.startsWith("#/plants") ? "plants" : hash.startsWith("#/settings") ? "settings" : "today";
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  try {
    if (m) await renderPlant(Number(m[1]));
    else if (tab === "plants") await renderPlants();
    else if (tab === "settings") await renderSettings();
    else await renderToday();
  } catch (err) {
    if (!(err instanceof ApiError && err.message === "Please sign in again")) fail(err);
  }
}

window.addEventListener("hashchange", () => { window.scrollTo(0, 0); route(); });

async function refresh() { await route(); }

// ---------------------------------------------------------------- today

function weatherHtml(w) {
  if (!w || !w.configured) {
    return `<section class="card weather" id="weather"><div class="muted">
      Local weather shows here once a location is set.
      ${state.me.is_admin ? '<a href="#/settings">Set location in Settings</a>' : "Ask an administrator to set it in Settings."}</div></section>`;
  }
  if (w.error) return `<section class="card weather" id="weather"><div class="muted">${esc(w.location)}: ${esc(w.error)}</div></section>`;
  const u = w.units;
  const c = w.current;
  const days = w.daily.map((d) => `
    <div class="wx-day"><b>${esc(d.short)}</b>${Math.round(d.high)}° / ${Math.round(d.low)}°
    <div class="wx-rain">${d.precip_probability ?? 0}%<span class="wx-amt"> · ${Number(d.precip || 0).toFixed(2)} ${esc(u.precipitation)}</span></div>
    <div class="muted">${esc(d.summary)}</div></div>`).join("");
  return `<section class="card weather" id="weather">
    <div class="wx-now">
      <div class="wx-temp">${Math.round(c.temperature)}${esc(u.temperature)}</div>
      <div><b>${esc(w.location || "Weather")}</b><div>${esc(c.summary)}</div>
      <div class="muted">Humidity ${c.humidity}% · Rain ${c.precipitation} ${esc(u.precipitation)} now · Wind ${Math.round(c.wind)} ${esc(u.wind)}</div></div>
    </div>
    <div class="wx-days">${days}</div>
    ${w.hint ? `<p class="wx-hint">${esc(w.hint)}</p>` : ""}
    <div class="wx-foot">Next 5 days: ${w.rain_total} ${esc(u.precipitation)} of rain expected. Weather never changes your schedules.
      <a href="https://open-meteo.com/" target="_blank" rel="noopener">Weather data by Open-Meteo.com</a>${w.stale ? " (showing cached data)" : ""}</div>
  </section>`;
}

function dueRow(i) {
  const sel = state.selected.has(i.task_id);
  return `<div class="due ${sel ? "sel" : ""}" data-task="${i.task_id}">
    <label class="checkwrap" aria-label="Select ${esc(i.plant_name)}"><input class="check" type="checkbox" ${sel ? "checked" : ""} data-select="${i.task_id}"></label>
    ${thumb(i.photo, i.plant_name, `#/plant/${i.plant_id}`)}
    <div class="due-main">
      <a class="due-name" href="#/plant/${i.plant_id}">${esc(i.plant_name)}</a>
      <div class="due-when ${i.state}">${KIND_ICON[i.kind] || ""} ${esc(i.label)} · ${whenText(i.days)}</div>
      <div class="due-meta">${esc([i.room, i.outdoor ? "Outdoor" : ""].filter(Boolean).join(" · "))}${i.room || i.outdoor ? " · " : ""}${esc(i.reason)}</div>
    </div>
    <div class="due-actions">
      <button class="primary" data-act="done" data-id="${i.task_id}">Done</button>
      <button data-act="snooze" data-id="${i.task_id}" data-plant="${i.plant_id}" data-plantdue="${i.plant_due || 0}" data-name="${esc(i.plant_name)}">Snooze</button>
      <button data-act="skip" data-id="${i.task_id}" title="Checked it, doesn't need it yet">Skip</button>
    </div>
  </div>`;
}

async function renderToday() {
  const [due, weather] = await Promise.all([
    api("/api/due?days=7"),
    api("/api/weather").catch(() => ({ configured: true, error: "Weather unavailable right now." })),
  ]);
  const rooms = [...new Set(due.items.map((i) => i.room || "No room"))].sort();
  if (state.roomFilter !== "all" && !rooms.includes(state.roomFilter)) state.roomFilter = "all";
  const dueByPlant = {};
  due.items.forEach((i) => { if (i.days <= 0) dueByPlant[i.plant_id] = (dueByPlant[i.plant_id] || 0) + 1; });
  due.items.forEach((i) => (i.plant_due = dueByPlant[i.plant_id] || 0));
  const items = due.items.filter((i) => state.roomFilter === "all" || (i.room || "No room") === state.roomFilter);
  const visible = new Set(items.map((i) => i.task_id));
  state.selected = new Set([...state.selected].filter((id) => visible.has(id)));
  const groups = [
    ["overdue", "Overdue", items.filter((i) => i.state === "overdue")],
    ["today", "Today", items.filter((i) => i.state === "today")],
    ["upcoming", "Next 7 days", items.filter((i) => i.state === "upcoming")],
  ];
  const needNow = groups[0][2].length + groups[1][2].length;
  $("#view").innerHTML = `
    ${weatherHtml(weather)}
    <div class="pagehead"><div><h1>Today</h1>
      <div class="muted">${needNow ? `${needNow} thing${needNow === 1 ? "" : "s"} to check. Feel the soil first; Skip if it's still moist.` : "Nothing due. Nice."}</div></div>
    </div>
    ${rooms.length > 1 ? `<div class="chips" role="group" aria-label="Filter by room">
      <button class="chip ${state.roomFilter === "all" ? "active" : ""}" data-room="all">All rooms</button>
      ${rooms.map((r) => `<button class="chip ${state.roomFilter === r ? "active" : ""}" data-room="${esc(r)}">${esc(r)}</button>`).join("")}
    </div>` : ""}
    ${groups.map(([cls, title, list]) => list.length ? `
      <h2 class="section-title ${cls}">${title} <span class="count">${list.length}</span>
        <button class="small ghost" data-selectgroup="${cls}">Select all</button></h2>
      <div class="due-list">${list.map(dueRow).join("")}</div>` : "").join("")}
    ${items.length ? "" : `<div class="card empty">Nothing due in the next week${state.roomFilter !== "all" ? " in this room" : ""}.<br><a href="#/plants">See all plants</a></div>`}
    <div class="batchbar" id="batchbar" ${state.selected.size ? "" : "hidden"}>
      <b id="batch-count"></b>
      <button class="primary" data-batch="done">Done</button>
      <button data-batch="snooze">Snooze</button>
      <button data-batch="skip">Skip</button>
      <button class="ghost" data-batch="clear">Clear</button>
    </div>`;
  updateBatchBar();
  $$("[data-room]").forEach((b) => (b.onclick = () => { state.roomFilter = b.dataset.room; state.selected.clear(); renderToday(); }));
  $$("[data-select]").forEach((cb) => (cb.onchange = () => {
    const id = Number(cb.dataset.select);
    cb.checked ? state.selected.add(id) : state.selected.delete(id);
    cb.closest(".due").classList.toggle("sel", cb.checked);
    updateBatchBar();
  }));
  $$("[data-selectgroup]").forEach((b) => (b.onclick = () => {
    const list = groups.find((g) => g[0] === b.dataset.selectgroup)[2];
    list.forEach((i) => state.selected.add(i.task_id));
    renderToday();
  }));
  bindCareButtons($("#view"), renderToday);
  $$("[data-batch]").forEach((b) => (b.onclick = () => batchAction(b.dataset.batch)));
}

function updateBatchBar() {
  const bar = $("#batchbar");
  if (!bar) return;
  bar.hidden = state.selected.size === 0;
  $("#batch-count").textContent = `${state.selected.size} selected`;
}

async function batchAction(action) {
  if (action === "clear") { state.selected.clear(); return renderToday(); }
  const ids = [...state.selected];
  const run = async (extra = {}) => {
    try {
      await api("/api/care/batch", { method: "POST", body: { task_ids: ids, action, ...extra } });
      state.selected.clear();
      toast(`${ids.length} marked ${action === "done" ? "done" : action === "skip" ? "skipped" : "snoozed"}`);
      renderToday();
    } catch (err) { fail(err); }
  };
  if (action === "snooze") return snoozeMenu((days) => run({ days }));
  run();
}

function bindCareButtons(root, after) {
  $$("[data-act]", root).forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    const id = Number(b.dataset.id);
    const act = b.dataset.act;
    if (act === "snooze") {
      const whole = Number(b.dataset.plantdue) > 1 ? { plant: Number(b.dataset.plant), name: b.dataset.name, count: Number(b.dataset.plantdue) } : null;
      return snoozeMenu((days, all) => (all ? snoozePlant(whole.plant, days, after) : care(id, "snooze", { days }, after)), whole);
    }
    if (act === "unsnooze") return unsnooze(id, after);
    if (act === "copylink") return copyQuickLink(b.dataset.link);
    if (act === "log") return logCareModal(id, after);
    care(id, act, {}, after);
  }));
}

async function care(taskId, action, body, after) {
  try {
    await api(`/api/tasks/${taskId}/${action}`, { method: "POST", body });
    toast(action === "done" ? "Logged" : action === "skip" ? "Skipped until next cycle" : `Snoozed ${body.days} day${body.days === 1 ? "" : "s"}`);
    await after();
  } catch (err) { fail(err); }
}

async function snoozePlant(plantId, days, after) {
  try {
    const res = await api(`/api/plants/${plantId}/snooze`, { method: "POST", body: { days } });
    toast(`Snoozed ${res.count} task${res.count === 1 ? "" : "s"} for ${days} day${days === 1 ? "" : "s"}`);
    await after();
  } catch (err) { fail(err); }
}

async function unsnooze(taskId, after) {
  try { await api(`/api/tasks/${taskId}/unsnooze`, { method: "POST" }); toast("Snooze cleared"); await after(); } catch (err) { fail(err); }
}

function absoluteLink(link) {
  return /^https?:\/\//.test(link) ? link : location.origin + link;
}

async function copyQuickLink(link) {
  const url = absoluteLink(link);
  try {
    await navigator.clipboard.writeText(url);
    toast("Quick link copied. Anyone with it can log this one task.");
  } catch {
    prompt("Copy this quick link. Anyone with it can log this one task.", url);
  }
}

// whole: {plant, name, count} offers "snooze everything due on this plant"; pick(days, all)
function snoozeMenu(pick, whole = null) {
  openModal(`<h2>Snooze</h2><div class="menu">
      ${whole ? `<label class="toggle"><input type="checkbox" id="snooze-all"> All ${whole.count} due tasks for ${esc(whole.name)}</label>` : ""}
      <button data-days="1">1 day</button><button data-days="3">3 days</button><button data-days="7">7 days</button>
      <div class="row"><input type="number" id="snooze-days" min="1" max="365" placeholder="Custom days" inputmode="numeric" style="flex:1">
      <button class="primary" id="snooze-custom">Snooze</button></div>
    </div><div class="sheet-actions"><button data-close>Cancel</button></div>`, (sheet) => {
    const all = () => Boolean($("#snooze-all", sheet)?.checked);
    $$("[data-days]", sheet).forEach((b) => (b.onclick = () => { const a = all(); closeModal(); pick(Number(b.dataset.days), a); }));
    $("#snooze-custom", sheet).onclick = () => {
      const d = Number($("#snooze-days", sheet).value);
      if (d >= 1 && d <= 365) { const a = all(); closeModal(); pick(d, a); } else toast("Enter 1 to 365 days");
    };
  });
}

function logCareModal(taskId, after) {
  openModal(`<h2>Log care</h2><form id="log-form" class="stack">
      <div><label for="log-date">Date</label><input type="date" id="log-date" value="${localToday()}" max="${localToday()}" required></div>
      <div><label for="log-note">Note (optional)</label><textarea id="log-note" maxlength="1000" placeholder="Soil was bone dry, gave it a soak"></textarea></div>
      <div class="sheet-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`, (sheet) => {
    $("#log-form", sheet).onsubmit = (e) => {
      e.preventDefault();
      const body = { date: $("#log-date").value, note: $("#log-note").value };
      closeModal();
      care(taskId, "done", body, after);
    };
  });
}

// ---------------------------------------------------------------- plants list

async function renderPlants() {
  const plants = await api("/api/plants");
  const rooms = [...new Set(plants.map((p) => p.room || "No room"))].sort();
  if (state.plantRoom !== "all" && !rooms.includes(state.plantRoom)) state.plantRoom = "all";
  $("#view").innerHTML = `
    <div class="pagehead"><div><h1>Plants</h1><div class="muted" id="plant-count"></div></div>
      <button class="primary" id="add-plant">Add plant</button></div>
    <div class="toolbar"><input type="search" id="plant-search" placeholder="Search plants" value="${esc(state.search)}" aria-label="Search plants"></div>
    ${rooms.length > 1 ? `<div class="chips" role="group" aria-label="Filter by room">
      <button class="chip ${state.plantRoom === "all" ? "active" : ""}" data-proom="all">All</button>
      ${rooms.map((r) => `<button class="chip ${state.plantRoom === r ? "active" : ""}" data-proom="${esc(r)}">${esc(r)}</button>`).join("")}</div>` : ""}
    <div class="grid" id="plant-grid"></div>`;
  const draw = () => {
    const q = state.search.trim().toLowerCase();
    const list = plants.filter((p) =>
      (state.plantRoom === "all" || (p.room || "No room") === state.plantRoom) &&
      (!q || [p.name, p.species, p.room].join(" ").toLowerCase().includes(q)));
    $("#plant-count").textContent = `${list.length} of ${plants.length} plant${plants.length === 1 ? "" : "s"}`;
    $("#plant-grid").innerHTML = list.length ? list.map((p) => {
      const n = p.next;
      return `<a class="plant-card" href="#/plant/${p.id}">${thumb(p.photo, p.name)}
        <div class="pc-body"><div class="pc-name">${esc(p.name)}</div>
        <div class="muted">${esc([p.species, p.room].filter(Boolean).join(" · ") || "No details yet")}</div>
        ${n ? `<div class="due-when ${n.state}">${KIND_ICON[n.kind] || ""} ${esc(n.label)} · ${whenText(n.days)}</div>` : '<div class="muted">No care tasks</div>'}
        </div></a>`;
    }).join("") : `<div class="card empty">${plants.length ? "No plants match." : "No plants yet. Add your first one."}</div>`;
  };
  draw();
  $("#plant-search").oninput = (e) => { state.search = e.target.value; draw(); };
  $$("[data-proom]").forEach((b) => (b.onclick = () => { state.plantRoom = b.dataset.proom; renderPlants(); }));
  $("#add-plant").onclick = () => plantForm(null);
}

// ---------------------------------------------------------------- plant detail

async function renderPlant(id) {
  const [p, events] = await Promise.all([api(`/api/plants/${id}`), api(`/api/plants/${id}/events`)]);
  const facts = [
    ["Species", p.species], ["Room", p.room], ["Light", p.light], ["Pot", [p.pot_size, p.pot_material].filter(Boolean).join(", ")],
    ["Acquired", fmtDate(p.acquired)], ["Where", p.outdoor ? "Outdoor" : "Indoor"], ["Added by", p.added_by],
  ].filter(([, v]) => v);
  const actLabel = { done: "Done", skip: "Skipped", snooze: "Snoozed", photo: "Photo", note: "Note" };
  const dueNow = p.tasks.filter((t) => t.days <= 0).length;
  const photos = events.filter((e) => e.photo).sort((a, b) => (a.date === b.date ? a.id - b.id : a.date < b.date ? -1 : 1));
  const growth = feature("growth_timeline");
  $("#view").innerHTML = `
    <div class="hero">
      <div class="hero-photo">${p.photo ? `<img src="${esc(p.photo)}" alt="${esc(p.name)}">` : "🪴"}</div>
      <div>
        <p><a href="#/plants">← All plants</a></p>
        <h1>${esc(p.name)}</h1>
        <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${k}</span>${esc(v)}</div>`).join("")}</div>
        <div class="row">
          <button id="edit-plant">Edit</button>
          <button id="add-photo">Add photo or note</button>
          <button id="dup-plant">Duplicate</button>
          ${dueNow ? `<button id="snooze-plant">Snooze plant</button>` : ""}
        </div>
      </div>
    </div>
    ${p.notes || p.care_source ? `<section class="card settings-section"><h2 style="margin-top:0">Care notes</h2>
      ${p.notes ? `<div class="notes">${esc(p.notes)}</div>` : ""}
      ${p.care_source ? `<p class="hint">Source: <a href="${esc(p.care_source)}" target="_blank" rel="noopener">${esc(p.care_source.replace(/^https?:\/\//, "").split("/")[0])}</a></p>` : ""}
    </section>` : ""}
    <section class="card settings-section">
      <div class="pagehead"><h2 style="margin:0">Care tasks</h2><button class="small" id="add-task">Add task</button></div>
      <div id="tasks">${p.tasks.length ? p.tasks.map((t) => `
        <div class="task">
          <div><b>${KIND_ICON[t.kind] || ""} ${esc(t.label)}</b> <span class="due-when ${t.state}">· ${whenText(t.days)}</span>
            ${t.snoozed ? `<span class="badge snoozed">💤 Snoozed to ${fmtDate(t.snoozed_until)}</span>` : ""}
            <div class="due-meta">${esc(t.reason)} · next ${fmtDate(t.next_due)}</div></div>
          <div class="row">
            <button class="primary" data-act="done" data-id="${t.id}">Done</button>
            <button data-act="log" data-id="${t.id}" title="Log with a date or note">Log…</button>
            ${t.snoozed ? `<button data-act="unsnooze" data-id="${t.id}">Unsnooze</button>`
              : `<button data-act="snooze" data-id="${t.id}" data-plant="${p.id}" data-plantdue="${t.days <= 0 ? dueNow : 0}" data-name="${esc(p.name)}">Snooze</button>`}
            <button data-act="skip" data-id="${t.id}">Skip</button>
            ${t.quick_links ? `<button class="ghost" data-act="copylink" data-link="${esc(t.quick_links.done)}" title="One-tap link for a text message">Copy link</button>` : ""}
            <button class="ghost" data-edit-task="${t.id}" aria-label="Edit ${esc(t.label)} task">Edit</button>
          </div>
        </div>`).join("") : '<p class="muted">No care tasks yet.</p>'}</div>
    </section>
    ${growth ? `<section class="card settings-section" id="growth">
      <div class="pagehead"><h2 style="margin:0">Growth</h2><span class="muted">${photos.length} photo${photos.length === 1 ? "" : "s"}</span></div>
      ${photos.length ? `<div class="growth-strip">${photos.map((e, i) => `
        <button type="button" class="growth-item" data-growth="${i}" aria-label="Photo from ${fmtDate(e.date)}">
          <img src="${esc(e.photo)}" alt="" loading="lazy">
          <span class="growth-date">${fmtDate(e.date)}</span>
          <span class="growth-age">${growthAge(photos[0].date, e.date, i)}</span>
        </button>`).join("")}</div>
        ${photos.length === 1 ? '<p class="hint">Add a photo every few weeks and they line up here, oldest first, so you can watch it grow.</p>' : ""}`
        : '<p class="muted">Photos you add show up here oldest first, so you can watch it grow. Use Add photo or note.</p>'}
    </section>` : ""}
    <section class="card settings-section">
      <h2 style="margin-top:0">Timeline</h2>
      ${events.length ? `<ul class="timeline">${events.map((e) => `
        <li><div class="tl-date">${fmtDate(e.date)}</div>
          <div><b>${esc(e.action === "done" || e.action === "skip" || e.action === "snooze" ? `${e.label} · ${actLabel[e.action]}` : actLabel[e.action])}</b>
            <span class="muted">${e.user ? "by " + esc(e.user) : ""}${e.via ? " via " + esc(e.via) : ""}</span>
            ${e.note ? `<div class="notes">${esc(e.note)}</div>` : ""}
            ${e.photo ? `<a href="${esc(e.photo)}" target="_blank" rel="noopener"><img class="tl-photo" src="${esc(e.photo)}" alt="Photo from ${fmtDate(e.date)}" loading="lazy"></a>` : ""}
            ${e.photo && e.photo !== p.photo ? `<button class="small ghost" data-main="${e.id}">Use as main photo</button>` : ""}
          </div>
          <button class="icon-btn ghost danger" data-del-event="${e.id}" aria-label="Delete entry">✕</button></li>`).join("")}</ul>`
        : '<p class="muted">Care you log and photos you add show up here.</p>'}
    </section>
    <p><button class="danger" id="delete-plant">Delete plant</button></p>`;
  const again = () => renderPlant(id);
  bindCareButtons($("#view"), again);
  $("#edit-plant").onclick = () => plantForm(p);
  $("#add-photo").onclick = () => photoModal(p, again);
  $("#add-task").onclick = () => taskForm(p, null, again);
  $$("[data-edit-task]").forEach((b) => (b.onclick = () => taskForm(p, p.tasks.find((t) => t.id === Number(b.dataset.editTask)), again)));
  if ($("#snooze-plant")) $("#snooze-plant").onclick = () => snoozeMenu((days) => snoozePlant(id, days, again));
  $$("[data-growth]").forEach((b) => (b.onclick = () => growthViewer(p, photos, Number(b.dataset.growth))));
  $("#dup-plant").onclick = async () => {
    try {
      const copy = await api(`/api/plants/${id}/duplicate`, { method: "POST" });
      toast("Duplicated");
      location.hash = `#/plant/${copy.id}`;
    } catch (err) { fail(err); }
  };
  $$("[data-main]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/plants/${id}/main-photo?event_id=${b.dataset.main}`, { method: "POST" }); again(); } catch (err) { fail(err); }
  }));
  $$("[data-del-event]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Delete this timeline entry?")) return;
    try { await api(`/api/events/${b.dataset.delEvent}`, { method: "DELETE" }); toast("Entry deleted"); again(); } catch (err) { fail(err); }
  }));
  $("#delete-plant").onclick = async () => {
    if (!confirm(`Delete ${p.name} and its whole history and photos?`)) return;
    try { await api(`/api/plants/${id}`, { method: "DELETE" }); toast("Plant deleted"); location.hash = "#/plants"; } catch (err) { fail(err); }
  };
}

function isoDays(a, b) {
  const [y1, m1, d1] = a.split("-").map(Number);
  const [y2, m2, d2] = b.split("-").map(Number);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000);
}

function growthAge(first, date, index) {
  if (index === 0) return "First photo";
  const d = isoDays(first, date);
  if (d < 1) return "Same day";
  if (d < 60) return `+${d} day${d === 1 ? "" : "s"}`;
  if (d < 730) return `+${Math.round(d / 30.44)} months`;
  return `+${(d / 365.25).toFixed(1)} years`;
}

function growthViewer(p, photos, start) {
  let i = start;
  const draw = (sheet) => {
    const e = photos[i];
    $("#gv-body", sheet).innerHTML = `<img class="gv-photo" src="${esc(e.photo)}" alt="${esc(p.name)} on ${fmtDate(e.date)}">
      <p><b>${fmtDate(e.date)}</b> · ${growthAge(photos[0].date, e.date, i)} <span class="muted">(${i + 1} of ${photos.length})</span></p>
      ${e.note ? `<div class="notes">${esc(e.note)}</div>` : ""}`;
    $("#gv-prev", sheet).disabled = i === 0;
    $("#gv-next", sheet).disabled = i === photos.length - 1;
  };
  openModal(`<h2>Growth · ${esc(p.name)}</h2><div id="gv-body"></div>
    <div class="sheet-actions"><button id="gv-prev">← Older</button><button id="gv-next">Newer →</button><button data-close>Close</button></div>`, (sheet) => {
    $("#gv-prev", sheet).onclick = () => { if (i > 0) { i--; draw(sheet); } };
    $("#gv-next", sheet).onclick = () => { if (i < photos.length - 1) { i++; draw(sheet); } };
    draw(sheet);
  });
}

// ---------------------------------------------------------------- forms

let libraryCache = null;
async function getLibrary() {
  if (!libraryCache) libraryCache = await api("/api/library");
  return libraryCache;
}

// In the add-plant form, picking a task type fills its usual interval until you type your own.
function bindKindDefaults(root) {
  $$(".task-edit", root).forEach((r) => {
    const kind = $("[data-f=kind]", r), days = $("[data-f=interval_days]", r);
    if (kind.dataset.bound) return;
    kind.dataset.bound = "1";
    days.addEventListener("input", () => (days.dataset.touched = "1"));
    kind.addEventListener("change", () => { if (!days.dataset.touched) days.value = KIND_DAYS[kind.value]; });
  });
}

function taskRow(t = {}) {
  return `<div class="task-edit">
    <div class="kind"><label>Task</label><select data-f="kind">${KINDS.map(([k, l]) => `<option value="${k}" ${t.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div><label>Check every (days)</label><input data-f="interval_days" type="number" min="1" max="730" inputmode="numeric" value="${esc(t.interval_days ?? 7)}" required></div>
    <div><label>Winter (days)</label><input data-f="winter_interval_days" type="number" min="1" max="730" inputmode="numeric" value="${esc(t.winter_interval_days ?? "")}" placeholder="Same"></div>
    <div><label>Last done</label><input data-f="last_done" type="date" max="${localToday()}" value="${esc(t.last_done_new ?? "")}"></div>
    <button type="button" class="icon-btn ghost danger" data-rm aria-label="Remove task">✕</button>
  </div>`;
}

async function plantForm(p) {
  const rooms = await api("/api/rooms");
  const identify = await api("/api/identify").catch(() => ({ configured: false }));
  const isNew = !p;
  openModal(`<h2>${isNew ? "Add plant" : "Edit plant"}</h2>
    <form id="plant-form">
      <div class="stack" style="margin-bottom:12px"><div><label for="id-photo">Identify from a photo</label>
        ${identify.configured ? `<div class="row">
          <input id="id-photo" type="file" accept="image/*">
          <button type="button" class="small" id="id-go" disabled>Identify</button>
        </div>
        <p class="hint" id="id-hint">A sharp photo of leaves or flowers works best. The photo goes to Pl@ntNet just to identify; it isn't saved.</p>
        <div class="results" id="id-results"></div>` : `<p class="hint" id="id-hint">Photo identification isn't set up on this server.
          ${state.me.is_admin ? "Add PLANTS_PLANTNET_API_KEY to the compose file; see Photo identification in the readme." : "Ask an administrator to add a Pl@ntNet API key."}</p>`}
      </div></div>
      ${isNew ? `<div class="stack" style="margin-bottom:12px"><div><label for="lib">Start from the starter library (optional)</label>
        <select id="lib"><option value="">Blank plant</option></select>
        <p class="hint">Fills in suggested care you can change. Intervals are when to check the soil, not a strict watering schedule.</p></div></div>` : ""}
      <div class="form-grid">
        <div><label for="f-name">Name</label><input id="f-name" name="name" required maxlength="80" value="${esc(p?.name)}" placeholder="Kitchen pothos"></div>
        <div><label for="f-species">Species or type</label><input id="f-species" name="species" maxlength="120" value="${esc(p?.species)}" placeholder="From the plant tag"></div>
        <div><label for="f-room">Room</label><input id="f-room" name="room" list="room-list" maxlength="60" value="${esc(p?.room)}" placeholder="Living room">
          <datalist id="room-list">${rooms.map((r) => `<option value="${esc(r.name)}">`).join("")}</datalist></div>
        <div><label for="f-light">Light</label><input id="f-light" name="light" maxlength="120" value="${esc(p?.light)}" placeholder="Bright, indirect"></div>
        <div><label for="f-pot">Pot size</label><input id="f-pot" name="pot_size" maxlength="40" value="${esc(p?.pot_size)}" placeholder='6"'></div>
        <div><label for="f-mat">Pot material</label><input id="f-mat" name="pot_material" maxlength="40" value="${esc(p?.pot_material)}" placeholder="Terracotta"></div>
        <div><label for="f-acq">Acquired</label><input id="f-acq" name="acquired" type="date" value="${esc(p?.acquired)}"></div>
        <div><label>&nbsp;</label><label class="toggle"><input type="checkbox" name="outdoor" ${p?.outdoor ? "checked" : ""}> Outdoor plant</label></div>
        <div class="full"><label for="f-notes">Care notes</label><textarea id="f-notes" name="notes" maxlength="4000">${esc(p?.notes)}</textarea></div>
        <div class="full"><label for="f-src">Care source link (optional)</label><input id="f-src" name="care_source" type="url" maxlength="300" value="${esc(p?.care_source)}" placeholder="https://"></div>
      </div>
      ${isNew ? `<h2>Care tasks</h2><div id="task-rows">${taskRow({ kind: "water", interval_days: 7 })}</div>
        <div class="chips add-kinds" role="group" aria-label="Add a care task">${KINDS.filter(([k]) => k !== "water").map(([k, l]) => `<button type="button" class="chip" data-addkind="${k}">+ ${KIND_ICON[k]} ${l}</button>`).join("")}</div>` : ""}
      ${isNew ? `<div class="stack" style="margin-top:12px"><div><label for="f-photo">Photo (optional)</label><input id="f-photo" type="file" accept="image/*"></div></div>` : ""}
      <p class="error" id="form-error"></p>
      <div class="sheet-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">${isNew ? "Add plant" : "Save"}</button></div>
    </form>`, async (sheet) => {
    const form = $("#plant-form", sheet);
    const bindRm = () => $$("[data-rm]", sheet).forEach((b) => (b.onclick = () => b.closest(".task-edit").remove()));
    if (identify.configured) {
      const idHintText = "A sharp photo of leaves or flowers works best. The photo goes to Pl@ntNet just to identify; it isn't saved.";
      const file = $("#id-photo", sheet), go = $("#id-go", sheet), hint = $("#id-hint", sheet), results = $("#id-results", sheet);
      file.onchange = () => { go.disabled = !file.files[0]; results.innerHTML = ""; hint.textContent = idHintText; };
      go.onclick = async () => {
        if (!file.files[0]) return;
        go.disabled = true;
        go.textContent = "Identifying…";
        results.innerHTML = "";
        hint.textContent = idHintText;
        try {
          const fd = new FormData();
          fd.append("photo", file.files[0]);
          const res = await api("/api/identify", { method: "POST", form: fd });
          results.innerHTML = res.suggestions.length
            ? res.suggestions.map((s, i) => `<button type="button" data-id-pick="${i}">${s.common ? `<b>${esc(s.common)}</b> · ` : ""}<i>${esc(s.scientific)}</i><span class="muted"> · ${s.score}% match</span></button>`).join("")
            : '<p class="muted">No matches for that photo. Try a closer shot of leaves or flowers.</p>';
          $$("[data-id-pick]", results).forEach((b) => (b.onclick = () => {
            const s = res.suggestions[Number(b.dataset.idPick)];
            form.species.value = s.scientific;
            if (!form.name.value) form.name.value = s.common || s.scientific;
            results.innerHTML = "";
            const c = isNew && s.care;
            if (c) {
              const rows = [taskRow({ kind: "water", interval_days: c.water_days })];
              if (c.fertilize_days) rows.push(taskRow({ kind: "fertilize", interval_days: c.fertilize_days }));
              if (c.mist_days) rows.push(taskRow({ kind: "mist", interval_days: c.mist_days }));
              $("#task-rows").innerHTML = rows.join("");
              bindRm();
              bindKindDefaults(sheet);
              if (c.match === "species") {
                if (!form.light.value) form.light.value = c.light;
                if (!form.notes.value) form.notes.value = c.care;
                if (!form.care_source.value) form.care_source.value = c.source;
              }
              const extra = [c.fertilize_days ? `fertilize every ${c.fertilize_days}` : "", c.mist_days ? `mist every ${c.mist_days}` : ""].filter(Boolean).join(", ");
              hint.textContent = `Filled from ${s.scientific}. Suggested care from the ${c.basis}: check water every ${c.water_days} days${extra ? ", " + extra : ""}. Edit anything before saving.`;
            } else {
              hint.textContent = `Filled from ${s.scientific}. Edit anything before saving.`;
            }
            file.value = "";
            go.disabled = true;
          }));
        } catch (err) { hint.textContent = err.message; }
        go.textContent = "Identify";
        go.disabled = !file.files[0];
      };
    }
    if (isNew) {
      bindRm();
      $$("[data-addkind]", sheet).forEach((b) => (b.onclick = () => {
        const kind = b.dataset.addkind;
        $("#task-rows").insertAdjacentHTML("beforeend", taskRow({ kind, interval_days: KIND_DAYS[kind] }));
        bindRm();
        bindKindDefaults(sheet);
      }));
      bindKindDefaults(sheet);
      const lib = await getLibrary();
      $("#lib").insertAdjacentHTML("beforeend", lib.plants.map((x) => `<option value="${esc(x.key)}">${esc(x.name)} (${esc(x.species)})</option>`).join(""));
      $("#lib").onchange = () => {
        const x = lib.plants.find((l) => l.key === $("#lib").value);
        if (!x) return;
        if (!form.name.value) form.name.value = x.name;
        form.species.value = x.species;
        form.light.value = x.light;
        form.notes.value = x.care;
        form.care_source.value = x.source;
        const rows = [taskRow({ kind: "water", interval_days: x.water_days })];
        if (x.fertilize_days) rows.push(taskRow({ kind: "fertilize", interval_days: x.fertilize_days }));
        if (x.mist_days) rows.push(taskRow({ kind: "mist", interval_days: x.mist_days }));
        $("#task-rows").innerHTML = rows.join("");
        bindRm();
      };
    }
    form.onsubmit = async (e) => {
      e.preventDefault();
      const body = {
        name: form.name.value, species: form.species.value, room: form.room.value, light: form.light.value,
        pot_size: form.pot_size.value, pot_material: form.pot_material.value, acquired: form.acquired.value || null,
        outdoor: form.outdoor.checked, notes: form.notes.value, care_source: form.care_source.value,
      };
      if (isNew) {
        body.tasks = $$(".task-edit", sheet).map((r) => ({
          kind: $("[data-f=kind]", r).value,
          interval_days: Number($("[data-f=interval_days]", r).value),
          winter_interval_days: $("[data-f=winter_interval_days]", r).value ? Number($("[data-f=winter_interval_days]", r).value) : null,
          last_done: $("[data-f=last_done]", r).value || null,
        }));
      }
      try {
        const saved = await api(isNew ? "/api/plants" : `/api/plants/${p.id}`, { method: isNew ? "POST" : "PUT", body });
        const file = isNew && $("#f-photo").files[0];
        if (file) {
          const fd = new FormData();
          fd.append("photo", file);
          fd.append("main", "true");
          await api(`/api/plants/${saved.id}/events`, { method: "POST", form: fd });
        }
        closeModal();
        toast(isNew ? "Plant added" : "Saved");
        if (isNew) location.hash = `#/plant/${saved.id}`; else refresh();
      } catch (err) { $("#form-error").textContent = err.message; }
    };
  });
}

function taskForm(p, t, after) {
  const isNew = !t;
  openModal(`<h2>${isNew ? "Add task" : "Edit task"} · ${esc(p.name)}</h2>
    <form id="task-form" class="stack">
      <div class="form-grid">
        <div><label for="t-kind">Task</label><select id="t-kind">${KINDS.map(([k, l]) => `<option value="${k}" ${(t?.kind || "fertilize") === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div><label for="t-label">Custom name (optional)</label><input id="t-label" maxlength="40" value="${esc(t?.custom_label)}" placeholder="Rotate pot"></div>
        <div><label for="t-int">Check every (days)</label><input id="t-int" type="number" min="1" max="730" inputmode="numeric" required value="${esc(t?.interval_days ?? KIND_DAYS[t?.kind || "fertilize"])}"></div>
        <div><label for="t-win">Winter interval (days)</label><input id="t-win" type="number" min="1" max="730" inputmode="numeric" value="${esc(t?.winter_interval_days ?? "")}" placeholder="Use the season setting"></div>
        ${isNew ? `<div><label for="t-last">Last done (optional)</label><input id="t-last" type="date" max="${localToday()}"></div>` : ""}
      </div>
      <p class="hint">Winter months and the default winter stretch are set in Settings. Nothing changes a schedule behind your back.</p>
      <p class="error" id="task-error"></p>
      <div class="sheet-actions">
        ${isNew ? "" : '<button type="button" class="danger" id="t-del">Delete task</button>'}
        <button type="button" data-close>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`, (sheet) => {
    if (isNew) {
      let touched = false;
      $("#t-int", sheet).oninput = () => (touched = true);
      $("#t-kind", sheet).onchange = () => { if (!touched) $("#t-int", sheet).value = KIND_DAYS[$("#t-kind", sheet).value]; };
    }
    $("#task-form", sheet).onsubmit = async (e) => {
      e.preventDefault();
      const body = {
        kind: $("#t-kind").value, label: $("#t-label").value, interval_days: Number($("#t-int").value),
        winter_interval_days: $("#t-win").value ? Number($("#t-win").value) : null,
      };
      if (isNew) body.last_done = $("#t-last").value || null;
      try {
        await api(isNew ? `/api/plants/${p.id}/tasks` : `/api/tasks/${t.id}`, { method: isNew ? "POST" : "PUT", body });
        closeModal(); toast("Saved"); after();
      } catch (err) { $("#task-error").textContent = err.message; }
    };
    if (!isNew) $("#t-del", sheet).onclick = async () => {
      if (!confirm("Delete this task? Its history stays in the timeline.")) return;
      try { await api(`/api/tasks/${t.id}`, { method: "DELETE" }); closeModal(); after(); } catch (err) { fail(err); }
    };
  });
}

function photoModal(p, after) {
  openModal(`<h2>Add to timeline · ${esc(p.name)}</h2>
    <form id="photo-form" class="stack">
      <div><label for="ph-file">Photo</label><input id="ph-file" type="file" accept="image/*"></div>
      <div><label for="ph-note">Note</label><textarea id="ph-note" maxlength="1000" placeholder="New leaf unfurling"></textarea></div>
      <div><label for="ph-date">Date</label><input id="ph-date" type="date" value="${localToday()}" max="${localToday()}"></div>
      <label class="toggle"><input type="checkbox" id="ph-main" ${p.photo ? "" : "checked"}> Use as main photo</label>
      <p class="error" id="ph-error"></p>
      <div class="sheet-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`, (sheet) => {
    $("#photo-form", sheet).onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData();
      const file = $("#ph-file").files[0];
      if (file) fd.append("photo", file);
      fd.append("note", $("#ph-note").value);
      fd.append("date", $("#ph-date").value);
      fd.append("main", $("#ph-main").checked ? "true" : "false");
      try { await api(`/api/plants/${p.id}/events`, { method: "POST", form: fd }); closeModal(); toast("Added"); after(); }
      catch (err) { $("#ph-error").textContent = err.message; }
    };
  });
}

// ---------------------------------------------------------------- settings

async function renderSettings() {
  const admin = state.me.is_admin;
  const [settings, rooms, tokens, notify, users] = await Promise.all([
    api("/api/settings"), api("/api/rooms"), api("/api/tokens"),
    admin ? api("/api/notifications") : null, admin ? api("/api/users") : null,
  ]);
  state.settings = settings;
  const hours = (sel) => Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === sel ? "selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("");
  $("#view").innerHTML = `
    <h1>Settings</h1>
    ${admin ? `
    <section class="card settings-section stack">
      <h2 style="margin:0">General</h2>
      <div><label for="s-name">App name</label><div class="row"><input id="s-name" maxlength="60" value="${esc(settings.app_name)}" style="flex:1"><button id="s-name-save">Save</button></div></div>
      <div><label for="s-units">Units</label><select id="s-units"><option value="imperial" ${settings.units !== "metric" ? "selected" : ""}>°F, inches, mph</option><option value="metric" ${settings.units === "metric" ? "selected" : ""}>°C, mm, km/h</option></select></div>
    </section>
    <section class="card settings-section stack" id="features">
      <h2 style="margin:0">Features</h2>
      <p class="muted" style="margin:0">Turn the extras on or off for everyone. Turning one off hides it; nothing is deleted.</p>
      <label class="toggle"><input type="checkbox" data-feature="growth_timeline" ${settings.feature_growth_timeline ? "checked" : ""}> Growth timeline <span class="muted">· each plant's photos side by side, oldest first</span></label>
      <label class="toggle"><input type="checkbox" data-feature="care_suggestions" ${settings.feature_care_suggestions ? "checked" : ""}> Care suggestions <span class="muted">· photo ID pre-fills water and fertilize intervals</span></label>
      <label class="toggle"><input type="checkbox" data-feature="quick_links" ${settings.feature_quick_links ? "checked" : ""}> One-tap quick links <span class="muted">· log care from a notification without signing in</span></label>
      <label class="toggle"><input type="checkbox" data-feature="token_login" ${settings.feature_token_login ? "checked" : ""}> API token sign-in <span class="muted">· sign into the website with an existing API token</span></label>
      <p class="hint" style="margin:0">A quick link can only log or snooze its own task. Set "App address for links" under Notifications so the links in alerts work away from home. If a link ever ends up somewhere it shouldn't, reset them all.</p>
      <div><button class="small" id="ql-reset">Reset all quick links</button></div>
    </section>
    <section class="card settings-section stack">
      <h2 style="margin:0">Weather location</h2>
      <p class="muted" style="margin:0">Current: ${settings.weather_name ? esc(settings.weather_name) : "not set"}. Weather comes from Open-Meteo (free, no API key) and is refreshed every 30 minutes.</p>
      <div class="row"><input id="wx-q" placeholder="City or town" style="flex:1" aria-label="Search for a city"><button id="wx-search">Search</button></div>
      <div class="results" id="wx-results"></div>
      ${settings.weather_name ? '<button class="small ghost" id="wx-clear">Remove location</button>' : ""}
    </section>
    <section class="card settings-section stack">
      <h2 style="margin:0">Seasons</h2>
      <p class="muted" style="margin:0">Plants drink less in winter. Pick your winter months and how much to stretch check intervals then. A task's own winter interval wins over this.</p>
      <div class="row" id="s-months">${MONTHS.map((m, i) => `<label class="toggle" style="min-width:74px"><input type="checkbox" value="${i + 1}" ${settings.winter_months.includes(i + 1) ? "checked" : ""}> ${m}</label>`).join("")}</div>
      <div><label for="s-mult">Winter stretch</label><select id="s-mult">
        ${[1, 1.25, 1.5, 2].map((v) => `<option value="${v}" ${settings.winter_multiplier === v ? "selected" : ""}>${v === 1 ? "Off (same intervals)" : "x" + v}</option>`).join("")}</select></div>
      <button id="s-season-save">Save seasons</button>
    </section>
    <section class="card settings-section stack">
      <h2 style="margin:0">Notifications</h2>
      <p class="muted" style="margin:0">Uses <a href="https://github.com/caronc/apprise/wiki" target="_blank" rel="noopener">Apprise</a> URLs, one per line: ntfy, Gotify, Pushover, email, Discord, Telegram and more. Alerts say "check soil", never "water now".</p>
      <div><label for="n-urls">Apprise URLs</label><textarea id="n-urls" placeholder="ntfys://ntfy.example.com/plants&#10;pover://user@token">${esc(notify.notify_urls)}</textarea></div>
      <div class="form-grid">
        <div><label for="n-mode">Style</label><select id="n-mode"><option value="digest" ${notify.notify_mode === "digest" ? "selected" : ""}>One daily digest</option><option value="each" ${notify.notify_mode === "each" ? "selected" : ""}>One alert per plant</option></select></div>
        <div><label for="n-hour">Send from</label><select id="n-hour">${hours(notify.notify_hour)}</select></div>
        <div><label for="n-qs">Quiet hours start</label><select id="n-qs">${hours(notify.quiet_start)}</select></div>
        <div><label for="n-qe">Quiet hours end</label><select id="n-qe">${hours(notify.quiet_end)}</select></div>
        <div><label for="n-rep">Repeat overdue every (days, 0 = never)</label><input id="n-rep" type="number" min="0" max="30" inputmode="numeric" value="${notify.overdue_repeat_days}"></div>
        <div><label for="n-url">App address for links (optional)</label><input id="n-url" type="url" placeholder="https://plants.example.com" value="${esc(notify.public_url)}"></div>
      </div>
      <div class="row"><button id="n-save">Save notifications</button><button id="n-test">Send test</button></div>
    </section>` : ""}
    <section class="card settings-section stack" id="push-section">
      <h2 style="margin:0">Push notifications</h2>
      <p class="muted" style="margin:0">Get care reminders on this device, even when Plants is closed. They follow ${admin ? "the notification schedule above" : "the household's notification schedule"}: send-from hour, quiet hours, and overdue repeats.</p>
      <label class="toggle"><input type="checkbox" id="push-on" disabled> Push on this device</label>
      <p class="muted" id="push-status" style="margin:0">Checking this browser…</p>
      <div class="row" id="push-actions" hidden><button id="push-test">Send test push</button></div>
    </section>
    <section class="card settings-section">
      <h2 style="margin-top:0">Rooms</h2>
      <div id="rooms">${rooms.map((r) => `<div class="list-row"><span class="grow">${esc(r.name)} <span class="muted">· ${r.plants} plant${r.plants === 1 ? "" : "s"}</span></span>
        <button class="small" data-rename="${r.id}">Rename</button><button class="small danger" data-delroom="${r.id}">Delete</button></div>`).join("") || '<p class="muted">No rooms yet. Rooms are created when you type one on a plant.</p>'}</div>
      <div class="row" style="margin-top:10px"><input id="room-new" placeholder="New room" maxlength="60" style="flex:1" aria-label="New room name"><button id="room-add">Add room</button></div>
    </section>
    <section class="card settings-section">
      <h2 style="margin-top:0">API tokens</h2>
      <p class="muted">Let a script, home automation, or an AI assistant add plants from a tag photo, read what's due, and log care. <a href="/api/docs" target="_blank" rel="noopener">API docs</a></p>
      <div id="token-new"></div>
      <div>${tokens.map((t) => `<div class="list-row"><span class="grow"><b>${esc(t.name)}</b> <span class="muted">${esc(t.prefix)}… · ${t.last_used_at ? "used " + fmtDate(t.last_used_at) : "never used"}</span></span>
        <button class="small danger" data-revoke="${t.id}">Revoke</button></div>`).join("")}</div>
      <div class="row" style="margin-top:10px"><input id="tok-name" placeholder="Token name, e.g. Assistant" maxlength="60" style="flex:1" aria-label="Token name"><button id="tok-add">Create token</button></div>
    </section>
    ${admin ? `
    <section class="card settings-section">
      <h2 style="margin-top:0">Users</h2>
      <p class="muted">Everyone shares one set of plants. Each care entry records who logged it.</p>
      ${users.map((u) => `<div class="list-row"><span class="grow"><b>${esc(u.username)}</b> <span class="muted">${u.is_admin ? "Administrator" : "Member"}${u.active ? "" : " · disabled"}</span></span>
        ${u.id === state.me.id ? '<span class="muted">You</span>' : `<button class="small" data-uadmin="${u.id}" data-val="${u.is_admin ? 0 : 1}">${u.is_admin ? "Make member" : "Make admin"}</button>
        <button class="small" data-uactive="${u.id}" data-val="${u.active ? 0 : 1}">${u.active ? "Disable" : "Enable"}</button>`}
        <button class="small" data-upw="${u.id}">Password</button></div>`).join("")}
      <form class="form-grid" id="user-form" style="margin-top:10px">
        <div><label for="u-name">Username</label><input id="u-name" required autocomplete="off"></div>
        <div><label for="u-pw">Password</label><input id="u-pw" type="password" required minlength="8" autocomplete="new-password"></div>
        <label class="toggle"><input type="checkbox" id="u-admin"> Administrator</label>
        <div><button class="primary" type="submit">Add user</button></div>
      </form>
    </section>
    <section class="card settings-section stack">
      <h2 style="margin:0">Backup</h2>
      <p class="muted" style="margin:0">Export everything, photos included, as one JSON file. Importing replaces all data and signs everyone out.</p>
      <div class="row"><a class="btn" href="/api/export" download="plants-backup.json">Export backup</a>
        <label class="btn" style="margin:0;color:var(--text);font-size:15px">Import backup<input type="file" id="import" accept="application/json,.json" hidden></label></div>
    </section>` : ""}`;
  bindSettings(admin);
  initPushSettings(admin);
}

function bindSettings(admin) {
  const saveSettings = async (body, msg = "Saved") => {
    try { state.settings = await api("/api/settings", { method: "PUT", body }); toast(msg); } catch (err) { fail(err); }
  };
  if (admin) {
    $("#s-name-save").onclick = async () => { await saveSettings({ app_name: $("#s-name").value }); $("#app-name").textContent = state.settings.app_name; document.title = state.settings.app_name; };
    $("#s-units").onchange = () => saveSettings({ units: $("#s-units").value });
    $$("[data-feature]").forEach((cb) => (cb.onchange = () => saveSettings({ [`feature_${cb.dataset.feature}`]: cb.checked }, cb.checked ? "Turned on" : "Turned off")));
    $("#ql-reset").onclick = async () => {
      if (!confirm("Reset every quick link? Links already sent in old notifications stop working.")) return;
      try { const r = await api("/api/quick-links/reset", { method: "POST" }); toast(`Reset ${r.reset} link${r.reset === 1 ? "" : "s"}`); } catch (err) { fail(err); }
    };
    const search = async () => {
      const q = $("#wx-q").value.trim();
      if (q.length < 2) return;
      try {
        const res = await api(`/api/weather/search?q=${encodeURIComponent(q)}`);
        $("#wx-results").innerHTML = res.length ? res.map((r, i) => `<button data-wx="${i}">${esc(r.label)}</button>`).join("") : '<p class="muted">No matches.</p>';
        $$("[data-wx]").forEach((b) => (b.onclick = async () => {
          const r = res[Number(b.dataset.wx)];
          await saveSettings({ weather_name: r.label, weather_lat: r.latitude, weather_lon: r.longitude }, "Location saved");
          renderSettings();
        }));
      } catch (err) { fail(err); }
    };
    $("#wx-search").onclick = search;
    $("#wx-q").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } };
    if ($("#wx-clear")) $("#wx-clear").onclick = async () => { await saveSettings({ clear_weather: true }, "Location removed"); renderSettings(); };
    $("#s-season-save").onclick = () => saveSettings({
      winter_months: $$("#s-months input:checked").map((i) => Number(i.value)),
      winter_multiplier: Number($("#s-mult").value),
    });
    const notifyBody = () => ({
      notify_urls: $("#n-urls").value, notify_mode: $("#n-mode").value, notify_hour: Number($("#n-hour").value),
      quiet_start: Number($("#n-qs").value), quiet_end: Number($("#n-qe").value),
      overdue_repeat_days: Number($("#n-rep").value || 0), public_url: $("#n-url").value,
    });
    $("#n-save").onclick = async () => {
      try { await api("/api/notifications", { method: "PUT", body: notifyBody() }); toast("Notifications saved"); } catch (err) { fail(err); }
    };
    $("#n-test").onclick = async () => {
      try { await api("/api/notifications", { method: "PUT", body: notifyBody() }); await api("/api/notifications/test", { method: "POST" }); toast("Test sent"); } catch (err) { fail(err); }
    };
    $$("[data-uadmin]").forEach((b) => (b.onclick = () => updateUser(b.dataset.uadmin, { is_admin: b.dataset.val === "1" })));
    $$("[data-uactive]").forEach((b) => (b.onclick = () => updateUser(b.dataset.uactive, { active: b.dataset.val === "1" })));
    $$("[data-upw]").forEach((b) => (b.onclick = () => {
      const pw = prompt("New password (at least 8 characters)");
      if (pw) updateUser(b.dataset.upw, { password: pw });
    }));
    $("#user-form").onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api("/api/users", { method: "POST", body: { username: $("#u-name").value, password: $("#u-pw").value, is_admin: $("#u-admin").checked } });
        toast("User added"); renderSettings();
      } catch (err) { fail(err); }
    };
    $("#import").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file || !confirm("Replace ALL current data with this backup?")) return;
      try {
        const data = JSON.parse(await file.text());
        await api("/api/import", { method: "POST", body: data });
        toast("Backup restored. Sign in again.");
        state.me = null;
        showAuth();
      } catch (err) { fail(err); }
    };
  }
  $$("[data-rename]").forEach((b) => (b.onclick = async () => {
    const name = prompt("Room name");
    if (!name) return;
    try { await api(`/api/rooms/${b.dataset.rename}`, { method: "PUT", body: { name } }); renderSettings(); } catch (err) { fail(err); }
  }));
  $$("[data-delroom]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Delete this room? Its plants stay, just without a room.")) return;
    try { await api(`/api/rooms/${b.dataset.delroom}`, { method: "DELETE" }); renderSettings(); } catch (err) { fail(err); }
  }));
  $("#room-add").onclick = async () => {
    const name = $("#room-new").value.trim();
    if (!name) return;
    try { await api("/api/rooms", { method: "POST", body: { name } }); renderSettings(); } catch (err) { fail(err); }
  };
  $$("[data-revoke]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Revoke this token? Anything using it stops working.")) return;
    try { await api(`/api/tokens/${b.dataset.revoke}`, { method: "DELETE" }); renderSettings(); } catch (err) { fail(err); }
  }));
  $("#tok-add").onclick = async () => {
    const name = $("#tok-name").value.trim();
    if (!name) return toast("Name the token first");
    try {
      const t = await api("/api/tokens", { method: "POST", body: { name } });
      await renderSettings();
      $("#token-new").innerHTML = `<p><b>Copy this token now.</b> It won't be shown again.</p><div class="token-new">${esc(t.token)}</div>`;
    } catch (err) { fail(err); }
  };
}

async function updateUser(id, body) {
  try { await api(`/api/users/${id}`, { method: "PUT", body }); toast("User updated"); renderSettings(); } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- start

// ---------------------------------------------------------------- browser push

function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

// Empty string when this browser can do push; otherwise why not, in plain words.
function pushUnsupportedReason() {
  if (!window.isSecureContext) return "Push needs HTTPS. Open Plants at its https:// address (through your reverse proxy) to turn it on.";
  const has = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (isIOS() && !isStandalone()) return "On iPhone and iPad, add Plants to your Home Screen first (Share, then Add to Home Screen), open it from there, and turn push on in Settings.";
  if (!has) return "This browser doesn't support push notifications.";
  return "";
}

function b64urlToBytes(value) {
  const pad = "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob((value + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
}

function bytesToB64url(buf) {
  return btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function currentPushSubscription() {
  if (pushUnsupportedReason()) return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

async function pushOff() {
  const sub = await currentPushSubscription();
  if (!sub) return;
  await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint }, allow401: true }).catch(() => {});
  await sub.unsubscribe();
}

async function pushOn(publicKey) {
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  // A subscription made with old server keys can't receive pushes signed with new ones.
  if (sub && sub.options.applicationServerKey && bytesToB64url(sub.options.applicationServerKey) !== publicKey.replace(/=+$/, "")) {
    await sub.unsubscribe();
    sub = null;
  }
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(publicKey) });
  const json = sub.toJSON();
  return api("/api/push/subscribe", { method: "POST", body: { endpoint: json.endpoint, keys: json.keys } });
}

async function initPushSettings(admin) {
  const box = $("#push-on");
  const status = $("#push-status");
  const actions = $("#push-actions");
  if (!box) return;
  const show = (text, checked, enabled) => {
    status.textContent = text;
    box.checked = checked;
    box.disabled = !enabled;
    actions.hidden = !checked;
  };
  const reason = pushUnsupportedReason();
  if (reason) return show(reason, false, false);
  let sub = null;
  let info;
  try {
    sub = await currentPushSubscription();
    info = await api("/api/push" + (sub ? "?endpoint=" + encodeURIComponent(sub.endpoint) : ""));
  } catch (err) {
    return show("Couldn't check push status: " + (err.message || err), false, false);
  }
  if (!info.configured) {
    const why = info.problem ? ` (${info.problem})` : "";
    return show(admin
      ? `Push isn't set up on the server yet${why}. Add VAPID keys to your compose file; the readme shows how.`
      : `Push isn't set up on the server yet${why}. Ask an administrator to turn it on.`, false, false);
  }
  if (Notification.permission === "denied") {
    return show("Notifications are blocked for this site. Allow them in your browser's site settings, then reload.", false, false);
  }
  const others = (n) => (n > 0 ? ` You have push on ${n} other device${n === 1 ? "" : "s"}.` : "");
  const on = Boolean(sub && info.subscribed);
  if (on) show("On for this device." + (info.last_error ? ` Last delivery failed: ${info.last_error}.` : "") + others(info.devices - 1), true, true);
  else show("Off for this device." + others(info.devices), false, true);

  box.onchange = async () => {
    box.disabled = true;
    try {
      if (box.checked) {
        // Ask straight from the tap, before any other await, so browsers treat it as a user gesture.
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          show(permission === "denied"
            ? "Notifications are blocked for this site. Allow them in your browser's site settings, then reload."
            : "Permission wasn't granted, so push stays off.", false, permission !== "denied");
          return;
        }
        const res = await pushOn(info.public_key);
        show("On for this device." + others(res.devices - 1), true, true);
        toast("Push is on for this device");
      } else {
        await pushOff();
        const res = await api("/api/push");
        show("Off for this device." + others(res.devices), false, true);
        toast("Push is off for this device");
      }
    } catch (err) {
      show("Couldn't change push: " + (err.message || err), false, true);
    }
  };
  $("#push-test").onclick = async () => {
    try {
      const current = await currentPushSubscription();
      if (!current) throw new Error("This device isn't subscribed. Turn push on first.");
      await api("/api/push/test", { method: "POST", body: { endpoint: current.endpoint } });
      toast("Test push sent");
    } catch (err) {
      fail(err);
      initPushSettings(admin);
    }
  };
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
boot();
