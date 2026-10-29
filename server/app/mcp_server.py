"""MCP server (P5-B): AI blog authoring over the sync-server's data.

A streamable-HTTP MCP endpoint (official `mcp` SDK, mounted at /mcp by
app.main) exposing the same operations as the REST authoring API, plus the
read-side day timeline and hybrid record search, so an external AI can
gather material, write a draft, and publish it after the user reviews.

House rule baked into every tool description: writes create DRAFTS first;
publishing happens only after the user (本人) has confirmed the content.
Read tools are marked 読み取り専用, write tools 書き込み. Tools are async
wrappers that run their (blocking, DB-bound) implementations on a worker
thread so the event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date as date_type
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import blog, config, todo
from .db import SessionLocal
from .models import BlogPost, Device, Event, Todo, WorkSession

mcp = FastMCP(
    name="hibi",
    stateless_http=True,  # each JSON-RPC request stands alone (no session)
    json_response=True,  # plain JSON responses (no SSE) keeps curl/e2e simple
    # The SDK only allows localhost Host headers by default, which rejects
    # clients reaching the server by its tailnet IP / name (421). The whole API
    # is tailnet-only without auth, so the check is off unless MCP_ALLOWED_HOSTS
    # lists hosts ("host:port" or "host:*", comma separated).
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(config.MCP_ALLOWED_HOSTS),
        allowed_hosts=config.MCP_ALLOWED_HOSTS,
        allowed_origins=[f"http://{h}" for h in config.MCP_ALLOWED_HOSTS],
    ),
)

_READ_NOTE = "（読み取り専用: 何も変更しない）"
_WRITE_NOTE = "（書き込み: 記事を変更する）"
_TODO_WRITE_NOTE = "（書き込み: ToDoを変更する）"
_FLOW_NOTE = (
    "運用: 記事は必ず下書き（draft）で作成し、本人が確認してから"
    " hibi_publish_post で公開する。"
)


def _tz(tz: str | None) -> str:
    name = tz or config.TZ_DEFAULT
    try:
        ZoneInfo(name)
    except Exception as e:
        raise ValueError(f"unknown tz: {name}") from e
    return name


def _day(value: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"date must be YYYY-MM-DD: {value!r}") from e


def _dt(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        out = datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"timestamp must be ISO 8601: {value!r}") from e
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _brief(p: BlogPost) -> dict:
    return {
        "slug": p.slug,
        "date": p.date.isoformat(),
        "type": p.type,
        "title": p.title,
        "excerpt": p.excerpt,
        "tags": p.tags,
        "status": p.status,
        "author": p.author,
        "updated_at": p.updated_at.isoformat(),
    }


def _full(p: BlogPost) -> dict:
    out = _brief(p)
    out.update(
        {
            "id": str(p.id),
            "markdown": p.markdown,
            "model": p.model,
            "created_at": p.created_at.isoformat(),
        }
    )
    return out


def _get(db, slug: str) -> BlogPost:
    p = db.scalars(select(BlogPost).where(BlogPost.slug == slug)).first()
    if p is None:
        raise ValueError(f"post not found: {slug}")
    return p


# --- read tools ---


def _timeline_impl(date_s: str, tz: str | None) -> dict:
    day = _day(date_s)
    tz_name = _tz(tz)
    zone = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = (
        datetime.combine(day, time.max, tzinfo=zone)
        .replace(microsecond=999999)
        .astimezone(timezone.utc)
    )
    with SessionLocal() as db:
        sessions = db.scalars(
            select(WorkSession)
            .where(
                WorkSession.started_at <= end,
                or_(WorkSession.ended_at.is_(None), WorkSession.ended_at >= start),
            )
            .order_by(WorkSession.started_at.asc())
        ).fetchall()
        rows = db.execute(
            select(Event, Device.name)
            .join(Device, Event.device_id == Device.id, isouter=True)
            .where(Event.started_at >= start, Event.started_at <= end)
            .order_by(Event.started_at.asc(), Event.id.asc())
        ).fetchall()
    items = []
    for s in sessions:
        items.append(
            {
                "type": "session",
                "time": s.started_at.astimezone(zone).strftime("%H:%M"),
                "title": s.title,
                "note": s.note,
                "status": s.status,
                "confirmed": s.confirmed,
            }
        )
    for ev, device_name in rows:
        if ev.kind == "ai_work" and (ev.meta or {}).get("superseded_by"):
            continue  # same rule as the timeline default
        items.append(
            {
                "type": ev.kind,
                "time": ev.started_at.astimezone(zone).strftime("%H:%M"),
                "device": device_name or ev.device_id,
                "app": ev.app,
                "title": ev.title,
                "text": (ev.text or "")[:400],
            }
        )
    return {"date": day.isoformat(), "tz": tz_name, "items": items}


@mcp.tool()
async def hibi_get_day_timeline(date: str, tz: str | None = None) -> dict:
    """その日（YYYY-MM-DD）の作業記録を時系列で返す。セッション宣言・
    自分の activity/screen_summary・AI の ai_work・日報が時刻順に並ぶ
    （superseded された ai_work は除外）。記事を書く前の下調べに使う。
    """ + _READ_NOTE
    return await asyncio.to_thread(_timeline_impl, date, tz)


def _search_impl(
    query: str,
    from_time: str | None,
    to_time: str | None,
    kinds: list[str] | None,
) -> dict:
    from . import main  # lazy: main mounts this module

    from_ = _dt(from_time)
    to = _dt(to_time)
    with SessionLocal() as db:
        hits = main._hybrid_search(db, query, kinds=kinds, from_=from_, to=to, k=20)
    return {
        "query": query,
        "hits": [
            {
                "time": h["event"].started_at.isoformat(),
                "kind": h["event"].kind,
                "device": h["event"].device_name or h["event"].device_id,
                "title": h["event"].title,
                "text": (h["event"].text or "")[:400],
                "score": round(h["score"], 6),
            }
            for h in hits
        ],
    }


@mcp.tool()
async def hibi_search_records(
    query: str,
    from_time: str | None = None,
    to_time: str | None = None,
    kinds: list[str] | None = None,
) -> dict:
    """作業記録をハイブリッド検索（意味ベクトル＋部分一致）する。
    query は検索語。from_time/to_time は ISO 8601 の時刻（例
    2026-10-01T00:00:00+09:00）。kinds は activity/screen_summary/note/
    todo/ai_work/report のリスト（省略時は全種別）。上位20件。
    """ + _READ_NOTE
    return await asyncio.to_thread(_search_impl, query, from_time, to_time, kinds)


def _materials_impl(date_s: str, tz: str | None) -> dict:
    with SessionLocal() as db:
        return blog.blog_materials(db, _day(date_s), _tz(tz))


@mcp.tool()
async def hibi_get_blog_materials(date: str, tz: str | None = None) -> dict:
    """その日のブログ執筆材料を JSON で返す: 宣言された作業と時間、
    自分の記録（activity/screen_summary）、AI の作業記録（ai_work）、
    日報本文。長文は切り詰めのみで要約はしない。記事を書くときの
    材料はこれ＋hibi_get_day_timeline。""" + _READ_NOTE
    return await asyncio.to_thread(_materials_impl, date, tz)


def _list_impl(status: str, type_: str | None, limit: int) -> dict:
    if status not in blog.POST_STATUSES + ("all",):
        raise ValueError(f"status must be one of {blog.POST_STATUSES} or 'all'")
    if type_ is not None and type_ not in blog.POST_TYPES:
        raise ValueError(f"unknown type: {type_}")
    conds = []
    if status != "all":
        conds.append(BlogPost.status == status)
    if type_ is not None:
        conds.append(BlogPost.type == type_)
    with SessionLocal() as db:
        posts = db.scalars(
            select(BlogPost)
            .where(*conds)
            .order_by(BlogPost.date.desc(), BlogPost.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).fetchall()
    return {"posts": [_brief(p) for p in posts]}


@mcp.tool()
async def hibi_list_posts(
    status: str = "published", type: str | None = None, limit: int = 20
) -> dict:
    """ブログ記事を一覧する（新しい順）。status は published（既定）/
    draft/archived/all。type は post/summary/learning。""" + _READ_NOTE
    return await asyncio.to_thread(_list_impl, status, type, limit)


def _get_impl(slug: str) -> dict:
    with SessionLocal() as db:
        return _full(_get(db, slug))


@mcp.tool()
async def hibi_get_post(slug: str) -> dict:
    """ブログ記事1件の全文（markdown・status・author 等）を返す。""" + _READ_NOTE
    return await asyncio.to_thread(_get_impl, slug)


# --- write tools (draft-first workflow) ---


def _create_impl(
    title: str,
    markdown: str,
    date_s: str,
    type_: str,
    tags: list[str] | None,
    author_name: str | None,
) -> dict:
    author = "me" if not author_name or author_name == "me" else f"agent:{author_name}"
    with SessionLocal() as db:
        post = blog.create_post(
            db,
            title=title,
            markdown=markdown,
            day=_day(date_s),
            type_=type_,
            tags=tags,
            author=author,
            status="draft",
        )
        return _full(post)


@mcp.tool()
async def hibi_create_draft(
    title: str,
    markdown: str,
    date: str,
    type: str = "post",
    tags: list[str] | None = None,
    author_name: str | None = None,
) -> dict:
    """ブログ記事を下書き（draft）として作成する。title/markdown は必須、
    date は YYYY-MM-DD。type は post（自由記事）/summary/learning。
    author_name は自分の名前（agent:<名前> として記録される）。
    作成だけでは公開されない。""" + _WRITE_NOTE + " " + _FLOW_NOTE
    return await asyncio.to_thread(
        _create_impl, title, markdown, date, type, tags, author_name
    )


def _update_impl(
    slug: str,
    title: str | None,
    markdown: str | None,
    tags: list[str] | None,
    excerpt: str | None,
    status: str | None,
) -> dict:
    with SessionLocal() as db:
        post = _get(db, slug)
        post = blog.update_post(
            db,
            post,
            title=title,
            markdown=markdown,
            tags=tags,
            excerpt=excerpt,
            status=status,
        )
        return _full(post)


@mcp.tool()
async def hibi_update_post(
    slug: str,
    title: str | None = None,
    markdown: str | None = None,
    tags: list[str] | None = None,
    excerpt: str | None = None,
    status: str | None = None,
) -> dict:
    """ブログ記事を更新する（指定した項目のみ）。status は draft/
    published/archived。本文の書き直しを保存するときにも使う。""" + (
        _WRITE_NOTE
        + " "
        + _FLOW_NOTE
    )
    return await asyncio.to_thread(
        _update_impl, slug, title, markdown, tags, excerpt, status
    )


def _set_status_impl(slug: str, status: str) -> dict:
    with SessionLocal() as db:
        post = _get(db, slug)
        post.status = status
        db.commit()
        return _full(post)


@mcp.tool()
async def hibi_publish_post(slug: str) -> dict:
    """下書き記事を公開（published）する。本人が内容を確認した後だけ
    呼ぶこと。""" + _WRITE_NOTE
    return await asyncio.to_thread(_set_status_impl, slug, "published")


@mcp.tool()
async def hibi_unpublish_post(slug: str) -> dict:
    """公開済み記事を下書き（draft）に戻す。""" + _WRITE_NOTE
    return await asyncio.to_thread(_set_status_impl, slug, "draft")


# --- Todo tools (P6): the shared todo list ---


def _list_todos_impl(status: str, limit: int) -> dict:
    if status not in todo.TODO_LIST_STATUSES:
        raise ValueError(f"status must be one of {todo.TODO_LIST_STATUSES}")
    with SessionLocal() as db:
        todos = todo.query_todos(db, status, max(1, min(limit, 200)))
        tracked = todo.tracked_bulk(db, [t.id for t in todos])
    return {"todos": [todo.todo_dict(t, tracked.get(t.id)) for t in todos]}


@mcp.tool()
async def hibi_list_todos(status: str = "open", limit: int = 50) -> dict:
    """ToDo を一覧する。status は open（既定・未完了）/done/all。
    各ToDoには記録からの自動計上を含む合計作業時間 tracked_minutes（分）と、
    直近10分に作業があれば true になる tracking が付く。""" + _READ_NOTE
    return await asyncio.to_thread(_list_todos_impl, status, limit)


def _add_todo_impl(
    title: str, note: str | None, urgency: str, due_date: str | None
) -> dict:
    if urgency not in ("high", "medium", "low"):
        raise ValueError("urgency must be high/medium/low")
    dd = _day(due_date) if due_date else None
    with SessionLocal() as db:
        t, _deduped = todo.create_todo(
            db, title=title, note=note, urgency=urgency, due_date=dd, source="me"
        )
        return todo.todo_dict(t)


@mcp.tool()
async def hibi_add_todo(
    title: str,
    note: str | None = None,
    urgency: str = "medium",
    due_date: str | None = None,
) -> dict:
    """ToDo を追加する（source='me'）。title は必須、urgency は
    high/medium/low、due_date は YYYY-MM-DD（省略可）。""" + _TODO_WRITE_NOTE
    return await asyncio.to_thread(_add_todo_impl, title, note, urgency, due_date)


def _complete_todo_impl(id_s: str) -> dict:
    try:
        tid = uuid.UUID(id_s)
    except ValueError as e:
        raise ValueError(f"id must be a UUID: {id_s!r}") from e
    with SessionLocal() as db:
        t = db.get(Todo, tid)
        if t is None:
            raise ValueError(f"todo not found: {id_s}")
        if t.status != "done":
            t.status = "done"
            t.done_at = datetime.now(timezone.utc)
            db.commit()
        tracked = todo.tracked_bulk(db, [t.id])
        return todo.todo_dict(t, tracked.get(t.id))


@mcp.tool()
async def hibi_complete_todo(id: str) -> dict:
    """ToDo を完了（done）にする。id は hibi_list_todos で取れる id。""" + _TODO_WRITE_NOTE
    return await asyncio.to_thread(_complete_todo_impl, id)
