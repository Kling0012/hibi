// 作業の「自慢カード」: 期間の記録を 1 枚の画像にして、保存・コピー・X への投稿をする。
// 画像はブラウザの canvas で描くだけで、サーバには何も送らない。

import { t, lang, LOCALE } from "./i18n.js";

const W = 1200;
const H = 675; // X で切れずに出る 16:9
const KEY = "mc.share";

const THEMES = {
  blue: { name: "theme.blue", bg: ["#2f5bea", "#6d8fff"], fg: "#ffffff", sub: "rgba(255,255,255,.75)", panel: "rgba(255,255,255,.14)", bar: "#ffffff", accent: "#ffd84d" },
  night: { name: "theme.night", bg: ["#0f1115", "#262b45"], fg: "#e8eaee", sub: "#8a93a3", panel: "rgba(255,255,255,.06)", bar: "#6d8fff", accent: "#3ccf7f" },
  forest: { name: "theme.forest", bg: ["#0f6b46", "#3ccf7f"], fg: "#ffffff", sub: "rgba(255,255,255,.78)", panel: "rgba(255,255,255,.15)", bar: "#ffffff", accent: "#fff3a0" },
  sakura: { name: "theme.sakura", bg: ["#fff1f4", "#ffd3de"], fg: "#4a2330", sub: "#9a6474", panel: "rgba(255,255,255,.6)", bar: "#e0607e", accent: "#e0607e" },
  paper: { name: "theme.paper", bg: ["#ffffff", "#f3f5f9"], fg: "#1d2433", sub: "#6b7385", panel: "#eef1f6", bar: "#3b6cf6", accent: "#e07a1f" },
};

const PERIODS = { day: "period.day", week: "period.week", month: "period.month" };

const DEFAULTS = {
  period: "day",
  theme: "blue",
  heading: "",
  comment: "",
  handle: "",
  hideNames: false,
  show: { total: true, pc: true, ai: true, todos: true, streak: true, breakdown: true, chart: true },
};

const SHOW_LABELS = {
  total: "show.total",
  pc: "show.pc",
  ai: "show.ai",
  todos: "show.todos",
  streak: "show.streak",
  breakdown: "show.breakdown",
  chart: "show.chart",
};

let ctx = null; // { api, todayStr, shiftDay, fmtMinutes, toast, el }
let opts = loadOpts();
let data = null;
let postTextEdited = false;

function loadOpts() {
  try {
    const o = JSON.parse(localStorage.getItem(KEY) || "{}");
    return { ...DEFAULTS, ...o, show: { ...DEFAULTS.show, ...(o.show || {}) } };
  } catch { return structuredClone(DEFAULTS); }
}
function saveOpts() {
  // ひとことは毎回変わるので残さない
  const { comment, ...rest } = opts;
  localStorage.setItem(KEY, JSON.stringify(rest));
}

// ---------- データ ----------
function range() {
  const to = ctx.todayStr();
  const days = opts.period === "day" ? 1 : opts.period === "week" ? 7 : 30;
  return { from: ctx.shiftDay(to, -(days - 1)), to, days };
}

