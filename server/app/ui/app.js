// MineContext 管理画面（スマホ・iPad 向け）。sync-server の /v1 API だけを使う。
import { renderMarkdown } from "./md.js";
import { openShare } from "./share.js";
import { t, lang, LANGS, LOCALE, setLang, applyI18n, fmtMinutes } from "./i18n.js";

const TZ = "Asia/Tokyo";
const DEVICE_ID = "web-ui";
const $ = (id) => document.getElementById(id);

applyI18n();

const state = {
  day: todayStr(),
  filter: "all",
  timeline: null,
  active: null,
};

// ---------- 共通 ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try { const j = await res.json(); msg = j.detail ? JSON.stringify(j.detail) : msg; } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function todayStr() {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: TZ }).format(new Date());
}
function shiftDay(day, n) {
  const d = new Date(`${day}T12:00:00+09:00`);
  d.setUTCDate(d.getUTCDate() + n);
  return new Intl.DateTimeFormat("sv-SE", { timeZone: TZ }).format(d);
}
const fmtTime = (iso) => new Intl.DateTimeFormat(LOCALE[lang], { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));
const hourOf = (iso) => Number(new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", hourCycle: "h23" }).format(new Date(iso)));
function fmtDayLabel(day) {
  const d = new Date(`${day}T12:00:00+09:00`);
  const s = new Intl.DateTimeFormat(LOCALE[lang], { timeZone: TZ, month: "long", day: "numeric", weekday: "short" }).format(d);
  return day === todayStr() ? `${t("common.today")} · ${s}` : s;
}
function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}
// 日本時間の「その日の HH:MM」を ISO に
function isoFromDayTime(day, hhmm) {
  return new Date(`${day}T${hhmm}:00+09:00`).toISOString();
}
function hhmm(iso) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k);
  return e;
}
let toastTimer;
function toast(msg) {
  const box = $("toast");
  box.textContent = msg;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), 2600);
}

const END_REASON = {
  manual: "end.manual",
  ai_detected: "end.ai_detected",
  idle: "end.idle",
  superseded: "end.superseded",
};
const KIND_LABEL = {
  away: "kind.away",
  activity: "kind.activity",
  screen_summary: "kind.screenSummary",
  note: "kind.note",
  todo: "kind.todo",
  ai_work: "kind.aiWork",
  report: "kind.report",
  session: "kind.session",
};
const itemGroup = (it) => (it.type === "session" ? "session" : it.type === "ai_work" || it.event?.kind === "ai_work" ? "ai_work" : "mine");

// ---------- 状態 ----------
async function loadHealth() {
  const h = $("health");
  try {
    const j = await api("/healthz");
    h.className = "health " + (j.ok ? "ok" : "bad");
    h.querySelector(".label").textContent = j.ok ? (j.embed_pending ? t("health.pending", { n: j.embed_pending }) : t("health.ok")) : t("health.dbIssue");
    return j;
  } catch {
    h.className = "health bad";
    h.querySelector(".label").textContent = t("health.unreachable");
    return null;
  }
}

// ---------- 作業宣言 ----------
let elapsedTimer;
async function loadActive() {
  try {
    const j = await api("/v1/sessions/active");
    state.active = j.session;
  } catch {
    state.active = null;
  }
  renderActive();
}
function renderActive() {
  const s = state.active;
  $("active").hidden = !s;
  $("declare-label").textContent = s ? t("declare.promptNext") : t("declare.prompt");
  clearInterval(elapsedTimer);
  if (!s) return;
  $("active-title").textContent = s.title;
  $("active-meta").textContent = `${t("active.since", { time: fmtTime(s.started_at) })}${s.device_id ? ` · ${s.device_id}` : ""}${s.note ? ` · ${s.note}` : ""}`;
  const tick = () => ($("elapsed").textContent = fmtElapsed(Date.now() - new Date(s.started_at).getTime()));
  tick();
  elapsedTimer = setInterval(tick, 1000);
}
$("declare-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("declare-input");
  const title = input.value.trim();
  if (!title) return;
  try {
    const j = await api("/v1/sessions", { method: "POST", body: { title, device_id: DEVICE_ID } });
    input.value = "";
    toast(j.superseded ? t("toast.startSuperseded", { title: j.superseded.title }) : t("toast.started"));
    await refreshToday();
  } catch (err) {
    toast(t("toast.startFail", { err: err.message }));
  }
});
$("btn-end").addEventListener("click", async () => {
  if (!state.active) return;
  try {
    await api(`/v1/sessions/${state.active.id}/end`, { method: "POST", body: {} });
    toast(t("toast.ended"));
    await refreshToday();
  } catch (err) {
    toast(t("toast.endFail", { err: err.message }));
  }
});
$("btn-cancel").addEventListener("click", async () => {
  if (!state.active || !confirm(t("confirm.cancelSession", { title: state.active.title }))) return;
  try {
    await api(`/v1/sessions/${state.active.id}/cancel`, { method: "POST" });
    toast(t("toast.cancelled"));
    await refreshToday();
  } catch (err) {
    toast(t("toast.cancelFail", { err: err.message }));
  }
});

