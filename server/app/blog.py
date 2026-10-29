"""Blog post generation (P5) + authoring API helpers (P5-B).

Generated articles (written right after the daily report):
- summary: one diary-style article a day from the same material as the daily
  report (declared sessions + own activity/screen_summary + ai_work,
  superseded hidden), first person "私", です・ます style, narrative flow
  (one-line opening -> what was tackled -> stumbling blocks and decisions ->
  learnings -> what is next), 2-4 headings, 1500+ chars (no upper limit).
- learning: up to 3 articles a day of reusable technical knowledge from that
  day's ai_work records whose memory type is one of solution/cause/
  experiment/result/issue plus the user's screen_summary, written as a
  technical blog post for whoever hits the same problem (a concrete symptom
  first, then cause and fix in order; commands/configs only as code blocks
  and only when present in the material; a final 参考 section citing source
  record times and ids).

Every prompt says "write nothing that is not in the material" and embeds a
writing-style exemplar (app/blog_exemplar.md, loaded once at startup; a
missing or unreadable file only omits the exemplar, fail-soft - generation
itself keeps working): only its form is imitated (voice, motivation-first
opening, section headings, tables/lists for numbers), never its content.
Secret-looking strings are masked before storing (regex post-processing:
bearer values, key=value pairs, long key-shaped tokens; pure hex/dash
tokens such as uuids and git shas are kept so the 参考 section can cite
memory ids and commit ids). LLM_PROVIDER=fake returns short deterministic
Markdown (E2E). The nightly scheduler calls run_auto right after each
daily-report slot (23:55 same day / 06:00 force-regeneration of
yesterday); failures are logged only (fail-soft). BLOG_AUTO controls what
generated posts become: off (skip), draft (default) or publish.

Authoring (P5-B): posts have status (draft/published/archived) and author
("auto" / "me" / "agent:<name>"). The public list serves published posts
only. External AIs and the user write via POST/PATCH /v1/blog/posts (and
the MCP server); DELETE only archives. The materials endpoint/API returns
a day's writing material as structured JSON. rewrite_post asks the built-in
LLM for a rewritten version WITHOUT saving it (the caller PATCHes it in).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import secrets
import string
import uuid
from datetime import date as date_type
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import config
from .away import effective_away_intervals, session_minutes
from .db import SessionLocal
from .daily_report import (
    ReportError,
    _block_messages,
    _chunk_blocks,
    _day_window,
    _event_line,
    _llm_chat,
    _sessions_text,
    collect_material,
)
from .models import BlogPost, Event
from . import yomiyasu

log = logging.getLogger("sync.blog")

# Writing-style exemplar embedded in the generation prompts, loaded once at
# startup. A missing or unreadable file only omits the exemplar (fail-soft);
# generation itself keeps working without it.
_EXEMPLAR_PATH = Path(__file__).with_name("blog_exemplar.md")


def _load_exemplar() -> str | None:
    try:
        text = _EXEMPLAR_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        log.warning("blog exemplar not loaded (%s); prompts run without it", e)
        return None
    return text or None


_EXEMPLAR = _load_exemplar()


def _exemplar_section(note: str) -> str:
    """Prompt block embedding the exemplar ("" when it could not be loaded).

    `note` says what to imitate; the content warning is common to both
    prompts because the exemplar's own topics are never material for the
    day being written about.
    """
    if not _EXEMPLAR:
        return ""
    return (
        "\n\n文体の手本として次の記事を示す。" + note + "\n"
        "手本の内容（Hibi、Gemini、GitHub Actions など）は材料ではない。"
        "手本の固有名詞や出来事を記事に一切書かないこと。"
        "事実は必ず材料からだけ書く。\n\n"
        "### 手本記事\n" + _EXEMPLAR + "\n（手本ここまで）"
    )


def _chat(model: str, messages: list[dict]) -> str:
    """LLM call via the daily-report transport; ReportError -> BlogError."""
    try:
        return _llm_chat(model, messages)
    except ReportError as e:
        raise BlogError(str(e)) from e

BLOG_TYPES = ("summary", "learning")
# Types an authoring caller (REST/MCP) may create.
POST_TYPES = ("summary", "learning", "post")
POST_STATUSES = ("draft", "published", "archived")
# ai_work memory types that count as learning material.
LEARNING_MEMORY_TYPES = ("solution", "cause", "experiment", "result", "issue")
MAX_LEARNING_POSTS = 3
MAX_TAGS = 5
TITLE_MAX = 120
EXCERPT_MAX = 200
TOPIC_MAX = 8
# Per-record material caps (learning needs more detail than the report).
LEARN_AI_CHARS = 800
LEARN_SS_CHARS = 300
LEARN_MAX_RECORDS = 60
# Authoring materials endpoint caps (long texts are truncated, never LLM-
# summarized; callers can raise them via query params).
MATERIAL_CHARS = 500
MATERIAL_AI_CHARS = 2000
MATERIAL_MAX_RECORDS = 200
# Random slug suffix: date + this short id (POST/MCP-created posts).
_SLUG_ALPHABET = string.ascii_lowercase + string.digits


class BlogError(Exception):
    pass


def blog_id_for(day: date_type, type_: str, topic: str) -> uuid.UUID:
    """Deterministic post id: uuid5 of date+type+topic (idempotent upserts)."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"minecontext:sync-server:blog-post:{day.isoformat()}:{type_}:{topic}",
    )