async function loadData() {
  const { from, to } = range();
  const tz = "Asia/Tokyo";
  const fromAt = `${from}T00:00:00+09:00`;
  const toAt = `${ctx.shiftDay(to, 1)}T00:00:00+09:00`;
  const [stats, streakStats, sessions, done, timeline] = await Promise.all([
    ctx.api(`/v1/stats/daily?from=${from}&to=${to}&tz=${tz}`).catch(() => ({ days: [] })),
    ctx.api(`/v1/stats/daily?from=${ctx.shiftDay(to, -120)}&to=${to}&tz=${tz}`).catch(() => ({ days: [] })),
    ctx.api(`/v1/sessions?from=${encodeURIComponent(fromAt)}&to=${encodeURIComponent(toAt)}&limit=500`).catch(() => ({ sessions: [] })),
    ctx.api(`/v1/todos?status=done&limit=200`).catch(() => ({ todos: [] })),
    opts.period === "day" ? ctx.api(`/v1/timeline?date=${to}&tz=${tz}`).catch(() => null) : null,
  ]);
  const days = stats.days || [];
  const sum = (k) => days.reduce((a, d) => a + (d[k] || 0), 0);

  const byTitle = new Map();
  for (const s of sessions.sessions || []) {
    if (s.status === "cancelled") continue;
    byTitle.set(s.title, (byTitle.get(s.title) || 0) + (s.duration_minutes || 0));
  }
  const breakdown = [...byTitle.entries()].map(([title, minutes]) => ({ title, minutes }))
    .filter((b) => b.minutes >= 1).sort((a, b) => b.minutes - a.minutes);

  const fromMs = Date.parse(fromAt);
  const toMs = Date.parse(toAt);
  const doneTodos = (done.todos || []).filter((td) => {
    if (!td.done_at) return false;
    const doneMs = Date.parse(td.done_at);
    return doneMs >= fromMs && doneMs < toMs;
  });

  // 連続記録: 今日（記録がまだ無ければ昨日）からさかのぼって、記録のある日が続いた日数
  const active = new Set((streakStats.days || []).filter((d) => d.events > 0).map((d) => d.date));
  let streak = 0;
  let d = to;
  if (!active.has(d)) d = ctx.shiftDay(d, -1);
  while (active.has(d)) { streak++; d = ctx.shiftDay(d, -1); }

  // グラフ: 1 日なら 1 時間ごとの動き、複数日なら日ごとの作業時間
  let chart;
  if (opts.period === "day") {
    const hours = new Array(24).fill(0);
    for (const it of timeline?.items || []) {
      const tm = new Date(it.t);
      const h = Number(new Intl.DateTimeFormat("en-US", { hour: "numeric", hourCycle: "h23", timeZone: "Asia/Tokyo" }).format(tm));
      if (it.type !== "session" && h >= 0 && h < 24) hours[h]++;
    }
    chart = { kind: "hours", values: hours };
  } else {
    const byDay = new Map(days.map((x) => [x.date, Math.max(x.session_minutes || 0, x.active_minutes || 0)]));
    const values = [];
    for (let i = range().days - 1; i >= 0; i--) {
      const day = ctx.shiftDay(to, -i);
      values.push({ day, v: byDay.get(day) || 0 });
    }
    chart = { kind: "days", values };
  }

  data = {
    from, to,
    total: sum("session_minutes"),
    pc: sum("active_minutes"),
    ai: sum("ai_active_minutes"),
    breakdown,
    doneTodos,
    streak,
    chart,
  };
}