// ---------- タイムライン ----------
async function loadTimeline() {
  $("daylabel").textContent = fmtDayLabel(state.day);
  $("next-day").disabled = state.day >= todayStr();
  try {
    state.timeline = await api(`/v1/timeline?date=${state.day}&tz=${encodeURIComponent(TZ)}`);
  } catch (err) {
    state.timeline = { items: [], session_totals: [], total_minutes: 0 };
    toast(t("toast.loadFail", { err: err.message }));
  }
  renderTimeline();
}

// ---------- 横スクロールのタイムライン ----------
const ZOOMS = [30, 45, 60, 90, 120, 180, 240, 360]; // 1 時間あたりの px
let zoomIdx = Number(localStorage.getItem("mc.zoom") ?? 3);
let lanesScrolledFor = null;

function laneKey(it) {
  if (it.type === "session") return { key: "session", label: t("lane.work"), color: "var(--k-session)", order: 0 };
  if (it.type === "todo") return { key: "todo", label: t("lane.todo"), color: "var(--k-todo)", order: 0.5 };
  const ev = it.event;
  const name = ev.device_name || ev.device_id;
  if (ev.kind === "ai_work") return { key: `ai:${ev.device_id}`, label: t("lane.ai", { name }), color: "var(--k-ai)", order: 2 };
  return { key: `dev:${ev.device_id}`, label: name, color: "var(--k-mine)", order: 1 };
}

function renderLanes(items) {
  const pxh = ZOOMS[zoomIdx];
  const width = pxh * 24;
  const dayStart = new Date(`${state.day}T00:00:00+09:00`).getTime();
  const x = (iso) => Math.min(width, Math.max(0, ((new Date(iso).getTime() - dayStart) / 3600000) * pxh));

  const lanes = new Map([["session", { label: t("lane.work"), color: "var(--k-session)", order: 0, items: [] }]]);
  const todoItems = (state.timeline?.todo_entries || []).map((e) => ({ type: "todo", t: e.started_at, entry: e }));
  for (const it of [...items, ...todoItems]) {
    const k = laneKey(it);
    if (!lanes.has(k.key)) lanes.set(k.key, { ...k, items: [] });
    lanes.get(k.key).items.push(it);
  }
  const ordered = [...lanes.values()].sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));

  const labels = $("lane-labels");
  labels.replaceChildren(el("div", { class: "axis-gap" }),
    ...ordered.map((l) => el("div", { class: "lab", title: l.label }, el("span", { class: "sw", style: `background:${l.color}` }), l.label)));

  const canvas = $("lane-canvas");
  canvas.style.width = `${width}px`;
  const axis = el("div", { class: "axis" });
  const step = pxh >= 60 ? 1 : 2;
  for (let h = 0; h <= 24; h += step) axis.append(el("span", { style: `left:${h * pxh}px` }, `${h}:00`));
  const rows = ordered.map((l) => {
    const row = el("div", { class: "row-bg" });
    for (const it of l.items) row.append(laneMark(it, x));
    return row;
  });
  const grid = [];
  for (let h = 0; h <= 24; h++) grid.push(el("div", { class: "grid", style: `left:${h * pxh}px` }));
  if (pxh >= 120) for (let h = 0; h < 24; h++) grid.push(el("div", { class: "grid half", style: `left:${(h + 0.5) * pxh}px` }));
  const nowEl = state.day === todayStr() ? el("div", { class: "now", style: `left:${x(new Date().toISOString())}px` }) : null;
  canvas.replaceChildren(...grid, axis, ...rows, ...(nowEl ? [nowEl] : []));

  // 初回だけ、今（今日）か最初の記録の位置へスクロール
  if (lanesScrolledFor !== state.day) {
    lanesScrolledFor = state.day;
    if (state.day === todayStr()) scrollLanesTo(new Date().toISOString(), x, 0.7);
    else scrollLanesTo((items.find((i) => i.type === "session") || items[0])?.t, x, 0.1);
  }
}

