// Hibi の備忘録（ブログ閲覧）。sync-server の /v1/blog API だけを使う。
import { renderMarkdown, esc } from "../ui/md.js";
import { t, lang, LANGS, LOCALE, setLang, applyI18n } from "../ui/i18n.js";

const $ = (id) => document.getElementById(id);
const KIND = { post: "blog.kind.post", summary: "blog.kind.summary", learning: "blog.kind.learning" };
const EMBED = document.documentElement.classList.contains("embed");
const fmtDate = (d) => {
  const [y, m, day] = d.split("-").map(Number);
  return new Intl.DateTimeFormat(LOCALE[lang], { timeZone: "UTC", year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date(Date.UTC(y, m - 1, day)));
};

applyI18n();

// ヘッダ右の言語セレクト
{
  const sel = $("lang-select");
  for (const [code, name] of Object.entries(LANGS)) sel.append(new Option(name, code));
  sel.value = lang;
  sel.addEventListener("change", () => setLang(sel.value));
}

const list = { type: "", tag: "", q: "", cursor: null };

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

function tagLink(tag, n) {
  return `<a class="tag" href="#/tag/${encodeURIComponent(tag)}">#${esc(tag)}${n != null ? `<span class="n">${n}</span>` : ""}</a>`;
}

function card(p) {
  return `<li><a class="post-card" href="#/post/${encodeURIComponent(p.slug)}">
    <div class="meta"><span class="kind ${esc(p.type)}">${esc(KIND[p.type] ? t(KIND[p.type]) : p.type)}</span><span>${esc(fmtDate(p.date))}</span></div>
    <h3>${esc(p.title)}</h3>
    <p>${esc(p.excerpt || "")}</p>
  </a></li>`;
}

async function loadList(append = false) {
  const params = new URLSearchParams({ limit: "20" });
  if (list.type) params.set("type", list.type);
  if (list.tag) params.set("tag", list.tag);
  if (list.q) params.set("q", list.q);
  if (append && list.cursor) params.set("cursor", list.cursor);
  let j;
  try {
    j = await api(`/v1/blog/posts?${params}`);
  } catch {
    $("posts").innerHTML = `<li class="empty">${esc(t("blog.loadFail"))}</li>`;
    return;
  }
  const html = j.posts.map(card).join("");
  if (append) $("posts").insertAdjacentHTML("beforeend", html);
  else $("posts").innerHTML = html;
  list.cursor = j.next_cursor || null;
  $("more").hidden = !list.cursor;
  $("posts-empty").hidden = append || j.posts.length > 0;
  $("active-tag").hidden = !list.tag;
  if (list.tag) $("active-tag").innerHTML = `${t("blog.tagFilter", { tag: esc(list.tag) })} <button id="clear-tag">${esc(t("blog.clearTag"))}</button>`;
  for (const c of $("type-filter").children) c.classList.toggle("on", c.dataset.type === list.type);
}

async function loadTags() {
  try {
    const tags = await api("/v1/blog/tags");
    $("tags").innerHTML = tags.map((x) => tagLink(x.tag, x.count)).join("");
  } catch {
    $("tags").innerHTML = "";
  }
}

async function showPost(slug) {
  $("list-view").hidden = true;
  $("post-view").hidden = false;
  $("post-body").innerHTML = `<p class="empty">${esc(t("common.loading"))}</p>`;
  let p;
  try {
    p = await api(`/v1/blog/posts/${encodeURIComponent(slug)}`);
  } catch {
    $("post-title").textContent = t("blog.notFound");
    $("post-body").innerHTML = "";
    return;
  }
  document.title = t("blog.docTitle", { title: p.title });
  $("post-meta").innerHTML = `<span class="kind ${esc(p.type)}">${esc(KIND[p.type] ? t(KIND[p.type]) : p.type)}</span><span>${esc(fmtDate(p.date))}</span>`;
  $("post-title").textContent = p.title;
  $("post-tags").innerHTML = (p.tags || []).map((x) => tagLink(x)).join("");
  // 本文先頭の見出しがタイトルと同じなら重複させない
  const body = p.markdown.replace(/^#\s+.*\n+/, (h) => (h.replace(/^#\s+/, "").trim() === p.title.trim() ? "" : h));
  $("post-body").innerHTML = renderMarkdown(body);
  $("post-foot").innerHTML = `${t("blog.foot")}<a href="../ui/${EMBED ? "?embed=1" : ""}#report" data-day="${esc(p.date)}" id="to-report">${esc(t("blog.viewReport"))}</a>${p.model ? ` · ${t("blog.writtenBy", { model: esc(p.model) })}` : ""}`;
  $("to-report").addEventListener("click", () => localStorage.setItem("mc.reportDay", p.date));
  const nav = [];
  if (p.prev) nav.push(`<a class="prev" href="#/post/${encodeURIComponent(p.prev.slug)}"><span class="lbl">${esc(t("blog.prev"))}</span>${esc(p.prev.title)}</a>`);
  if (p.next) nav.push(`<a class="next" href="#/post/${encodeURIComponent(p.next.slug)}"><span class="lbl">${esc(t("blog.next"))}</span>${esc(p.next.title)}</a>`);
  $("prevnext").innerHTML = nav.join("");
  window.scrollTo(0, 0);
}

function route() {
  const h = decodeURIComponent(location.hash.slice(1) || "/");
  const post = h.match(/^\/post\/(.+)$/);
  if (post) { showPost(post[1]); return; }
  document.title = t("blog.title");
  $("post-view").hidden = true;
  $("list-view").hidden = false;
  const tag = h.match(/^\/tag\/(.+)$/);
  list.tag = tag ? tag[1] : "";
  list.cursor = null;
  loadList();
}

$("type-filter").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  list.type = b.dataset.type;
  list.cursor = null;
  loadList();
});
$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  list.q = $("q").value.trim();
  list.cursor = null;
  loadList();
});
$("active-tag").addEventListener("click", (e) => {
  if (e.target.id === "clear-tag") location.hash = "#/";
});
$("more").addEventListener("click", () => loadList(true));
window.addEventListener("hashchange", route);

loadTags();
route();