// ---------- 描画 ----------
function fmtDur(m) {
  m = Math.round(m || 0);
  if (m < 60) return { big: String(m), unit: t("unit.min") };
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? { big: `${h}`, unit: t("unit.hour"), big2: `${r}`, unit2: t("unit.min") } : { big: `${h}`, unit: t("unit.hour") };
}
function durText(m) {
  const f = fmtDur(m);
  return `${f.big}${f.unit}${f.big2 ? f.big2 + f.unit2 : ""}`;
}
function dateText() {
  const at = (s) => new Date(`${s}T12:00:00+09:00`);
  if (opts.period === "day") {
    return new Intl.DateTimeFormat(LOCALE[lang], { timeZone: "Asia/Tokyo", year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(at(data.to));
  }
  const md = new Intl.DateTimeFormat(LOCALE[lang], { timeZone: "Asia/Tokyo", month: "long", day: "numeric" });
  return `${md.format(at(data.from))} ${t("common.dash")} ${md.format(at(data.to))}`;
}
function defaultHeading() {
  return t(opts.period === "day" ? "share.head.day" : opts.period === "week" ? "share.head.week" : "share.head.month");
}
function shownTitle(title, i) {
  return opts.hideNames ? t("share.anonWork", { label: String.fromCharCode(65 + i) }) : title;
}

const FONT = '"Hiragino Sans", "Noto Sans JP", "Yu Gothic UI", "Meiryo", system-ui, sans-serif';
function font(px, weight = 400) { return `${weight} ${px}px ${FONT}`; }

function fitText(g, text, maxW) {
  if (g.measureText(text).width <= maxW) return text;
  let s = text;
  while (s.length > 1 && g.measureText(`${s}…`).width > maxW) s = s.slice(0, -1);
  return `${s}…`;
}

function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.roundRect(x, y, w, h, r);
  g.fill();
}

function draw(canvas) {
  const th = THEMES[opts.theme] || THEMES.blue;
  canvas.width = W;
  canvas.height = H;
  const g = canvas.getContext("2d");
  const grad = g.createLinearGradient(0, 0, W, H);
  grad.addColorStop(0, th.bg[0]);
  grad.addColorStop(1, th.bg[1]);
  g.fillStyle = grad;
  g.fillRect(0, 0, W, H);
  if (!data) return;

  const P = 64;
  g.textBaseline = "alphabetic";

  // 見出しと日付
  g.fillStyle = th.sub;
  g.font = font(26, 500);
  g.fillText(dateText(), P, P + 22);
  g.fillStyle = th.fg;
  g.font = font(48, 700);
  g.fillText(fitText(g, opts.heading || defaultHeading(), W - P * 2), P, P + 84);

  const s = opts.show;
  const right = s.breakdown && data.breakdown.length > 0;
  const leftW = right ? 560 : W - P * 2;
  let y = P + 130;

  // 大きな数字（作業時間）
  // 作業の宣言が無い日は、PC を使った時間を大きく出す
  const mainIsPc = s.total && data.total < 1 && data.pc > 0;
  if (s.total) {
    g.fillStyle = th.sub;
    g.font = font(24, 500);
    g.fillText(mainIsPc ? t("share.pcMain") : t("share.totalTime"), P, y + 20);
    const f = fmtDur(mainIsPc ? data.pc : data.total);
    let x = P;
    const big = (txt, unit) => {
      g.fillStyle = th.fg; g.font = font(112, 800); g.fillText(txt, x, y + 140); x += g.measureText(txt).width + 6;
      g.fillStyle = th.sub; g.font = font(36, 600); g.fillText(unit, x, y + 140); x += g.measureText(unit).width + 18;
    };
    big(f.big, f.unit);
    if (f.big2) big(f.big2, f.unit2);
    y += 170;
  }

  // 小さな数字の並び
  const chips = [];
  if (s.pc && !mainIsPc) chips.push([t("share.pcUsed"), durText(data.pc)]);
  if (s.ai) chips.push([t("share.aiActive"), durText(data.ai)]);
  if (s.todos) chips.push([t("share.doneTodos"), t("share.nitems", { n: data.doneTodos.length })]);
  if (s.streak) chips.push([t("share.streakLabel"), t("share.ndays", { n: data.streak })]);
  if (chips.length) {
    const cols = Math.min(chips.length, right ? 2 : 4);
    const gap = 16;
    const cw = (leftW - gap * (cols - 1)) / cols;
    chips.forEach(([label, value], i) => {
      const cx = P + (i % cols) * (cw + gap);
      const cy = y + Math.floor(i / cols) * 96;
      g.fillStyle = th.panel; roundRect(g, cx, cy, cw, 82, 16);
      g.fillStyle = th.sub; g.font = font(20, 500); g.fillText(label, cx + 20, cy + 32);
      g.fillStyle = th.fg; g.font = font(32, 700); g.fillText(fitText(g, value, cw - 40), cx + 20, cy + 68);
    });
    y += Math.ceil(chips.length / cols) * 96;
  }

  // 右側: 作業の内訳
  if (right) {
    const bx = P + leftW + 48;
    const bw = W - P - bx;
    let by = P + 130;
    g.fillStyle = th.sub; g.font = font(24, 500); g.fillText(t("share.breakdownTitle"), bx, by + 20);
    by += 44;
    const items = data.breakdown.slice(0, 5);
    const max = items[0].minutes || 1;
    items.forEach((b, i) => {
      g.fillStyle = th.fg; g.font = font(24, 600);
      const label = fitText(g, shownTitle(b.title, i), bw - 130);
      g.fillText(label, bx, by + 24);
      g.fillStyle = th.sub; g.font = font(22, 500);
      const dt = durText(b.minutes);
      g.fillText(dt, bx + bw - g.measureText(dt).width, by + 24);
      g.fillStyle = th.panel; roundRect(g, bx, by + 36, bw, 12, 6);
      g.fillStyle = th.bar; roundRect(g, bx, by + 36, Math.max(12, (bw * b.minutes) / max), 12, 6);
      by += 70;
    });
    if (data.breakdown.length > 5) {
      g.fillStyle = th.sub; g.font = font(20, 500);
      g.fillText(t("share.more", { n: data.breakdown.length - 5 }), bx, by + 14);
    }
  }

  // 下: グラフ
  const footY = H - P + 12;
  const commentH = opts.comment ? 44 : 0;
  if (s.chart && data.chart) {
    const bottom = footY - 40 - commentH;
    const gh = Math.min(220, bottom - (y + 24));
    const gy = bottom - gh;
    if (gh >= 40) drawChart(g, th, P, gy, W - P * 2, gh);
  }

  // ひとこと
  if (opts.comment) {
    g.fillStyle = th.fg; g.font = font(28, 600);
    g.fillText(fitText(g, opts.comment, W - P * 2), P, footY - 44);
  }

  // フッター
  g.fillStyle = th.sub; g.font = font(22, 500);
  if (opts.handle) g.fillText(fitText(g, opts.handle, W / 2), P, footY);
  const brand = t("share.footer");
  g.fillText(brand, W - P - g.measureText(brand).width, footY);
}

function drawChart(g, th, x, y, w, h) {
  const vals = data.chart.kind === "hours" ? data.chart.values : data.chart.values.map((d) => d.v);
  const n = vals.length;
  const max = Math.max(1, ...vals);
  const gap = n > 24 ? 4 : 6;
  const bw = (w - gap * (n - 1)) / n;
  const labelH = 22;
  const ch = h - labelH;
  vals.forEach((v, i) => {
    const bh = v > 0 ? Math.max(6, (ch * v) / max) : 4;
    g.fillStyle = v > 0 ? th.bar : th.panel;
    g.globalAlpha = v > 0 ? 0.35 + 0.65 * (v / max) : 1;
    roundRect(g, x + i * (bw + gap), y + ch - bh, bw, bh, Math.min(4, bw / 2));
  });
  g.globalAlpha = 1;
  g.fillStyle = th.sub; g.font = font(16, 500);
  if (data.chart.kind === "hours") {
    for (const hr of [0, 6, 12, 18, 23]) g.fillText(t("common.hour", { h: hr }), x + hr * (bw + gap), y + h);
  } else {
    const first = data.chart.values[0].day.slice(5).replace("-", "/");
    const last = data.chart.values[n - 1].day.slice(5).replace("-", "/");
    g.fillText(first, x, y + h);
    g.fillText(last, x + w - g.measureText(last).width, y + h);
  }
}

// ---------- 投稿文 ----------
function autoPostText() {
  if (!data) return "";
  const p = opts.period === "day" ? "day" : opts.period === "week" ? "week" : "month";
  const parts = [];
  if (opts.show.total && data.total >= 1) parts.push(t("post.partWork", { dur: durText(data.total) }));
  if (opts.show.todos && data.doneTodos.length) parts.push(t("post.partTodos", { n: data.doneTodos.length }));
  if (!parts.length && data.pc >= 1) parts.push(t("post.partPc", { dur: durText(data.pc) }));
  let txt = parts.length ? t(`post.done.${p}`, { parts: parts.join(t("post.and")) }) : t("post.recordOnly", { label: t(`post.label.${p}`) });
  if (opts.show.streak && data.streak >= 2) txt += t("post.streak", { n: data.streak });
  if (opts.comment) txt = `${opts.comment}\n${txt}`;
  return `${txt}\n#Hibi`;
}

// ---------- 画面 ----------
function toBlob(canvas) {
  return new Promise((res) => canvas.toBlob(res, "image/png"));
}
function fileName() {
  return `hibi-${opts.period}-${data?.to || ctx.todayStr()}.png`;
}
function download(blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fileName();
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}
const embedded = () => document.documentElement.classList.contains("embed") && window.parent !== window;

async function copyImage(canvas) {
  // アプリに埋め込まれているときは、アプリ側でクリップボードに入れる（http のページからは書けないため）
  if (embedded()) {
    window.parent.postMessage({ hibi: "copy-image", dataUrl: canvas.toDataURL("image/png") }, "*");
    return true;
  }
  if (window.isSecureContext && navigator.clipboard && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": toBlob(canvas) })]);
      return true;
    } catch { /* 下で保存に切り替える */ }
  }
  return false;
}