function scrollLanesTo(iso, x, at = 0.7) {
  const sc = $("lane-scroll");
  if (!iso) { sc.scrollLeft = 0; return; }
  sc.scrollLeft = Math.max(0, x(iso) - sc.clientWidth * at);
}

function laneMark(it, x) {
  if (it.type === "session") {
    const s = it.session;
    const end = s.ended_at || (s.status === "active" ? new Date().toISOString() : s.started_at);
    const left = x(s.started_at);
    const cls = ["blk", s.status === "active" ? "live" : "", !s.confirmed && s.status === "ended" ? "unconfirmed" : ""].join(" ");
    return el("div", { class: cls, style: `left:${left}px;width:${Math.max(4, x(end) - left)}px`, onclick: () => showPop(it) }, s.title);
  }
  if (it.type === "todo") {
    const e = it.entry;
    const left = x(e.started_at);
    return el("div", { class: "blk todo", style: `left:${left}px;width:${Math.max(4, x(e.ended_at) - left)}px`, onclick: () => showPop(it) }, e.todo_title);
  }
  const ev = it.event;
  if (ev.ended_at && new Date(ev.ended_at) > new Date(ev.started_at)) {
    const left = x(ev.started_at);
    return el("div", { class: ev.kind === "away" ? "bar away" : "bar", style: `left:${left}px;width:${Math.max(6, x(ev.ended_at) - left)}px`, onclick: () => showPop(it) });
  }
  return el("div", { class: `dot ${ev.kind === "ai_work" ? "ai_work" : ""}`, style: `left:${x(ev.started_at)}px`, onclick: () => showPop(it) });
}

function showPop(it) {
  const pop = $("lane-pop");
  if (it.type === "session") {
    const s = it.session;
    pop.replaceChildren(
      el("div", { class: "t" }, s.title),
      el("div", { class: "s" }, `${fmtTime(s.started_at)}${t("common.dash")}${s.ended_at ? fmtTime(s.ended_at) : t("active.working")}${s.duration_minutes != null ? ` · ${fmtMinutes(s.duration_minutes)}` : ""}`),
      s.note ? el("div", { class: "b" }, s.note) : null,
    );
  } else if (it.type === "todo") {
    const e = it.entry;
    pop.replaceChildren(
      el("div", { class: "t" }, e.todo_title),
      el("div", { class: "s" }, `${fmtTime(e.started_at)}${t("common.dash")}${fmtTime(e.ended_at)} · ${fmtMinutes(e.minutes)} · ${t("pop.autoTracked")}`),
      el("div", { class: "b" }, el("button", { class: "btn small", onclick: () => deleteTodoEntry(e) }, t("pop.revertTime"))),
    );
  } else if (it.event?.kind === "away") {
    const ev = it.event;
    pop.replaceChildren(
      el("div", { class: "t" }, awayText(ev)),
      el("div", { class: "s" }, [ev.device_name || ev.device_id, awayReason(ev)].filter(Boolean).join(" · ")),
    );
  } else {
    const ev = it.event;
    pop.replaceChildren(
      el("div", { class: "t" }, ev.title || ev.app || (KIND_LABEL[ev.kind] ? t(KIND_LABEL[ev.kind]) : ev.kind)),
      el("div", { class: "s" }, `${fmtTime(ev.started_at)} · ${ev.device_name || ev.device_id} · ${KIND_LABEL[ev.kind] ? t(KIND_LABEL[ev.kind]) : ev.kind}`),
      el("div", { class: "b" }, ev.text),
    );
  }
  pop.hidden = false;
}

function setZoom(delta) {
  const sc = $("lane-scroll");
  const center = (sc.scrollLeft + sc.clientWidth / 2) / ZOOMS[zoomIdx]; // 中央の時刻（時間）
  zoomIdx = Math.min(ZOOMS.length - 1, Math.max(0, zoomIdx + delta));
  localStorage.setItem("mc.zoom", String(zoomIdx));
  renderLanes(state.timeline?.items || []);
  sc.scrollLeft = Math.max(0, center * ZOOMS[zoomIdx] - sc.clientWidth / 2);
}
$("zoom-in").addEventListener("click", () => setZoom(1));
$("zoom-out").addEventListener("click", () => setZoom(-1));
$("zoom-now").addEventListener("click", () => {
  if (state.day !== todayStr()) { state.day = todayStr(); loadTimeline(); return; }
  const pxh = ZOOMS[zoomIdx];
  const dayStart = new Date(`${state.day}T00:00:00+09:00`).getTime();
  scrollLanesTo(new Date().toISOString(), (iso) => ((new Date(iso).getTime() - dayStart) / 3600000) * pxh);
});