def slug_for(day: date_type, type_: str, topic: str) -> str:
    if type_ == "summary":
        return f"{day.isoformat()}-summary"
    return f"{day.isoformat()}-learning-{topic}"


# --- secret masking (post-processing on everything we store) ---

# Bearer <token> -> Bearer ***
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
# key=value / key: value pairs (password=..., api_key: "...")
_KV_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[-_]?key|access[-_]?key)\b"
    r"(\s*[=:]\s*)([\"'])?([^\s\"',;]{4,})"
)
# Long key-shaped tokens. Pure hex/dash strings (uuids, git shas) survive so
# the 参考 section can cite memory ids and commit ids.
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{32,}(?![A-Za-z0-9+/_=-])")
_HEXISH_RE = re.compile(r"^[0-9a-fA-F-]+$")


def mask_secrets(text: str) -> str:
    text = _BEARER_RE.sub("Bearer ***", text)

    def _kv(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{m.group(3) or ''}***"

    text = _KV_RE.sub(_kv, text)

    def _tok(m: re.Match) -> str:
        return m.group(0) if _HEXISH_RE.match(m.group(0)) else "***"

    return _TOKEN_RE.sub(_tok, text)


# --- LLM output helpers ---


def _parse_json_object(content: str) -> dict:
    """Best-effort JSON extraction (the prompt asks for JSON only)."""
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s).strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise BlogError("LLM returned no JSON object")
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError as e:
        raise BlogError(f"LLM returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise BlogError("LLM JSON is not an object")
    return data


def _norm_tags(tags) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        t = " ".join(str(t).split())
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _norm_title(title, fallback: str) -> str:
    t = " ".join(str(title or "").split())[:TITLE_MAX]
    return t or fallback


def _fallback_excerpt(markdown: str) -> str:
    plain = re.sub(r"^#+\s*", "", markdown, flags=re.MULTILINE).strip()
    plain = " ".join(plain.split())
    return plain[:EXCERPT_MAX]


def _norm_excerpt(excerpt, markdown: str) -> str:
    e = " ".join(str(excerpt or "").split())[:EXCERPT_MAX]
    return e or _fallback_excerpt(markdown)


def _norm_markdown(markdown) -> str:
    md = str(markdown or "").strip()
    lines = md.splitlines()
    # strip accidental fences and a duplicated H1 (title is a column)
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    md = "\n".join(lines).strip()
    if not md:
        raise BlogError("LLM returned empty markdown")
    return md


def _topic_slug(topic, idx: int) -> str:
    s = re.sub(r"[^a-z0-9]", "", str(topic or "").lower())[:TOPIC_MAX]
    return s or f"t{idx + 1}"


def _auto_status() -> str:
    """What a built-in-generated post becomes (BLOG_AUTO)."""
    return "published" if config.BLOG_AUTO == "publish" else "draft"


def _upsert_post(
    db: Session,
    *,
    day: date_type,
    type_: str,
    topic: str,
    title: str,
    excerpt: str,
    markdown: str,
    tags: list[str],
    source_event_ids: list[uuid.UUID],
    model: str,
    commit: bool = True,
    upsert: bool = True,
) -> BlogPost:
    values = dict(
        id=blog_id_for(day, type_, topic),
        date=day,
        type=type_,
        slug=slug_for(day, type_, topic),
        title=mask_secrets(title),
        excerpt=mask_secrets(excerpt),
        markdown=mask_secrets(markdown),
        tags=tags,
        source_event_ids=source_event_ids,
        model=model,
        status=_auto_status(),
        author="auto",
    )
    stmt = pg_insert(BlogPost).values(**values)
    if upsert:
        set_ = {k: v for k, v in values.items() if k != "id"}
        set_["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=set_)
    db.execute(stmt)
    if commit:
        db.commit()
    return db.get(BlogPost, values["id"])


# --- summary (one article a day) ---


def _summary_messages(day: date_type, tz_name: str, sessions_text: str, events_part: str):
    system = (
        "あなたはPC作業ログから、個人のブログの日記的な記事を書く助手です。"
        "次の規則を厳守すること:\n"
        "- 材料に無い事実は書かない。推測・創作・一般論を混ぜない。"
        "感想や意見は本人の発言として材料にある範囲でだけ書く。\n"
        "- 一人称は「私」。文体は「です・ます」調。\n"
        "- 物語の流れで書く: 導入（その日を一言で表す）→ 何に取り組んだか →"
        " つまずき・判断・発見 → 学び → 次にやること。\n"
        "- 本文は文章で語り、箇条書きは数字の比較と最後の「次にやること」だけに使う。見出し(## )は2〜4個。"
        "H1(# )は書かない。本文は1500字以上（長くなってよい。上限なし）。\n"
        "- 出力は JSON オブジェクトのみ。前置き・後書き・コードブロックは書かない。"
    ) + _exemplar_section(
        "手本から真似るのは形式だけ: 語り口（です・ます、一人称）、"
        "導入で動機を書く、見出しで場面を区切る、"
        "数字や比較は表・箇条書きにする、"
        "つまずきは症状→原因→対処の順、最後に次にやることを箇条書き。"
    )
    user = (
        f"日付: {day.isoformat()}（時刻は {tz_name} の24時間表記 (HH:MM) に統一）\n\n"
        "## 材料1: 宣言された作業（work_sessions）\n"
        f"{sessions_text}\n\n"
        "## 材料2: この日の記録（時系列。activity/screen_summary=自分の作業、"
        "ai_work=AI の作業）\n"
        f"{events_part}\n\n"
        "以上の材料だけから、その日の記録を「私」の一人称で読み物として"
        "語る記事を1本書くこと。つまずきや判断があったら、何に困りどう決めたかを"
        "具体的に。次の JSON を返すこと:\n"
        '{"title": "記事タイトル（内容が伝わる自然な日本語。記号で区切らない。'
        '40字まで）", "excerpt": "リード文（120字程度）", '
        '"tags": ["プロジェクト名や技術名（最大5個）"], "markdown": "本文（見出し2〜4個）"}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _fake_summary(day: date_type, tz_name: str, counts: dict) -> dict:
    return {
        "title": f"まとめ {day.isoformat()}（fake）",
        "excerpt": (
            f"fake: sessions={counts['sessions']} activity={counts['activity']} "
            f"screen_summary={counts['screen_summary']} ai_work={counts['ai_work']}"
        )[:EXCERPT_MAX],
        "tags": ["fake"],
        "markdown": (
            f"## 概要（fake）\n"
            f"tz: {tz_name} / LLM_PROVIDER=fake（材料の件数のみを並べる）。\n\n"
            f"- sessions: {counts['sessions']}\n"
            f"- activity: {counts['activity']}\n"
            f"- screen_summary: {counts['screen_summary']}\n"
            f"- ai_work: {counts['ai_work']}\n"
        ),
    }


def generate_summary_post(
    db: Session, day: date_type, tz_name: str, zone: ZoneInfo, *, force: bool = False
) -> tuple[BlogPost, bool]:
    """Generate (or return the existing) summary post for `day`."""
    slug = slug_for(day, "summary", "")
    existing = db.scalars(select(BlogPost).where(BlogPost.slug == slug)).first()
    if existing is not None and not force:
        return existing, False

    sessions, events, counts, window = collect_material(db, day, zone)
    now = datetime.now(timezone.utc)
    source_ids = [e.id for e in events]
    if config.LLM_PROVIDER == "fake":
        data = _fake_summary(day, tz_name, counts)
        model = "fake"
    elif config.LLM_PROVIDER == "openai_compat":
        sessions_text = _sessions_text(sessions, zone, window, now, db)
        events_part = (
            "\n".join(_event_line(e, zone) for e in events) or "（この日の記録は無い）"
        )
        if len(events_part) > config.REPORT_CHUNK_CHARS:
            # same long-material strategy as the daily report: per-block
            # flash pre-summary, then integrate
            parts = []
            for label, lines in _chunk_blocks(events, zone):
                summary = _chat(
                    config.REPORT_LLM_FLASH_MODEL,
                    _block_messages(day, label, "\n".join(lines)),
                )
                parts.append(f"### {label}\n{summary.strip()}")
            events_part = "（長いため時間帯ごとの要約）\n" + "\n\n".join(parts)
        data = _parse_json_object(
            _chat(
                config.REPORT_LLM_MODEL,
                _summary_messages(day, tz_name, sessions_text, events_part),
            )
        )
        model = config.REPORT_LLM_MODEL
    else:
        raise BlogError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")

    markdown = _norm_markdown(data.get("markdown"))
    markdown = yomiyasu.polish(markdown, "blog_summary")  # body only, fail-soft
    return (
        _upsert_post(
            db,
            day=day,
            type_="summary",
            topic="",
            title=_norm_title(data.get("title"), f"まとめ {day.isoformat()}"),
            excerpt=_norm_excerpt(data.get("excerpt"), markdown),
            markdown=markdown,
            tags=_norm_tags(data.get("tags")),
            source_event_ids=source_ids,
            model=model,
        ),
        True,
    )


# --- learning (topic-wise, up to 3 a day) ---


def collect_learning_material(
    db: Session, day: date_type, zone: ZoneInfo
) -> tuple[list[Event], list[Event], tuple[datetime, datetime]]:
    """Learning material: qualifying ai_work + the user's screen_summary.

    Returns (ai_events, screen_events, window); superseded ai_work hidden
    (same rule as the timeline default).
    """
    start_local, end_local = _day_window(day, zone)
    window = (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )
    ai_events = db.scalars(
        select(Event)
        .where(
            Event.started_at >= window[0],
            Event.started_at <= window[1],
            Event.kind == "ai_work",
            Event.meta["memory_type"].astext.in_(LEARNING_MEMORY_TYPES),
            # hide superseded ai_work (same rule as the timeline default)
            Event.meta["superseded_by"].astext.is_(None),
        )
        .order_by(Event.started_at.asc(), Event.id.asc())
    ).fetchall()
    screen_events = db.scalars(
        select(Event)
        .where(
            Event.started_at >= window[0],
            Event.started_at <= window[1],
            Event.kind == "screen_summary",
        )
        .order_by(Event.started_at.asc(), Event.id.asc())
    ).fetchall()
    return ai_events, screen_events, window


def _learning_ai_line(e: Event, zone: ZoneInfo) -> str:
    t = e.started_at.astimezone(zone).strftime("%H:%M")
    agent = (e.device_id or "?").removeprefix("agent:")
    mtype = (e.meta or {}).get("memory_type") or "?"
    tags = ",".join(str(x) for x in (e.meta or {}).get("tags") or [])
    refs = ",".join(str(x) for x in (e.meta or {}).get("refs") or [])
    parts = [f"- [{t}] (ai_work/{agent} type={mtype}) {e.title or 'ai_work'}"]
    body = (e.text or "")[:LEARN_AI_CHARS]
    line = f"{parts[0]}: {body}"
    if tags:
        line += f" [tags: {tags}]"
    if refs:
        line += f" [refs: {refs}]"
    return line + f" [id: {e.id}]"


def _learning_ss_line(e: Event, zone: ZoneInfo) -> str:
    t = e.started_at.astimezone(zone).strftime("%H:%M")
    head = " ".join(x for x in (e.app, e.title) if x)
    prefix = f" ({e.device_id})" + (f" {head}:" if head else "")
    return f"- [{t}]{prefix} {(e.text or '')[:LEARN_SS_CHARS]}"


def _learning_messages(day: date_type, tz_name: str, material: str):
    system = (
        "あなたはPC作業ログから、再利用できる技術的な学びをブログ記事にする助手です。"
        "次の規則を厳守すること:\n"
        "- 材料に無い事実は書かない。推測・創作・一般論を混ぜない。"
        "感想や意見は本人の発言として材料にある範囲でだけ書く。\n"
        "- 読者は「同じ問題に当たった人（未来の本人）」。導入で症状を具体的に示し、"
        "原因と対処を順に語る技術ブログとして書く。\n"
        "- コマンド・設定値・コードは材料にあるものだけを、コードブロックで示す。\n"
        "- 見出し(## )は2〜5個（例:「症状」「原因」「対処」「確かめ方」）。"
        "H1(# )は書かない。800字以上（長くなってよい）。\n"
        "- 最後の「## 参考」には根拠となった記録の時刻 (HH:MM) と id（memory id・"
        "コミットID等、材料の [id: ...]）を並べる。\n"
        "- 出力は JSON オブジェクトのみ。前置き・後書き・コードブロックは書かない。"
    ) + _exemplar_section(
        "参考にするのは語り口（です・ます、一人称）だけ。"
        "記事の構成は上記の規則（症状→原因→対処の順、見出し、「## 参考」）を"
        "優先すること。"
    )
    user = (
        f"日付: {day.isoformat()}（時刻は {tz_name} の24時間表記 (HH:MM) に統一）\n\n"
        "## 材料: この日の記録（ai_work=AI の作業で type が solution/cause/"
        "experiment/result/issue のもの、screen_summary=自分の画面記録）\n"
        f"{material}\n\n"
        "以上の材料だけから、再利用できる技術的な学びを話題ごとに記事化すること"
        f"（最大{MAX_LEARNING_POSTS}記事。材料が薄い話題は無理に作らない。"
        "学びが無ければ空配列）。各記事の topic は内容を表す半角英数字3〜8字の"
        "スラッグ。次の JSON を返すこと:\n"
        '{"articles": [{"topic": "slug", "title": "記事タイトル（内容が伝わる自然な'
        '日本語。記号で区切らない。40字まで）", '
        '"excerpt": "リード文（120字程度）", "tags": ["技術名等（最大5個）"], '
        '"markdown": "本文"}]}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _fake_learning_articles(
    day: date_type, zone: ZoneInfo, ai_events: list[Event], screen_events: list[Event]
) -> list[dict]:
    """Deterministic grouping by the first tag (E2E)."""
    groups: dict[str, list[Event]] = {}
    for e in ai_events:
        tags = [str(t) for t in (e.meta or {}).get("tags") or [] if str(t)]
        key = tags[0] if tags else "untagged"
        groups.setdefault(key, []).append(e)
    articles = []
    for idx, (tag, evs) in enumerate(list(groups.items())[:MAX_LEARNING_POSTS]):
        ref_lines = [
            f"- [{e.started_at.astimezone(zone).strftime('%H:%M')}] {e.title or 'ai_work'} [id: {e.id}]"
            for e in evs
        ]
        markdown = (
            f"## 背景（fake）\n{day.isoformat()} の ai_work（tags: {tag}）"
            f"{len(evs)}件・screen_summary {len(screen_events)}件。\n\n"
            "## 問題・症状\nfake 出力のため省略。\n\n"
            "## 原因\nfake 出力のため省略。\n\n"
            "## 対処\nfake 出力のため省略。\n\n"
            "## 確かめ方\nfake 出力のため省略。\n\n"
            "## 参考\n" + "\n".join(ref_lines) + "\n"
        )
        articles.append(
            {
                "topic": f"t{idx + 1}",
                "title": f"学び {tag}（fake）",
                "excerpt": f"fake: {tag} の ai_work {len(evs)}件",
                "tags": [tag, "fake"],
                "markdown": markdown,
            }
        )
    return articles


def _replaceable_learning_post(post: BlogPost) -> bool:
    """Only untouched built-in drafts belong to the regeneration set.

    Authoring posts use random ids/slugs, even with author=auto. PATCH takes
    ownership as author=me; legacy edits/status changes are conservatively
    protected by updated_at != created_at (there is no edit-history column).
    """
    prefix = f"{post.date.isoformat()}-learning-"
    topic = post.slug.removeprefix(prefix)
    return (
        post.type == "learning"
        and post.author == "auto"
        and post.status == "draft"
        and post.model != "api"
        and post.updated_at == post.created_at
        and post.slug.startswith(prefix)
        and bool(topic)
        and post.id == blog_id_for(post.date, "learning", topic)
    )


def generate_learning_posts(
    db: Session, day: date_type, tz_name: str, zone: ZoneInfo, *, force: bool = False
) -> tuple[list[BlogPost], bool]:
    """Atomically replace only the day's untouched built-in learning drafts.

    Authored, edited, published and archived posts remain unchanged, including
    when a generated topic collides with their reserved id/slug. Without force,
    existing posts are returned as-is (idempotent).
    """
    existing_query = (
        select(BlogPost)
        .where(BlogPost.date == day, BlogPost.type == "learning")
        .order_by(BlogPost.created_at.asc(), BlogPost.id.asc())
    )
    existing = db.scalars(existing_query).fetchall()
    if existing and not force:
        return existing, False

    ai_events, screen_events, _window = collect_learning_material(db, day, zone)
    source_ids = [e.id for e in ai_events] + [e.id for e in screen_events]
    if config.LLM_PROVIDER == "fake":
        articles = _fake_learning_articles(day, zone, ai_events, screen_events)
        model = "fake"
    elif config.LLM_PROVIDER == "openai_compat":
        if not ai_events and not screen_events:
            articles = []
        else:
            lines = [_learning_ai_line(e, zone) for e in ai_events]
            lines += [_learning_ss_line(e, zone) for e in screen_events]
            if len(lines) > LEARN_MAX_RECORDS:
                lines = lines[:LEARN_MAX_RECORDS]
                lines.append("（このほかの記録は省略）")
            data = _parse_json_object(
                _chat(
                    config.REPORT_LLM_MODEL,
                    _learning_messages(day, tz_name, "\n".join(lines) or "（材料なし）"),
                )
            )
            articles = data.get("articles") or []
            if not isinstance(articles, list):
                raise BlogError("LLM JSON 'articles' is not a list")
        model = config.REPORT_LLM_MODEL
    else:
        raise BlogError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")

    # Re-read after the LLM call so edits/publication during generation are
    # protected too. Row locks keep those rows stable until replacement commits.
    replaceable: list[BlogPost] = []
    seen_slugs: set[str] = set()
    try:
        if force:
            existing = db.scalars(
                existing_query.with_for_update().execution_options(populate_existing=True)
            ).all()
            replaceable = [p for p in existing if _replaceable_learning_post(p)]
            seen_slugs = {p.slug for p in existing if p not in replaceable}

        posts: list[BlogPost] = []
        prepared: list[dict] = []
        for idx, art in enumerate(articles[:MAX_LEARNING_POSTS]):
            if force and not isinstance(art, dict):
                raise BlogError("LLM article is not an object")
            topic = _topic_slug(art.get("topic"), idx)
            slug = slug_for(day, "learning", topic)
            while slug in seen_slugs:  # duplicate topic or a protected post's slug
                topic = f"{topic}x{len(seen_slugs)}"
                slug = slug_for(day, "learning", topic)
            seen_slugs.add(slug)
            markdown = _norm_markdown(art.get("markdown"))
            markdown = yomiyasu.polish(markdown, "blog_learning")  # body only, fail-soft
            values = dict(
                day=day,
                type_="learning",
                topic=topic,
                title=_norm_title(art.get("title"), f"学び {day.isoformat()}"),
                excerpt=_norm_excerpt(art.get("excerpt"), markdown),
                markdown=markdown,
                tags=_norm_tags(art.get("tags")),
                source_event_ids=source_ids,
                model=model,
            )
            if force:
                prepared.append(values)
            else:
                posts.append(_upsert_post(db, **values))

        if force:
            # Every article is valid before any write. Flush deletes before inserts
            # that reuse an id/slug, then commit the entire replacement just once.
            for old in replaceable:
                db.delete(old)
            db.flush()
            for values in prepared:
                # Plain INSERT: a concurrent/conflicting row must cause rollback,
                # never silently overwrite a post outside the replacement set.
                posts.append(_upsert_post(db, **values, commit=False, upsert=False))
            db.commit()
    except Exception:
        if force:
            # Validation can fail after row locks are acquired, before any write.
            db.rollback()
        raise
    return posts, True


# --- entry points (API + scheduler) ---


def generate_blog(
    db: Session, day: date_type, tz_name: str, types: list[str], *, force: bool = False
) -> dict:
    """Generate the requested blog types for `day`; returns the API payload."""
    zone = ZoneInfo(tz_name)
    out: dict = {"date": day.isoformat(), "tz": tz_name}
    if "summary" in types:
        post, generated = generate_summary_post(db, day, tz_name, zone, force=force)
        out["summary"] = {"post": post, "generated": generated}
    else:
        out["summary"] = None
    if "learning" in types:
        posts, generated = generate_learning_posts(db, day, tz_name, zone, force=force)
        out["learning"] = [
            {"post": p, "generated": generated} for p in posts
        ]
    else:
        out["learning"] = []
    return out


def run_auto(day: date_type, tz_name: str, force: bool) -> bool:
    """One scheduled generation (both types); failures are logged only."""
    if config.BLOG_AUTO == "off":
        log.info("blog auto-generation disabled (BLOG_AUTO=off)")
        return False
    try:
        with SessionLocal() as db:
            out = generate_blog(db, day, tz_name, list(BLOG_TYPES), force=force)
            n = (1 if out["summary"] else 0) + len(out["learning"])
            return n > 0
    except Exception as e:  # noqa: BLE001 - log and retry at the next slot
        log.warning("blog auto-generation for %s failed: %s", day, e)
        return False


# --- authoring (P5-B): materials + create/update/rewrite for AIs and the user ---


def _clip(text: str | None, chars: int) -> str:
    t = text or ""
    return t if len(t) <= chars else t[:chars] + "…"


def blog_materials(
    db: Session,
    day: date_type,
    tz_name: str,
    *,
    max_records: int = MATERIAL_MAX_RECORDS,
    chars: int = MATERIAL_CHARS,
    ai_chars: int = MATERIAL_AI_CHARS,
    include_report: bool = True,
) -> dict:
    """A day's writing material as structured JSON (no LLM involved).

    Long texts are truncated to the given lengths, never summarized; each
    section is capped at max_records records (truncated flags report it).
    """
    zone = ZoneInfo(tz_name)
    sessions, events, counts, window = collect_material(db, day, zone)
    now = datetime.now(timezone.utc)

    session_items = []
    away = effective_away_intervals(db, *window)
    for s in sessions:
        started = max(s.started_at, window[0])
        end = min(s.ended_at or now, window[1])
        minutes, away_minutes = session_minutes(
            db, s, now, from_at=window[0], to_at=window[1], intervals=away
        )
        session_items.append(
            {
                "started_at": started.astimezone(zone).isoformat(),
                "ended_at": (
                    end.astimezone(zone).isoformat() if s.ended_at else None
                ),
                "minutes": minutes,
                "away_minutes": away_minutes,
                "title": s.title,
                "note": s.note,
                "status": s.status,
                "confirmed": s.confirmed,
            }
        )

    def own_item(e: Event) -> dict:
        return {
            "time": e.started_at.astimezone(zone).strftime("%H:%M"),
            "kind": e.kind,
            "device": e.device_id,
            "app": e.app,
            "title": e.title,
            "text": _event_line(e, zone) if e.kind == "away" else _clip(e.text, chars),
        }

    def ai_item(e: Event) -> dict:
        meta = e.meta or {}
        return {
            "time": e.started_at.astimezone(zone).strftime("%H:%M"),
            "agent": (e.device_id or "?").removeprefix("agent:"),
            "memory_type": meta.get("memory_type"),
            "title": e.title,
            "text": _clip(e.text, ai_chars),
            "tags": [str(t) for t in meta.get("tags") or []],
            "refs": [str(r) for r in meta.get("refs") or []],
            "id": str(e.id),
        }

    own = [e for e in events if e.kind in ("activity", "screen_summary", "away")]
    ai = [e for e in events if e.kind == "ai_work"]
    own_trunc = len(own) > max_records
    ai_trunc = len(ai) > max_records

    report_text = None
    if include_report:
        from . import daily_report

        rep = db.get(Event, daily_report.report_id_for_date(day))
        if rep is not None and rep.kind == "report":
            report_text = rep.text

    return {
        "date": day.isoformat(),
        "tz": tz_name,
        "sessions": session_items,
        "own_records": [own_item(e) for e in own[:max_records]],
        "ai_work": [ai_item(e) for e in ai[:max_records]],
        "daily_report": report_text,
        "counts": {
            "sessions": counts["sessions"],
            "activity": counts["activity"],
            "screen_summary": counts["screen_summary"],
            "ai_work": counts["ai_work"],
            "own_records_truncated": own_trunc,
            "ai_work_truncated": ai_trunc,
        },
    }


def _new_slug(db: Session, day: date_type, length: int = 8) -> str:
    """date + short alnum id, checked unique against the table."""
    for _ in range(20):
        suffix = "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(length))
        slug = f"{day.isoformat()}-{suffix}"
        if (
            db.scalars(select(BlogPost.id).where(BlogPost.slug == slug)).first()
            is None
        ):
            return slug
    raise BlogError("could not allocate a unique slug")  # ~36^8 tries split


_AUTHOR_RE = re.compile(r"^(me|auto|agent:\S+)$")


def create_post(
    db: Session,
    *,
    title: str,
    markdown: str,
    day: date_type,
    type_: str = "post",
    tags=None,
    author: str = "me",
    status: str = "draft",
    excerpt: str | None = None,
) -> BlogPost:
    """Create a post via the authoring API/MCP (random short-id slug)."""
    if type_ not in POST_TYPES:
        raise BlogError(f"unknown blog type: {type_}")
    if status not in ("draft", "published"):
        raise BlogError(f"invalid status for creation: {status}")
    if not _AUTHOR_RE.match(author):
        raise BlogError("author must be 'me', 'auto' or 'agent:<name>'")
    markdown = _norm_markdown(markdown)
    post = BlogPost(
        id=uuid.uuid4(),
        date=day,
        type=type_,
        slug=_new_slug(db, day),
        title=mask_secrets(_norm_title(title, f"無題 {day.isoformat()}")),
        excerpt=mask_secrets(_norm_excerpt(excerpt, markdown)),
        markdown=mask_secrets(markdown),
        tags=_norm_tags(tags),
        source_event_ids=[],
        model="api",
        status=status,
        author=author,
    )
    db.add(post)
    db.commit()
    return post


def update_post(
    db: Session,
    post: BlogPost,
    *,
    title=None,
    markdown=None,
    tags=None,
    excerpt=None,
    status=None,
) -> BlogPost:
    """PATCH-style update; editing built-in learning content takes ownership."""
    if status is not None:
        if status not in POST_STATUSES:
            raise BlogError(f"unknown status: {status}")
        post.status = status
    if title is not None:
        post.title = mask_secrets(_norm_title(title, post.title))
    if markdown is not None:
        post.markdown = mask_secrets(_norm_markdown(markdown))
    if tags is not None:
        post.tags = _norm_tags(tags)
    if excerpt is not None:
        post.excerpt = mask_secrets(_norm_excerpt(excerpt, post.markdown))
    if (
        post.type == "learning"
        and post.author == "auto"
        and any(value is not None for value in (title, markdown, tags, excerpt))
    ):
        # Content saved through REST/MCP is now author-managed, so a later
        # force regeneration must not discard these edits.
        post.author = "me"
    db.commit()
    return post


def _rewrite_messages(post: BlogPost, instruction: str | None) -> list[dict]:
    system = (
        "あなたはブログ記事を書き直す助手です。次の規則を厳守すること:\n"
        "- 現記事にある事実は変えず、指示に無い新しい事実を付け加えない。\n"
        "- タイトルは内容が伝わる自然な日本語（記号で区切らない）。\n"
        "- 出力は JSON オブジェクトのみ。前置き・後書き・コードブロックは書かない。"
    )
    ask = instruction and instruction.strip()
    user = (
        "次のブログ記事を書き直してください。\n"
        f"依頼: {ask if ask else '読みやすく自然な記事に整える'}\n\n"
        f"## タイトル\n{post.title}\n\n## 本文\n{post.markdown}\n\n"
        '次の JSON を返すこと: {"title": "新しいタイトル", '
        '"excerpt": "リード文（120字程度）", "markdown": "新しい本文"}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def rewrite_post(db: Session, post: BlogPost, instruction: str | None) -> dict:
    """Ask the built-in LLM for a rewritten version; NOT saved (caller
    PATCHes it in). Returns {title, excerpt, markdown} (masked)."""
    if config.LLM_PROVIDER == "fake":
        ask = (instruction or "").strip() or "指示なし"
        markdown = f"{post.markdown}\n\n（fake rewrite: {ask}）"
        return {
            "title": post.title,
            "excerpt": post.excerpt,
            "markdown": markdown,
        }
    if config.LLM_PROVIDER != "openai_compat":
        raise BlogError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")
    data = _parse_json_object(
        _chat(config.REPORT_LLM_MODEL, _rewrite_messages(post, instruction))
    )
    markdown = _norm_markdown(data.get("markdown"))
    return {
        "title": mask_secrets(_norm_title(data.get("title"), post.title)),
        "excerpt": mask_secrets(_norm_excerpt(data.get("excerpt"), markdown)),
        "markdown": mask_secrets(markdown),
    }