async function postToX(canvas, text) {
  const blob = await toBlob(canvas);
  const file = new File([blob], fileName(), { type: "image/png" });
  // スマホ: 共有シートで X アプリに画像ごと渡す
  if (!embedded() && navigator.canShare?.({ files: [file] })) {
    try { await navigator.share({ files: [file], text }); return; }
    catch (err) { if (err?.name === "AbortError") return; }
  }
  // PC: 画像をクリップボード（だめなら保存）に置いてから、投稿画面を開く
  const copied = await copyImage(canvas);
  if (!copied) download(blob);
  window.open(`https://x.com/intent/post?text=${encodeURIComponent(text)}`, "_blank", "noopener");
  ctx.toast(copied ? t("share.copiedPaste") : t("share.savedAttach"));
}

function buildDialog() {
  const { el } = ctx;
  const dlg = el("dialog", { id: "share-dialog", class: "share-dialog" });
  const canvas = el("canvas", { class: "share-canvas", width: W, height: H });
  const postText = el("textarea", { class: "share-text", rows: 3, maxlength: 280 });
  postText.addEventListener("input", () => { postTextEdited = true; });

  const seg = (name, choices, onPick) => el("div", { class: "seg", role: "group", "data-name": name },
    ...Object.entries(choices).map(([k, label]) => el("button", {
      type: "button", "data-k": k, class: opts[name] === k ? "on" : "",
      onclick: (e) => { opts[name] = k; for (const b of e.target.parentElement.children) b.classList.toggle("on", b.dataset.k === k); onPick(); },
    }, label)));

  const refresh = async (reload) => {
    saveOpts();
    if (reload) {
      canvas.classList.add("loading");
      try { await loadData(); } catch (err) { ctx.toast(t("toast.loadFail", { err: err.message })); }
      canvas.classList.remove("loading");
    }
    draw(canvas);
    if (!postTextEdited) postText.value = autoPostText();
  };

  const textField = (key, label, placeholder, max) => {
    const input = el("input", { type: "text", maxlength: max, placeholder, value: opts[key] || "" });
    input.addEventListener("input", () => { opts[key] = input.value; refresh(false); });
    return el("label", {}, label, input);
  };

  const checks = el("div", { class: "share-checks" },
    ...Object.entries(SHOW_LABELS).map(([k, key]) => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = !!opts.show[k];
      cb.addEventListener("change", () => { opts.show[k] = cb.checked; refresh(false); });
      return el("label", { class: "mini-check" }, cb, t(key));
    }),
    (() => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = !!opts.hideNames;
      cb.addEventListener("change", () => { opts.hideNames = cb.checked; refresh(false); });
      return el("label", { class: "mini-check" }, cb, t("share.hideNames"));
    })(),
  );

  const swatches = el("div", { class: "swatches" },
    ...Object.entries(THEMES).map(([k, th]) => el("button", {
      type: "button", title: t(th.name), "aria-label": t(th.name), "data-k": k,
      class: opts.theme === k ? "on" : "",
      style: `background:linear-gradient(135deg, ${th.bg[0]}, ${th.bg[1]})`,
      onclick: (e) => { opts.theme = k; for (const b of e.currentTarget.parentElement.children) b.classList.toggle("on", b.dataset.k === k); refresh(false); },
    })));

  dlg.append(
    el("div", { class: "share-head" },
      el("h2", {}, t("share.title")),
      el("button", { type: "button", class: "btn icon small ghost", "aria-label": t("btn.close"), onclick: () => dlg.close() }, "×")),
    el("div", { class: "share-body" },
      el("div", { class: "share-preview" }, canvas),
      el("div", { class: "share-opts" },
        el("div", { class: "share-row" }, el("span", { class: "k" }, t("share.periodLabel")), seg("period", { day: t(PERIODS.day), week: t(PERIODS.week), month: t(PERIODS.month) }, () => { postTextEdited = false; refresh(true); })),
        el("div", { class: "share-row" }, el("span", { class: "k" }, t("share.colorLabel")), swatches),
        el("div", { class: "share-row col" }, el("span", { class: "k" }, t("share.includeLabel")), checks),
        textField("heading", t("share.headingLabel"), t("share.headingPh"), 40),
        textField("comment", t("share.commentLabel"), t("share.commentPh"), 60),
        textField("handle", t("share.handleLabel"), t("share.handlePh"), 30),
        el("label", {}, t("share.postText"), postText),
      ),
    ),
    el("div", { class: "share-actions" },
      el("button", { type: "button", class: "btn small", onclick: async () => download(await toBlob(canvas)) }, t("share.save")),
      el("button", { type: "button", class: "btn small", onclick: async () => {
        ctx.toast(await copyImage(canvas) ? t("share.copied") : t("share.copyFail"));
      } }, t("share.copy")),
      el("button", { type: "button", class: "btn small primary x-btn", onclick: () => postToX(canvas, postText.value) }, t("share.post")),
    ),
  );
  document.body.append(dlg);
  dlg.refresh = refresh;
  return dlg;
}

export function openShare(helpers) {
  ctx = helpers;
  const dlg = document.getElementById("share-dialog") || buildDialog();
  postTextEdited = false;
  dlg.showModal();
  dlg.refresh(true);
}