// ---------- 1か月のヒートマップ ----------
let heatMetric = localStorage.getItem("mc.heat") || "active_minutes";
let heatData = null;
const HEAT_UNIT = { active_minutes: "heat.unit.active", ai_active_minutes: "heat.unit.ai", session_minutes: "heat.unit.session" };

async function loadHeat() {
  const to = todayStr();
  const from = shiftDay(to, -34);
  try {
    const j = await api(`/v1/stats/daily?from=${from}&to=${to}&tz=${encodeURIComponent(TZ)}`);
    heatData = j.days;
    $("heat-note").textContent = "";
  } catch {
    heatData = null;
    $("heat-note").textContent = t("heat.loadFail");
  }
  renderHeat();
}

function renderHeat() {
  for (const b of $("heat-metric").children) b.classList.toggle("on", b.dataset.m === heatMetric);
  const box = $("heat");
  box.replaceChildren();
  if (!heatData) return;
  const today = todayStr();
  // 直近 5 週（月曜はじまり）
  const dow = (day) => (new Date(`${day}T12:00:00+09:00`).getUTCDay() + 6) % 7;
  const end = shiftDay(today, 6 - dow(today));
  const start = shiftDay(end, -34);
  const byDate = new Map(heatData.map((d) => [d.date, d]));
  const vals = heatData.map((d) => d[heatMetric] || 0).filter((v) => v > 0).sort((a, b) => a - b);
  const q = (p) => vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * vals.length))] : Infinity;
  const th = [q(0.25), q(0.5), q(0.75)];
  const level = (v) => (!v ? 0 : v <= th[0] ? 1 : v <= th[1] ? 2 : v <= th[2] ? 3 : 4);
  let total = 0;
  for (let i = 0; i < 35; i++) {
    const day = shiftDay(start, i);
    const d = byDate.get(day);
    const v = d ? Math.round(d[heatMetric] || 0) : 0;
    if (day <= today) total += v;
    const future = day > today;
    const lv = future ? 0 : level(v);
    const cls = ["c", `l${lv}`, day === today ? "today" : "", day === state.day ? "sel" : "", future ? "future" : ""].join(" ");
    const label = `${Number(day.slice(5, 7))}/${Number(day.slice(8))}`;
    const title = t("heat.cellTitle", { label, v, unit: t(HEAT_UNIT[heatMetric]) });
    box.append(el("button", {
      class: cls,
      title,
      "aria-label": title,
      disabled: future,
      onclick: () => { state.day = day; loadTimeline(); renderHeat(); window.scrollTo({ top: 0, behavior: "smooth" }); },
    }, el("span", { class: "n" }, String(Number(day.slice(8))))));
  }
  $("heat-title").textContent = t("heat.titleTotal", { dur: fmtMinutes(total) });
}
$("heat-metric").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  heatMetric = b.dataset.m;
  localStorage.setItem("mc.heat", heatMetric);
  renderHeat();
});

function sessionItem(s) {
  const reason = s.status === "active" ? el("span", { class: "badge live" }, t("active.working"))
    : s.status === "cancelled" ? el("span", { class: "badge" }, t("badge.cancelled"))
    : el("span", { class: `badge ${s.confirmed ? "ok" : "warn"}` }, s.confirmed ? t("badge.confirmed") : t("badge.unconfirmed", { reason: END_REASON[s.end_reason] ? t(END_REASON[s.end_reason]) : t("end.other") }));
  const range = `${fmtTime(s.started_at)}${t("common.dash")}${s.ended_at ? fmtTime(s.ended_at) : ""}`;
  const actions = el("div", { class: "actions" });
  if (s.status === "ended") {
    if (!s.confirmed) actions.append(el("button", { class: "btn small", onclick: () => confirmSession(s) }, t("btn.confirmAsIs")));
    actions.append(el("button", { class: "btn small ghost", onclick: () => openEdit(s) }, t("btn.edit")));
  }
  const evidence = (s.end_evidence || []).map((e) => e.reason).filter(Boolean);
  return el("li", { class: "item session" },
    el("div", { class: "time" }, fmtTime(s.started_at)),
    el("div", {},
      el("div", { class: "head" }, el("span", { class: "title" }, s.title), reason),
      el("div", { class: "sub" }, `${range}${s.duration_minutes != null ? ` · ${fmtMinutes(s.duration_minutes)}` : ""}${s.end_confidence != null ? ` · ${t("session.confidence", { pct: Math.round(s.end_confidence * 100) })}` : ""}`),
      s.note ? el("div", { class: "body" }, s.note) : null,
      evidence.length ? el("div", { class: "sub" }, t("session.evidence", { list: evidence.join(" / ") })) : null,
      actions.childElementCount ? actions : null,
    ),
  );
}

function awayText(ev) {
  const min = (new Date(ev.ended_at || ev.started_at) - new Date(ev.started_at)) / 60000;
  return t("away.range", { from: fmtTime(ev.started_at), to: ev.ended_at ? fmtTime(ev.ended_at) : "", dur: fmtMinutes(min) });
}
function awayReason(ev) {
  const r = ev.meta?.reason;
  return r && ["idle", "lock", "suspend"].includes(r) ? t(`away.reason.${r}`) : "";
}

function eventItem(ev, extra) {
  if (ev.kind === "away") {
    return el("li", { class: "item away" },
      el("div", { class: "time" }, fmtTime(ev.started_at)),
      el("div", {}, el("div", { class: "head" },
        el("span", { class: "title" }, awayText(ev)),
        el("span", { class: "sub" }, [ev.device_name || ev.device_id, awayReason(ev)].filter(Boolean).join(" · ")))),
    );
  }
  const g = ev.kind === "ai_work" ? "ai_work" : "mine";
  const body = el("div", { class: "body clamp", onclick: (e) => e.currentTarget.classList.toggle("clamp") }, ev.text);
  const who = ev.device_name || ev.device_id;
  return el("li", { class: `item ${g}` },
    el("div", { class: "time" }, fmtTime(ev.started_at)),
    el("div", {},
      el("div", { class: "head" },
        el("span", { class: "title" }, ev.title || ev.app || (KIND_LABEL[ev.kind] ? t(KIND_LABEL[ev.kind]) : ev.kind)),
        el("span", { class: "badge" }, KIND_LABEL[ev.kind] ? t(KIND_LABEL[ev.kind]) : ev.kind),
        extra,
      ),
      el("div", { class: "sub" }, [who, ev.app && ev.title ? ev.app : null].filter(Boolean).join(" · ")),
      body,
    ),
  );
}

function renderTimeline() {
  const tl = state.timeline;
  const items = tl.items || [];
  renderLanes(items);
  $("lane-pop").hidden = true;
  if (heatData) renderHeat();
  const shown = items.filter((it) => state.filter === "all" || itemGroup(it) === state.filter);
  const list = $("timeline");
  list.replaceChildren();
  let lastHour = -1;
  for (const it of shown) {
    const h = hourOf(it.t);
    if (h !== lastHour) {
      list.append(el("li", { class: "hour" }, t("common.hour", { h })));
      lastHour = h;
    }
    list.append(it.type === "session" ? sessionItem(it.session) : eventItem(it.event));
  }
  $("timeline-empty").hidden = shown.length > 0;

  $("total").textContent = fmtMinutes(tl.total_minutes);
  $("totals").replaceChildren(...(tl.session_totals || []).map((st) =>
    el("li", {}, el("span", { class: "t" }, st.title), el("span", { class: "m" }, fmtMinutes(st.minutes)))));
  if (!(tl.session_totals || []).length) $("totals").append(el("li", {}, el("span", { class: "t" }, t("stats.noDeclared"))));

  const counts = {};
  for (const it of items) {
    const k = it.type === "session" ? "session" : it.event?.kind || it.type;
    counts[k] = (counts[k] || 0) + 1;
  }
  $("counts").replaceChildren(...Object.entries(counts).map(([k, n]) =>
    el("li", {}, el("span", {}, KIND_LABEL[k] ? t(KIND_LABEL[k]) : k), el("span", { class: "m" }, t("common.count", { n })))));

  renderDevices(items);
}

async function confirmSession(s) {
  try {
    await api(`/v1/sessions/${s.id}`, { method: "PATCH", body: { confirmed: true } });
    toast(t("toast.confirmed"));
    await loadTimeline();
  } catch (err) {
    toast(t("toast.confirmFail", { err: err.message }));
  }
}

let editing = null;
function openEdit(s) {
  editing = s;
  $("edit-title").value = s.title;
  $("edit-note").value = s.note || "";
  $("edit-start").value = hhmm(s.started_at);
  $("edit-end").value = s.ended_at ? hhmm(s.ended_at) : "";
  $("edit-reason").textContent = s.end_reason ? t("edit.reason", { reason: END_REASON[s.end_reason] ? t(END_REASON[s.end_reason]) : s.end_reason }) : "";
  $("edit-dialog").showModal();
}
$("edit-dialog").addEventListener("close", async () => {
  if ($("edit-dialog").returnValue !== "save" || !editing) return;
  const s = editing;
  editing = null;
  const day = new Intl.DateTimeFormat("sv-SE", { timeZone: TZ }).format(new Date(s.started_at));
  const body = {
    title: $("edit-title").value.trim(),
    note: $("edit-note").value.trim() || null,
    started_at: isoFromDayTime(day, $("edit-start").value),
    confirmed: true,
  };
  if ($("edit-end").value) {
    let end = isoFromDayTime(day, $("edit-end").value);
    if (end <= body.started_at) end = isoFromDayTime(shiftDay(day, 1), $("edit-end").value);
    body.ended_at = end;
  }
  try {
    await api(`/v1/sessions/${s.id}`, { method: "PATCH", body });
    toast(t("toast.saved"));
    await loadTimeline();
  } catch (err) {
    toast(t("toast.saveFail", { err: err.message }));
  }
});

// ---------- 絞り込み・日付 ----------
$("filters").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  state.filter = b.dataset.kind;
  for (const c of $("filters").children) c.classList.toggle("on", c === b);
  renderTimeline();
});
$("prev-day").addEventListener("click", () => { state.day = shiftDay(state.day, -1); loadTimeline(); });
$("next-day").addEventListener("click", () => { if (state.day < todayStr()) { state.day = shiftDay(state.day, 1); loadTimeline(); } });
$("daylabel").addEventListener("click", () => { state.day = todayStr(); loadTimeline(); });

// ---------- 検索 ----------
$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("search-input").value.trim();
  if (!query) return;
  const kinds = [];
  if ($("search-mine").checked) kinds.push("activity", "screen_summary", "note", "todo", "report");
  if ($("search-ai").checked) kinds.push("ai_work");
  const list = $("results");
  list.replaceChildren(el("li", { class: "empty" }, t("search.searching")));
  try {
    const j = await api("/v1/search", { method: "POST", body: { query, kinds: kinds.length ? kinds : null, k: 30 } });
    list.replaceChildren(...j.hits.map((h) => {
      const li = eventItem(h.event, el("span", { class: "score" }, new Intl.DateTimeFormat(LOCALE[lang], { timeZone: TZ, month: "numeric", day: "numeric" }).format(new Date(h.event.started_at))));
      return li;
    }));
    $("results-empty").hidden = j.hits.length > 0;
  } catch (err) {
    list.replaceChildren();
    toast(t("toast.searchFail", { err: err.message }));
  }
});

// ---------- 状態タブ ----------
async function loadStatus() {
  const h = await loadHealth();
  $("server-kv").replaceChildren(
    el("dt", {}, t("status.state")), el("dd", {}, h ? (h.ok ? t("status.ok") : t("status.issue")) : t("status.unreachable")),
    el("dt", {}, t("status.db")), el("dd", {}, h?.db ?? "-"),
    el("dt", {}, t("status.embedPending")), el("dd", {}, h ? t("common.count", { n: h.embed_pending }) : "-"),
  );
  try {
    const j = await api("/v1/importers");
    const im = j.importers.find((i) => i.name === "agent_memory") || j.importers[0];
    $("importer-kv").replaceChildren(
      el("dt", {}, t("status.enabled")), el("dd", {}, im?.enabled ? t("common.yes") : t("common.no")),
      el("dt", {}, t("status.lastSuccess")), el("dd", {}, im?.last_success_at ? new Date(im.last_success_at).toLocaleString(LOCALE[lang], { timeZone: TZ }) : "-"),
      el("dt", {}, t("status.importedCount")), el("dd", {}, im ? t("common.count", { n: im.imported_count }) : "-"),
      el("dt", {}, t("status.lastError")), el("dd", {}, im?.last_error || t("common.none")),
    );
  } catch {
    $("importer-kv").replaceChildren(el("dt", {}, t("status.state")), el("dd", {}, t("status.fetchFail")));
  }
  if (!state.timeline) await loadTimeline();
}
function renderDevices(items) {
  const by = new Map();
  for (const it of items) {
    const ev = it.event;
    const id = ev?.device_id || it.session?.device_id;
    if (!id) continue;
    const name = ev?.device_name || id;
    const d = by.get(id) || { name, n: 0, last: null };
    d.n += 1;
    d.last = it.t;
    by.set(id, d);
  }
  $("devices").replaceChildren(...[...by.values()].map((d) =>
    el("li", {}, el("span", {}, d.name), el("span", { class: "m" }, t("devices.count", { n: d.n, time: fmtTime(d.last) })))));
  if (!by.size) $("devices").append(el("li", {}, t("devices.empty")));
}
$("btn-import").addEventListener("click", async (e) => {
  e.currentTarget.disabled = true;
  try {
    const j = await api("/v1/importers/agent-memory/run", { method: "POST" });
    toast(t("toast.imported", { n: j.upserted }));
    await Promise.all([loadStatus(), loadTimeline()]);
  } catch (err) {
    toast(t("toast.importFail", { err: err.message }));
  } finally {
    e.currentTarget.disabled = false;
  }
});


// ---------- 日報 ----------
let reportDay = todayStr();
async function loadReport() {
  $("r-daylabel").textContent = fmtDayLabel(reportDay);
  $("r-next").disabled = reportDay >= todayStr();
  $("r-body").innerHTML = `<p class="empty">${t("common.loading")}</p>`;
  let rep = null;
  try {
    const j = await api(`/v1/reports/daily?date=${reportDay}&tz=${encodeURIComponent(TZ)}`);
    rep = j.report || null;
  } catch { rep = null; }
  if (rep) {
    $("r-body").innerHTML = renderMarkdown(rep.text);
    const m = rep.meta || {};
    $("r-meta").textContent = `${t("report.created", { date: new Date(rep.updated_at || rep.created_at || rep.started_at).toLocaleString(LOCALE[lang], { timeZone: TZ, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) })}${m.model ? ` · ${m.model}` : ""}`;
    $("r-generate").textContent = t("report.regenerate");
  } else {
    $("r-body").innerHTML = `<p class="empty">${t("report.empty")}</p>`;
    $("r-meta").textContent = "";
    $("r-generate").textContent = t("report.createNow");
  }
}
$("r-generate").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.disabled = true;
  $("r-body").innerHTML = `<p class="empty">${t("report.generating")}</p>`;
  try {
    await api("/v1/reports/daily", { method: "POST", body: { date: reportDay, tz: TZ, force: true } });
    toast(t("toast.reportCreated"));
  } catch (err) {
    toast(t("toast.reportFail", { err: err.message }));
  } finally {
    b.disabled = false;
    loadReport();
  }
});
$("r-prev").addEventListener("click", () => { reportDay = shiftDay(reportDay, -1); loadReport(); });
$("r-next").addEventListener("click", () => { if (reportDay < todayStr()) { reportDay = shiftDay(reportDay, 1); loadReport(); } });
$("r-daylabel").addEventListener("click", () => { reportDay = todayStr(); loadReport(); });

// ---------- ToDo ----------
let todos = [];
const TODO_URGENCY = { high: "todo.urgent", medium: "", low: "" };

async function loadTodos() {
  const showDone = $("todo-show-done").checked;
  try {
    const r = await api(`/v1/todos?status=${showDone ? "all" : "open"}`);
    todos = (r.todos || []).filter((x) => x.status !== "archived");
  } catch (err) {
    todos = [];
    $("todos-empty").textContent = t("todo.loadFail", { err: err.message });
  }
  renderTodos();
}

function renderTodos() {
  const list = $("todos");
  list.replaceChildren(...todos.map(todoItem));
  $("todos-empty").hidden = todos.length > 0;
}

function todoItem(td) {
  const done = td.status === "done";
  const meta = [];
  if (td.tracked_minutes > 0) meta.push(t("todo.tracked", { dur: fmtMinutes(td.tracked_minutes) }));
  if (td.due_date) meta.push(t("todo.due", { date: td.due_date }));
  if (td.source === "auto") meta.push(t("todo.aiFound"));
  return el("li", { class: `todo ${done ? "done" : ""} ${td.tracking ? "tracking" : ""}` },
    el("button", { class: "chk", title: done ? t("todo.markOpen") : t("todo.markDone"), "aria-label": t("todo.doneAria"), onclick: () => toggleTodo(td) }, done ? "✓" : ""),
    el("div", { class: "body" },
      el("div", { class: "tt" },
        td.tracking ? el("span", { class: "live-dot", title: t("todo.trackingHint") }) : null,
        TODO_URGENCY[td.urgency] ? el("span", { class: "urg" }, t(TODO_URGENCY[td.urgency])) : null,
        td.title),
      td.tracking || meta.length ? el("div", { class: "meta" },
        td.tracking ? el("span", { class: "now-on" }, t("todo.workingNow")) : null,
        td.tracking && meta.length ? " · " : "", meta.join(" · ")) : null,
    ),
    el("button", { class: "del", title: t("common.delete"), "aria-label": t("common.delete"), onclick: () => deleteTodo(td) }, "×"),
  );
}

async function toggleTodo(td) {
  try {
    await api(`/v1/todos/${td.id}`, { method: "PATCH", body: { status: td.status === "done" ? "open" : "done" } });
    if (td.status !== "done") toast(t("toast.doneTodo", { title: td.title }));
  } catch (err) { toast(t("toast.updateFail", { err: err.message })); }
  loadTodos();
}

async function deleteTodo(td) {
  if (!confirm(t("confirm.deleteTodo", { title: td.title }))) return;
  try { await api(`/v1/todos/${td.id}`, { method: "DELETE" }); }
  catch (err) { toast(t("toast.deleteFail", { err: err.message })); }
  loadTodos();
}

async function deleteTodoEntry(e) {
  try {
    await api(`/v1/todos/${e.todo_id}/entries/${e.id}`, { method: "DELETE" });
    $("lane-pop").hidden = true;
    toast(t("toast.cancelled"));
  } catch (err) { toast(t("toast.cancelFail", { err: err.message })); }
  await Promise.all([loadTimeline(), loadTodos()]);
}

$("todo-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("todo-input");
  const title = input.value.trim();
  if (!title) return;
  input.disabled = true;
  try {
    await api("/v1/todos", { method: "POST", body: { title, source: "me", device_id: DEVICE_ID } });
    input.value = "";
  } catch (err) { toast(t("toast.addFail", { err: err.message })); }
  input.disabled = false;
  input.focus();
  loadTodos();
});
$("todo-show-done").addEventListener("change", loadTodos);

$("share-open").addEventListener("click", () => openShare({ api, todayStr, shiftDay, fmtMinutes, toast, el }));

// ---------- タブ ----------
function showTab(name) {
  for (const v of document.querySelectorAll(".view")) v.hidden = v.dataset.view !== name;
  for (const tb of document.querySelectorAll(".tab")) tb.classList.toggle("on", tb.dataset.tab === name);
  if (name === "status") loadStatus();
  if (name === "report") { reportDay = localStorage.getItem("mc.reportDay") || state.day; localStorage.removeItem("mc.reportDay"); loadReport(); }
  if (name === "search") $("search-input").focus();
  history.replaceState(null, "", `#${name}`);
}
document.querySelector(".tabbar").addEventListener("click", (e) => {
  const tb = e.target.closest(".tab");
  if (tb) showTab(tb.dataset.tab);
});

async function refreshToday() {
  await Promise.all([loadActive(), loadTimeline(), loadHeat(), loadTodos()]);
}

window.addEventListener("hashchange", () => {
  const name = location.hash.slice(1).replace(/[^a-z]/g, "");
  if (document.querySelector(`.view[data-view="${name}"]`)) showTab(name);
});

// 埋め込み時は内容の高さを親（デスクトップアプリ）へ知らせる
if (document.documentElement.classList.contains("embed") && window.parent !== window) {
  const main = document.querySelector("main");
  new ResizeObserver(() => window.parent.postMessage({ hibi: "height", only: new URLSearchParams(location.search).get("only") || "", h: main.offsetHeight }, "*")).observe(main);
}

// 起動
for (const [code, name] of Object.entries(LANGS)) {
  $("lang-select").append(el("button", { type: "button", class: `chip${code === lang ? " on" : ""}`, onclick: () => setLang(code) }, name));
}
loadHealth();
refreshToday();
showTab((location.hash || "#today").slice(1).replace(/[^a-z]/g, "") || "today");
// 画面に戻ってきたら最新にする（スマホでの切り替え向け）
document.addEventListener("visibilitychange", () => { if (!document.hidden) { loadHealth(); refreshToday(); } });
setInterval(() => { if (!document.hidden) refreshToday(); }, 60_000);
