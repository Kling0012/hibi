"""FastAPI application: sync-server API (/v1)."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select, text as sql_text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import am_import, blog, config, daily_report, todo, todo_judge
from .away import effective_away_intervals, overlap_minutes, session_minutes
from .db import engine, get_db
from .embedding import embed_sync, embedding_worker, pending_count
from .links import session_links_for_times
from .models import (
    AUTO_LINK_KINDS,
    BlogPost,
    Device,
    Event,
    EventEmbedding,
    EVENT_KINDS,
    ImporterState,
    SESSION_STATUSES,
    Todo,
    TodoTimeEntry,
    WorkSession,
)
from .schemas import (
    ActiveSessionOut,
    BatchResult,
    BlogEntryOut,
    BlogGenerateIn,
    BlogGenerateOut,
    BlogPostBrief,
    BlogPostCreateIn,
    BlogPostDetail,
    BlogPostOut,
    BlogPostPatchIn,
    BlogPostRef,
    BlogPostsPage,
    BlogRewriteIn,
    BlogRewriteOut,
    BlogTagOut,
    DailyStat,
    DailyStatsResponse,
    DayCounts,
    DayResponse,
    DeviceIn,
    EmbeddingItem,
    EmbeddingsIn,
    EmbeddingsOut,
    EmbeddingsUsage,
    EventBatchIn,
    EventIn,
    EventOut,
    EventsPage,
    HealthResponse,
    ImporterOut,
    ImportersPage,
    ImporterRunOut,
    ReportDailyIn,
    ReportOut,
    SearchHit,
    SearchIn,
    SearchResponse,
    SessionCreateIn,
    SessionCreateOut,
    SessionEndIn,
    SessionOut,
    SessionPatchIn,
    SessionTotal,
    SessionsPage,
    TimelineItem,
    TimelineResponse,
    TimelineTodoEntry,
    TodoCreateIn,
    TodoCreateOut,
    TodoEntriesPage,
    TodoEntryOut,
    TodoOut,
    TodoPatchIn,
    TodosPage,
)
from .session_judge import session_judge_worker
from .todo_judge import todo_judge_worker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sync.api")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(embedding_worker()),
        asyncio.create_task(session_judge_worker()),
        asyncio.create_task(am_import.am_import_worker()),
        asyncio.create_task(daily_report.report_scheduler()),
        asyncio.create_task(todo_judge_worker()),
    ]
    # The MCP session manager runs for the app's whole lifetime (mounting it
    # does not start it by itself). A failure here must not take the REST
    # API down: enter/exit are guarded so only /mcp would misbehave.
    mcp_cm = None
    try:
        from . import mcp_server

        mcp_cm = mcp_server.mcp.session_manager.run()
        await mcp_cm.__aenter__()
    except Exception as e:  # noqa: BLE001 - fail-soft: MCP only
        log.error("MCP session manager failed to start: %s", e)
        mcp_cm = None
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if mcp_cm is not None:
            with contextlib.suppress(Exception):
                await mcp_cm.__aexit__(None, None, None)


app = FastAPI(title="MineContext sync-server", lifespan=lifespan)


def _ensure_device(db: Session, device_id: str, name: str = "", os_: str = "") -> None:
    stmt = (
        pg_insert(Device)
        .values(id=device_id, name=name, os=os_)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    db.execute(stmt)


@app.get("/healthz", response_model=HealthResponse)
def healthz(db: Session = Depends(get_db)):
    try:
        db.execute(sql_text("SELECT 1"))
        db_ok = "ok"
    except Exception as e:  # noqa: BLE001
        log.error("healthz db check failed: %s", e)
        db_ok = "fail"
    return HealthResponse(ok=db_ok == "ok", db=db_ok, embed_pending=pending_count())


@app.put("/v1/devices/{device_id}")
def put_device(device_id: str, body: DeviceIn, db: Session = Depends(get_db)):
    stmt = pg_insert(Device).values(
        id=device_id, name=body.name, os=body.os, last_seen_at=func.now()
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={"name": body.name, "os": body.os, "last_seen_at": func.now()},
    )
    db.execute(stmt)
    db.commit()
    device = db.get(Device, device_id)
    return jsonable_encoder(device)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _session_links(db: Session, events: list[EventIn]) -> dict[uuid.UUID, uuid.UUID]:
    """activity/screen_summary -> the session active at the event's time."""
    return session_links_for_times(
        db,
        [
            (ev.id, _as_utc(ev.started_at))
            for ev in events
            if ev.kind in AUTO_LINK_KINDS
        ],
    )


@app.post("/v1/events:batch", response_model=BatchResult)
def post_events_batch(body: EventBatchIn, db: Session = Depends(get_db)):
    _ensure_device(db, body.device_id)
    links = _session_links(db, body.events)
    incoming = {ev.id: ev for ev in body.events}
    existing = set(
        db.scalars(select(Event.id).where(Event.id.in_(incoming.keys()))).fetchall()
    )
    for ev in body.events:
        stmt = pg_insert(Event).values(
            id=ev.id,
            device_id=body.device_id,
            kind=ev.kind,
            started_at=ev.started_at,
            ended_at=ev.ended_at,
            app=ev.app,
            title=ev.title,
            text=ev.text,
            meta=ev.meta,
            source=ev.source,
            session_id=links.get(ev.id),
        )
        inserted = db.scalar(
            stmt.on_conflict_do_nothing(index_elements=["id"]).returning(Event.id)
        )
        if inserted is not None:
            continue
        # Lock after the insert attempt so concurrent first-time inserts are
        # also compared against the text that actually won the conflict.
        old_text = db.scalar(
            select(Event.text).where(Event.id == ev.id).with_for_update()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "device_id": body.device_id,
                "kind": ev.kind,
                "started_at": ev.started_at,
                "ended_at": ev.ended_at,
                "app": ev.app,
                "title": ev.title,
                "text": ev.text,
                "meta": ev.meta,
                "source": ev.source,
                "session_id": links.get(ev.id),
                "updated_at": func.now(),
            },
        )
        db.execute(stmt)
        if old_text != ev.text:
            db.execute(
                EventEmbedding.__table__.delete().where(
                    EventEmbedding.event_id == ev.id
                )
            )
    db.commit()
    updated = len(existing & set(incoming.keys()))
    accepted = len(incoming) - updated
    return BatchResult(accepted=accepted, updated=updated)


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_s, id_s = raw.split("|", 1)
        return datetime.fromisoformat(ts_s), uuid.UUID(id_s)
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid cursor") from e


def _encode_cursor(ts: datetime, id_: uuid.UUID) -> str:
    raw = f"{ts.isoformat()}|{id_}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@app.get("/v1/events", response_model=EventsPage)
def list_events(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    device_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = select(Event).order_by(Event.started_at.asc(), Event.id.asc())
    if from_ is not None:
        q = q.where(Event.started_at >= from_)
    if to is not None:
        q = q.where(Event.started_at < to)
    if device_id is not None:
        q = q.where(Event.device_id == device_id)
    if kind is not None:
        if kind not in EVENT_KINDS:
            raise HTTPException(status_code=422, detail=f"unknown kind: {kind}")
        q = q.where(Event.kind == kind)
    if cursor:
        ts, id_ = _decode_cursor(cursor)
        q = q.where(tuple_(Event.started_at, Event.id) > (ts, id_))
    q = q.limit(limit)
    events = db.scalars(q).fetchall()
    next_cursor = (
        _encode_cursor(events[-1].started_at, events[-1].id)
        if len(events) == limit
        else None
    )
    return EventsPage(
        events=[EventOut.model_validate(e) for e in events], next_cursor=next_cursor
    )


@app.get("/v1/days/{day}", response_model=DayResponse)
def get_day(
    day: date,
    tz: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    tz_name = tz or config.TZ_DEFAULT
    try:
        zone = ZoneInfo(tz_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown tz: {tz_name}") from e
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = datetime.combine(day, time.max, tzinfo=zone).replace(
        microsecond=999999
    )
    q = (
        select(Event, Device.name)
        .join(Device, Event.device_id == Device.id, isouter=True)
        .where(Event.started_at >= start_local, Event.started_at <= end_local)
        .order_by(Event.started_at.asc(), Event.id.asc())
    )
    rows = db.execute(q).fetchall()
    events: list[EventOut] = []
    for ev, device_name in rows:
        out = EventOut.model_validate(ev)
        out.device_name = device_name
        events.append(out)
    count_rows = db.execute(
        select(Event.kind, func.count())
        .where(Event.started_at >= start_local, Event.started_at <= end_local)
        .group_by(Event.kind)
    ).fetchall()
    counts = [DayCounts(kind=k, count=c) for k, c in count_rows]
    return DayResponse(
        date=day.isoformat(),
        tz=tz_name,
        from_at=start_local.astimezone(timezone.utc),
        to_at=end_local.astimezone(timezone.utc),
        events=events,
        counts=counts,
    )


def _search_filters(params: dict, kinds, device_ids, from_, to) -> list[str]:
    """Shared SQL conditions for the vector and trgm sides of hybrid search."""
    conds = []
    if from_ is not None:
        conds.append("e.started_at >= :from_at")
        params["from_at"] = from_
    if to is not None:
        conds.append("e.started_at < :to_at")
        params["to_at"] = to
    if kinds:
        conds.append("e.kind = ANY(:kinds)")
        params["kinds"] = list(kinds)
    if device_ids:
        conds.append("e.device_id = ANY(:device_ids)")
        params["device_ids"] = list(device_ids)
    return conds


def _where(conds: list[str]) -> str:
    return (" WHERE " + " AND ".join(conds)) if conds else ""


@app.post("/v1/search", response_model=SearchResponse)
def search(body: SearchIn, db: Session = Depends(get_db)):
    for k in body.kinds or []:
        if k not in EVENT_KINDS:
            raise HTTPException(status_code=422, detail=f"unknown kind: {k}")
    hits = _hybrid_search(
        db,
        body.query,
        kinds=body.kinds,
        device_ids=body.device_ids,
        from_=body.from_,
        to=body.to,
        k=body.k,
    )
    return SearchResponse(
        query=body.query,
        hits=[
            SearchHit(
                event=h["event"],
                score=h["score"],
                vector_rank=h["vector_rank"],
                trgm_rank=h["trgm_rank"],
            )
            for h in hits
        ],
    )


def _hybrid_search(
    db: Session,
    query: str,
    *,
    kinds=None,
    device_ids=None,
    from_=None,
    to=None,
    k: int = 20,
) -> list[dict]:
    """Hybrid search core shared by POST /v1/search and the MCP server.

    Vector side (cosine over embeddings) + trgm side (similarity), merged
    with RRF k=60. The vector side is best-effort: on failure it is skipped
    and the trgm side still answers (fail-soft).
    """
    params: dict = {"q": query}
    shared = _search_filters(params, kinds, device_ids, from_, to)

    # Vector side runs on its own connection so a failure here never poisons
    # the transaction used by the trgm side below.
    try:
        qvec = embed_sync([query])[0]
        params["qv"] = "[" + ",".join(f"{x:.8g}" for x in qvec) + "]"
        with engine.connect() as conn:
            vec_rows = conn.execute(
                sql_text(
                    "SELECT ee.event_id FROM event_embeddings ee "
                    "JOIN events e ON e.id = ee.event_id"
                    + _where(shared)
                    + " ORDER BY ee.embedding <=> CAST(:qv AS vector) LIMIT 50"
                ),
                params,
            ).fetchall()
    except Exception as e:  # noqa: BLE001 - vector side is best-effort
        log.warning("vector search skipped: %s", e)
        vec_rows = []
    vec_ranks = {row[0]: i + 1 for i, row in enumerate(vec_rows)}

    # trgm side: similarity ranking, catches events without embeddings.
    trgm_rows = db.execute(
        sql_text(
            "SELECT e.id FROM events e"
            + _where(shared + ["similarity(e.text, :q) > 0.01"])
            + " ORDER BY similarity(e.text, :q) DESC, e.id LIMIT 50"
        ),
        params,
    ).fetchall()
    trgm_ranks = {row[0]: i + 1 for i, row in enumerate(trgm_rows)}

    rrf_k = 60
    scores: dict[uuid.UUID, float] = {}
    for ranks in (vec_ranks, trgm_ranks):
        for eid, rank in ranks.items():
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (rrf_k + rank)
    top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    if not top:
        return []

    ids = [t[0] for t in top]
    rows = db.execute(
        select(Event, Device.name)
        .join(Device, Event.device_id == Device.id, isouter=True)
        .where(Event.id.in_(ids))
    ).fetchall()
    by_id = {ev.id: (ev, name) for ev, name in rows}
    out: list[dict] = []
    for eid, score in top:
        if eid not in by_id:
            continue
        ev, name = by_id[eid]
        event_out = EventOut.model_validate(ev)
        event_out.device_name = name
        out.append(
            {
                "event": event_out,
                "score": score,
                "vector_rank": vec_ranks.get(eid),
                "trgm_rank": trgm_ranks.get(eid),
            }
        )
    return out


@app.post("/v1/embeddings", response_model=EmbeddingsOut)
def post_embeddings(body: EmbeddingsIn):
    """OpenAI-compatible embeddings relay (P1-C).

    Lets PC clients (MineContext) use this server's embedding provider
    (cloudflare in production) without holding the provider credentials.
    Plain relay: the request's model name is ignored (the server-side
    EMBED_MODEL is used), and input_type does not change the result (the
    provider uses no query/document prefix, same as the agent-memory
    gateway). Provider failures return 502; callers are expected to degrade
    gracefully (fail-soft). No auth (tailnet-only policy, like the rest).
    """
    texts = [body.input] if isinstance(body.input, str) else body.input
    if len(texts) > config.EMBED_RELAY_MAX_INPUTS:
        raise HTTPException(
            status_code=422,
            detail=f"too many inputs: {len(texts)} (max {config.EMBED_RELAY_MAX_INPUTS})",
        )
    try:
        vecs = embed_sync(texts)
    except Exception as e:  # noqa: BLE001 - relay this request's failure only
        raise HTTPException(
            status_code=502, detail=f"embedding provider failed: {e}"
        ) from e
    # Character count as a rough token estimate (real usage is not returned
    # by the internal embed_sync interface; OpenAI clients don't rely on it).
    chars = sum(len(t) for t in texts)
    return EmbeddingsOut(
        data=[EmbeddingItem(index=i, embedding=v) for i, v in enumerate(vecs)],
        model=config.EMBED_MODEL,
        usage=EmbeddingsUsage(prompt_tokens=chars, total_tokens=chars),
    )


@app.delete("/v1/events/{event_id}")
def delete_event(event_id: uuid.UUID, db: Session = Depends(get_db)):
    ev = db.get(Event, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    db.delete(ev)
    db.commit()
    return {"deleted": str(event_id)}


# --- Work sessions (P2-B): declare -> AI-judged end -> timeline ---


def _session_out(db: Session, s: WorkSession, now: datetime | None = None) -> SessionOut:
    out = SessionOut.model_validate(s)
    out.duration_minutes, out.away_minutes = session_minutes(
        db, s, now or datetime.now(timezone.utc)
    )
    return out


def _get_session(db: Session, session_id: uuid.UUID) -> WorkSession:
    s = db.get(WorkSession, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.post("/v1/sessions", response_model=SessionCreateOut)
def create_session(body: SessionCreateIn, db: Session = Depends(get_db)):
    started = _as_utc(body.started_at or datetime.now(timezone.utc))
    superseded: WorkSession | None = None
    for old in db.scalars(
        select(WorkSession).where(WorkSession.status == "active")
    ).fetchall():
        old.status = "ended"
        old.end_reason = "superseded"
        old.ended_at = max(old.started_at, started)  # clamp for backdated declares
        superseded = old
    s = WorkSession(
        title=body.title,
        note=body.note,
        device_id=body.device_id,
        started_at=started,
    )
    db.add(s)
    db.commit()
    return SessionCreateOut(
        session=_session_out(db, s),
        superseded=_session_out(db, superseded) if superseded else None,
    )


@app.get("/v1/sessions/active", response_model=ActiveSessionOut)
def get_active_session(db: Session = Depends(get_db)):
    s = db.scalars(
        select(WorkSession)
        .where(WorkSession.status == "active")
        .order_by(WorkSession.started_at.desc())
        .limit(1)
    ).first()
    return ActiveSessionOut(session=_session_out(db, s) if s else None)


@app.get("/v1/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: uuid.UUID, db: Session = Depends(get_db)):
    return _session_out(db, _get_session(db, session_id))


@app.get("/v1/sessions", response_model=SessionsPage)
def list_sessions(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if status is not None and status not in SESSION_STATUSES:
        raise HTTPException(status_code=422, detail=f"unknown status: {status}")
    q = select(WorkSession).order_by(WorkSession.started_at.asc(), WorkSession.id.asc())
    if from_ is not None:
        q = q.where(WorkSession.started_at >= from_)
    if to is not None:
        q = q.where(WorkSession.started_at < to)
    if status is not None:
        q = q.where(WorkSession.status == status)
    q = q.limit(limit)
    now = datetime.now(timezone.utc)
    sessions = [_session_out(db, s, now) for s in db.scalars(q).fetchall()]
    return SessionsPage(sessions=sessions)


@app.post("/v1/sessions/{session_id}/end", response_model=SessionOut)
def end_session(session_id: uuid.UUID, body: SessionEndIn, db: Session = Depends(get_db)):
    s = _get_session(db, session_id)
    if s.status == "cancelled":
        raise HTTPException(status_code=409, detail="session is cancelled")
    ended = _as_utc(body.ended_at or datetime.now(timezone.utc))
    if ended < s.started_at:
        raise HTTPException(status_code=422, detail="ended_at before started_at")
    s.status = "ended"
    s.ended_at = ended
    s.end_reason = "manual"
    s.confirmed = True
    db.commit()
    return _session_out(db, s)


@app.patch("/v1/sessions/{session_id}", response_model=SessionOut)
def patch_session(
    session_id: uuid.UUID, body: SessionPatchIn, db: Session = Depends(get_db)
):
    s = _get_session(db, session_id)
    data = body.model_dump(exclude_unset=True)
    if "started_at" in data and data["started_at"] is not None:
        data["started_at"] = _as_utc(data["started_at"])
    if "ended_at" in data and data["ended_at"] is not None:
        data["ended_at"] = _as_utc(data["ended_at"])
    new_started = data.get("started_at", s.started_at)
    new_ended = data.get("ended_at", s.ended_at)
    if new_ended is not None and new_ended < new_started:
        raise HTTPException(status_code=422, detail="ended_at before started_at")
    for field in ("title", "note", "started_at", "ended_at", "confirmed"):
        if field in data:
            setattr(s, field, data[field])
    if "ended_at" in data:
        if data["ended_at"] is None:
            if s.status == "ended":  # clearing the end reopens the session
                s.status = "active"
                s.end_reason = None
                s.end_confidence = None
        elif s.status == "active":
            s.status = "ended"
            if s.end_reason is None:
                s.end_reason = "manual"
    db.commit()
    return _session_out(db, s)


@app.post("/v1/sessions/{session_id}/cancel", response_model=SessionOut)
def cancel_session(session_id: uuid.UUID, db: Session = Depends(get_db)):
    s = _get_session(db, session_id)
    if s.status != "cancelled":
        s.status = "cancelled"
    db.commit()
    return _session_out(db, s)


# --- Todos (P6): user/AI todo list + auto time tracking ---


def _todo_out(t: Todo, tracked: dict | None = None) -> TodoOut:
    out = TodoOut.model_validate(t)
    tr = tracked or {}
    out.tracked_minutes = tr.get("tracked_minutes", 0.0)
    out.last_tracked_at = tr.get("last_tracked_at")
    out.tracking = tr.get("tracking", False)
    return out


def _get_todo(db: Session, todo_id: uuid.UUID) -> Todo:
    t = db.get(Todo, todo_id)
    if t is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return t


@app.get("/v1/todos", response_model=TodosPage)
def list_todos(
    status: str = Query(default="open"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if status not in todo.TODO_LIST_STATUSES:
        raise HTTPException(status_code=422, detail=f"unknown status: {status}")
    todos = todo.query_todos(db, status, limit)
    tracked = todo.tracked_bulk(db, [t.id for t in todos])
    return TodosPage(todos=[_todo_out(t, tracked.get(t.id)) for t in todos])


@app.post("/v1/todos", response_model=TodoCreateOut)
def post_todo(body: TodoCreateIn, db: Session = Depends(get_db)):
    """Create a user-added todo."""
    if body.source == "auto":
        raise HTTPException(status_code=422, detail="auto-created to-dos are disabled")
    t, deduped = todo.create_todo(
        db,
        title=body.title,
        note=body.note,
        urgency=body.urgency,
        due_date=body.due_date,
        source=body.source,
        evidence=body.evidence,
        device_id=body.device_id,
    )
    tracked = todo.tracked_bulk(db, [t.id])
    return TodoCreateOut(**_todo_out(t, tracked.get(t.id)).model_dump(), deduped=deduped)


@app.patch("/v1/todos/{todo_id}", response_model=TodoOut)
def patch_todo(
    todo_id: uuid.UUID, body: TodoPatchIn, db: Session = Depends(get_db)
):
    t = _get_todo(db, todo_id)
    data = body.model_dump(exclude_unset=True)
    for field in ("title", "note", "urgency", "due_date"):
        if field in data:
            setattr(t, field, data[field])
    if "status" in data and data["status"] != t.status:
        t.status = data["status"]
        if data["status"] == "done":
            t.done_at = datetime.now(timezone.utc)
        elif data["status"] == "open":
            t.done_at = None  # reopening clears the completion time
    db.commit()
    tracked = todo.tracked_bulk(db, [t.id])
    return _todo_out(t, tracked.get(t.id))


@app.delete("/v1/todos/{todo_id}", response_model=TodoOut)
def delete_todo(todo_id: uuid.UUID, db: Session = Depends(get_db)):
    """Archive only - no physical delete (the row and its time history stay)."""
    t = _get_todo(db, todo_id)
    t.status = "archived"
    db.commit()
    tracked = todo.tracked_bulk(db, [t.id])
    return _todo_out(t, tracked.get(t.id))


@app.get("/v1/todos/{todo_id}/entries", response_model=TodoEntriesPage)
def list_todo_entries(todo_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_todo(db, todo_id)
    entries = db.scalars(
        select(TodoTimeEntry)
        .where(TodoTimeEntry.todo_id == todo_id)
        .order_by(TodoTimeEntry.started_at.desc(), TodoTimeEntry.id.desc())
    ).fetchall()
    return TodoEntriesPage(
        entries=[
            TodoEntryOut(
                id=e.id,
                started_at=e.started_at,
                ended_at=e.ended_at,
                minutes=round(
                    (e.ended_at - e.started_at).total_seconds() / 60.0, 2
                ),
                source=e.source,
                confidence=e.confidence,
                reason=e.reason,
            )
            for e in entries
        ]
    )


@app.delete("/v1/todos/{todo_id}/entries/{entry_id}")
def delete_todo_entry(
    todo_id: uuid.UUID, entry_id: uuid.UUID, db: Session = Depends(get_db)
):
    """Physically delete one (wrongly tracked) time entry."""
    e = db.get(TodoTimeEntry, entry_id)
    if e is None or e.todo_id != todo_id:
        raise HTTPException(status_code=404, detail="entry not found")
    db.delete(e)
    db.commit()
    return {"ok": True}


@app.get("/v1/timeline", response_model=TimelineResponse)
def get_timeline(
    date_: date = Query(alias="date"),
    tz: str | None = Query(default=None),
    include_superseded: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    tz_name = tz or config.TZ_DEFAULT
    try:
        zone = ZoneInfo(tz_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown tz: {tz_name}") from e
    start_local = datetime.combine(date_, time.min, tzinfo=zone)
    end_local = datetime.combine(date_, time.max, tzinfo=zone).replace(
        microsecond=999999
    )
    window_start = start_local.astimezone(timezone.utc)
    window_end = end_local.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)

    sessions = db.scalars(
        select(WorkSession)
        .where(
            WorkSession.started_at <= window_end,
            or_(WorkSession.ended_at.is_(None), WorkSession.ended_at >= window_start),
        )
        .order_by(WorkSession.started_at.asc(), WorkSession.id.asc())
    ).fetchall()
    ev_q = (
        select(Event, Device.name)
        .join(Device, Event.device_id == Device.id, isouter=True)
        .where(or_(
            (Event.started_at >= window_start) & (Event.started_at <= window_end),
            (Event.kind == "away") & (Event.started_at <= window_end)
            & (Event.ended_at > window_start),
        ))
    )
    if not include_superseded:
        # Hide superseded ai_work records (the replacement is shown instead).
        # astext (->>) treats both a missing key and a JSON null as NULL.
        ev_q = ev_q.where(
            or_(Event.kind != "ai_work", Event.meta["superseded_by"].astext.is_(None))
        )
    rows = db.execute(
        ev_q.order_by(Event.started_at.asc(), Event.id.asc())
    ).fetchall()

    items: list[TimelineItem] = []
    for s in sessions:
        items.append(
            TimelineItem(type="session", t=s.started_at, session=_session_out(db, s, now))
        )
    for ev, device_name in rows:
        out = EventOut.model_validate(ev)
        out.device_name = device_name
        items.append(
            TimelineItem(
                type="ai_work" if ev.kind == "ai_work" else "event",
                t=ev.started_at,
                event=out,
            )
        )
    items.sort(
        key=lambda it: (
            it.t,
            0 if it.type == "session" else 1,
            str((it.session or it.event).id),
        )
    )
    away = effective_away_intervals(db, window_start, window_end)
    totals = []
    for s in sessions:
        if s.status == "cancelled":
            continue
        minutes, away_minutes = session_minutes(
            db, s, now, from_at=window_start, to_at=window_end, intervals=away
        )
        totals.append(SessionTotal(
            session_id=s.id,
            title=s.title,
            status=s.status,
            confirmed=s.confirmed,
            started_at=s.started_at,
            ended_at=s.ended_at,
            minutes=minutes,
            away_minutes=away_minutes,
        ))
    # P6: the day's todo time entries (any todo overlapping the day window)
    entry_rows = db.execute(
        select(TodoTimeEntry, Todo.title)
        .join(Todo, TodoTimeEntry.todo_id == Todo.id)
        .where(
            TodoTimeEntry.started_at <= window_end,
            TodoTimeEntry.ended_at >= window_start,
        )
        .order_by(TodoTimeEntry.started_at.asc(), TodoTimeEntry.id.asc())
    ).fetchall()
    todo_entries = [
        TimelineTodoEntry(
            id=e.id,
            todo_id=e.todo_id,
            todo_title=title,
            started_at=e.started_at,
            ended_at=e.ended_at,
            minutes=round((e.ended_at - e.started_at).total_seconds() / 60.0, 2),
        )
        for e, title in entry_rows
    ]
    return TimelineResponse(
        date=date_.isoformat(),
        tz=tz_name,
        from_at=window_start,
        to_at=window_end,
        items=items,
        session_totals=totals,
        total_minutes=round(sum(t.minutes for t in totals), 2),
        todo_entries=todo_entries,
    )


# --- Daily stats (heatmap): one row per calendar day in the requested tz ---


# Day grid + event aggregates + 5-min activity buckets. Shared away/session
# accounting below clips durations at day boundaries. All days are returned,
# including days with no records (LEFT JOIN -> zeros/NULL).
_DAILY_STATS_SQL = sql_text(
    """
    WITH day_list AS (
        -- pure calendar arithmetic (date + int): no session-TZ involvement
        SELECT (CAST(:from_d AS date) + n)::date AS d
        FROM generate_series(0, CAST(:to_d AS date) - CAST(:from_d AS date)) AS g(n)
    ),
    bounds AS (
        SELECT d,
               timezone(:tz, d::timestamp) AS s,
               timezone(:tz, (d + 1)::timestamp) AS e
        FROM day_list
    ),
    evday AS (
        SELECT b.d, e.kind, e.started_at,
               floor(extract(epoch FROM e.started_at) / 300) AS bucket
        FROM events e
        JOIN bounds b ON e.started_at >= b.s AND e.started_at < b.e
        WHERE e.started_at >= (SELECT min(s) FROM bounds)
          AND e.started_at <  (SELECT max(e) FROM bounds)
    ),
    evagg AS (
        SELECT d,
               count(*) AS events,
               count(*) FILTER (WHERE kind NOT IN ('ai_work', 'away')) AS mine,
               count(*) FILTER (WHERE kind = 'ai_work') AS ai_work,
               min(started_at) AS first_at,
               max(started_at) AS last_at
        FROM evday GROUP BY d
    ),
    buck AS (
        SELECT d,
               count(DISTINCT bucket) FILTER (WHERE kind NOT IN ('ai_work', 'away')) AS active_b,
               count(DISTINCT bucket) FILTER (WHERE kind = 'ai_work') AS ai_b,
               array_agg(DISTINCT bucket) FILTER (WHERE kind NOT IN ('ai_work', 'away')) AS mine_buckets
        FROM evday GROUP BY d
    )
    SELECT to_char(b.d, 'YYYY-MM-DD') AS date,
           coalesce(a.events, 0) AS events,
           coalesce(a.mine, 0) AS mine,
           coalesce(a.ai_work, 0) AS ai_work,
           coalesce(k.active_b, 0) * 5 AS active_minutes,
           coalesce(k.ai_b, 0) * 5 AS ai_active_minutes,
           0.0::float8 AS session_minutes,
           a.first_at, a.last_at, k.mine_buckets
    FROM bounds b
    LEFT JOIN evagg a ON a.d = b.d
    LEFT JOIN buck k ON k.d = b.d
    ORDER BY b.d
    """
)


@app.get("/v1/stats/daily", response_model=DailyStatsResponse)
def get_daily_stats(
    from_: date = Query(alias="from"),
    to: date = Query(...),
    tz: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    tz_name = tz or config.TZ_DEFAULT
    try:
        zone = ZoneInfo(tz_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown tz: {tz_name}") from e
    n_days = (to - from_).days + 1
    if n_days < 1:
        raise HTTPException(status_code=422, detail="from must be <= to")
    if n_days > 400:
        raise HTTPException(status_code=422, detail="range too large (max 400 days)")
    rows = db.execute(
        _DAILY_STATS_SQL, {"tz": tz_name, "from_d": from_, "to_d": to}
    ).mappings().fetchall()
    window_start = datetime.combine(from_, time.min, tzinfo=zone).astimezone(timezone.utc)
    window_end = datetime.combine(to + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    away = effective_away_intervals(db, window_start, window_end)
    now = datetime.now(timezone.utc)
    sessions = db.scalars(select(WorkSession).where(
        WorkSession.status != "cancelled",
        WorkSession.started_at < window_end,
        or_(WorkSession.ended_at.is_(None), WorkSession.ended_at > window_start),
    )).all()
    days = []
    for row in rows:
        stat = DailyStat.model_validate(row)
        day = date.fromisoformat(stat.date)
        a = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
        b = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
        stat.away_minutes = round(overlap_minutes(away, a, b), 2)
        # Subtract only the away portions of occupied five-minute buckets;
        # an absence elsewhere in the day cannot reduce unrelated activity.
        bucket_intervals = [
            (max(a, datetime.fromtimestamp(float(bucket) * 300, timezone.utc)),
             min(b, datetime.fromtimestamp(float(bucket) * 300 + 300, timezone.utc)))
            for bucket in row["mine_buckets"] or []
        ]
        excluded = sum(overlap_minutes(away, start, end) for start, end in bucket_intervals)
        active = round(max(0.0, stat.active_minutes - excluded), 2)
        stat.active_minutes = int(active) if active.is_integer() else active
        stat.session_minutes = round(sum(
            session_minutes(db, s, now, from_at=a, to_at=b, intervals=away)[0]
            for s in sessions if s.started_at < b and (s.ended_at or now) > a
        ), 2)
        days.append(stat)
    return DailyStatsResponse(tz=tz_name, days=days)


# --- Daily reports (P3-B): a day's timeline -> LLM -> kind='report' event ---


def _event_out(db: Session, ev: Event) -> EventOut:
    out = EventOut.model_validate(ev)
    if ev.device_id:
        d = db.get(Device, ev.device_id)
        out.device_name = d.name if d else None
    return out


@app.post("/v1/reports/daily", response_model=ReportOut)
def post_daily_report(body: ReportDailyIn, db: Session = Depends(get_db)):
    tz_name = body.tz or config.TZ_DEFAULT
    try:
        ZoneInfo(tz_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown tz: {tz_name}") from e
    try:
        ev, generated = daily_report.generate_daily_report(
            db, body.date, tz_name, force=body.force
        )
    except daily_report.ReportError as e:
        # only this request fails; the server keeps running (fail-soft)
        raise HTTPException(
            status_code=502, detail=f"daily report failed: {e}"
        ) from e
    return ReportOut(report=_event_out(db, ev), generated=generated)


@app.get("/v1/reports/daily", response_model=ReportOut)
def get_daily_report(
    date_: date = Query(alias="date"),
    db: Session = Depends(get_db),
):
    ev = db.get(Event, daily_report.report_id_for_date(date_))
    if ev is None or ev.kind != "report":
        raise HTTPException(status_code=404, detail="report not found")
    return ReportOut(report=_event_out(db, ev))


@app.get("/v1/reports", response_model=EventsPage)
def list_reports(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = (
        select(Event)
        .where(Event.kind == "report")
        .order_by(Event.started_at.asc(), Event.id.asc())
    )
    if from_ is not None:
        q = q.where(Event.started_at >= from_)
    if to is not None:
        q = q.where(Event.started_at < to)
    q = q.limit(limit)
    reports = db.scalars(q).fetchall()
    return EventsPage(
        events=[EventOut.model_validate(r) for r in reports], next_cursor=None
    )


# --- Blog (P5): auto-generated articles from the day's records ---


@app.post("/v1/blog/generate", response_model=BlogGenerateOut)
def post_blog_generate(body: BlogGenerateIn, db: Session = Depends(get_db)):
    tz_name = body.tz or config.TZ_DEFAULT
    try:
        ZoneInfo(tz_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown tz: {tz_name}") from e
    types = body.types if body.types is not None else list(blog.BLOG_TYPES)
    for t in types:
        if t not in blog.BLOG_TYPES:
            raise HTTPException(status_code=422, detail=f"unknown blog type: {t}")
    try:
        out = blog.generate_blog(db, body.date, tz_name, types, force=body.force)
    except blog.BlogError as e:
        # only this request fails; the server keeps running (fail-soft)
        raise HTTPException(status_code=502, detail=f"blog generation failed: {e}") from e
    summary = (
        BlogEntryOut(
            post=BlogPostOut.model_validate(out["summary"]["post"]),
            generated=out["summary"]["generated"],
        )
        if out["summary"]
        else None
    )
    learning = [
        BlogEntryOut(post=BlogPostOut.model_validate(x["post"]), generated=x["generated"])
        for x in out["learning"]
    ]
    return BlogGenerateOut(date=out["date"], tz=out["tz"], summary=summary, learning=learning)


def _blog_decode_cursor(cursor: str) -> tuple[date, datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        d_s, ts_s, id_s = raw.split("|", 2)
        return date.fromisoformat(d_s), datetime.fromisoformat(ts_s), uuid.UUID(id_s)
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid cursor") from e


def _blog_encode_cursor(d: date, ts: datetime, id_: uuid.UUID) -> str:
    raw = f"{d.isoformat()}|{ts.isoformat()}|{id_}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _blog_status_cond(status: str | None):
    """Default (None) serves published posts only — the public reader's
    view; ?status=draft|published|archived narrows, ?status=all disables."""
    if status is None:
        return BlogPost.status == "published"
    if status != "all":
        if status not in blog.POST_STATUSES:
            raise HTTPException(status_code=422, detail=f"unknown status: {status}")
        return BlogPost.status == status
    return None


@app.get("/v1/blog/posts", response_model=BlogPostsPage)
def list_blog_posts(
    type_: str | None = Query(default=None, alias="type"),
    tag: str | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if type_ is not None and type_ not in blog.POST_TYPES:
        raise HTTPException(status_code=422, detail=f"unknown type: {type_}")
    conds = []
    status_cond = _blog_status_cond(status)
    if status_cond is not None:
        conds.append(status_cond)
    if type_ is not None:
        conds.append(BlogPost.type == type_)
    if tag is not None:
        conds.append(BlogPost.tags.contains([tag]))
    if q is not None:
        like = f"%{q}%"
        conds.append(or_(BlogPost.title.ilike(like), BlogPost.markdown.ilike(like)))
    if from_ is not None:
        conds.append(BlogPost.date >= from_)
    if to is not None:
        conds.append(BlogPost.date <= to)
    if cursor:
        d, ts, id_ = _blog_decode_cursor(cursor)
        conds.append(
            tuple_(BlogPost.date, BlogPost.created_at, BlogPost.id) < (d, ts, id_)
        )
    query = (
        select(BlogPost)
        .where(*conds)
        .order_by(BlogPost.date.desc(), BlogPost.created_at.desc(), BlogPost.id.desc())
        .limit(limit)
    )
    posts = db.scalars(query).fetchall()
    next_cursor = (
        _blog_encode_cursor(posts[-1].date, posts[-1].created_at, posts[-1].id)
        if len(posts) == limit
        else None
    )
    return BlogPostsPage(
        posts=[BlogPostBrief.model_validate(p) for p in posts], next_cursor=next_cursor
    )


@app.get("/v1/blog/posts/{slug}", response_model=BlogPostDetail)
def get_blog_post(
    slug: str,
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    status_cond = _blog_status_cond(status)
    q = select(BlogPost).where(BlogPost.slug == slug)
    if status_cond is not None:
        q = q.where(status_cond)
    p = db.scalars(q).first()
    if p is None:
        raise HTTPException(status_code=404, detail="post not found")
    key = tuple_(BlogPost.date, BlogPost.created_at, BlogPost.id)
    # chronological neighbors within the same status scope:
    # prev = closest older, next = closest newer
    prev_q = (
        select(BlogPost)
        .where(key < (p.date, p.created_at, p.id))
        .order_by(BlogPost.date.desc(), BlogPost.created_at.desc(), BlogPost.id.desc())
        .limit(1)
    )
    nxt_q = (
        select(BlogPost)
        .where(key > (p.date, p.created_at, p.id))
        .order_by(BlogPost.date.asc(), BlogPost.created_at.asc(), BlogPost.id.asc())
        .limit(1)
    )
    if status_cond is not None:
        prev_q = prev_q.where(status_cond)
        nxt_q = nxt_q.where(status_cond)
    prev = db.scalars(prev_q).first()
    nxt = db.scalars(nxt_q).first()
    out = BlogPostDetail.model_validate(p)
    out.prev = BlogPostRef(slug=prev.slug, title=prev.title) if prev else None
    out.next = BlogPostRef(slug=nxt.slug, title=nxt.title) if nxt else None
    return out


_BLOG_TAGS_SQL = sql_text(
    "SELECT t.tag AS tag, count(*) AS count "
    "FROM blog_posts p, unnest(p.tags) AS t(tag) "
    "WHERE p.status = 'published' "
    "GROUP BY t.tag ORDER BY count(*) DESC, t.tag"
)


@app.get("/v1/blog/tags", response_model=list[BlogTagOut])
def list_blog_tags(db: Session = Depends(get_db)):
    rows = db.execute(_BLOG_TAGS_SQL).mappings().fetchall()
    return [BlogTagOut(tag=r["tag"], count=r["count"]) for r in rows]


# --- Blog authoring (P5-B): writing API for external AIs and the user ---


@app.get("/v1/blog/materials")
def get_blog_materials(
    date_: date = Query(alias="date"),
    tz: str | None = Query(default=None),
    max_records: int = Query(default=200, ge=1, le=500),
    chars: int = Query(default=500, ge=1, le=4000),
    ai_chars: int = Query(default=2000, ge=1, le=8000),
    include_report: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """A day's writing material as JSON (sessions + own records + ai_work +
    the daily report). Long texts are truncated to the given lengths, never
    LLM-summarized — the caller (an AI) writes the article."""
    tz_name = tz or config.TZ_DEFAULT
    try:
        ZoneInfo(tz_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown tz: {tz_name}") from e
    return blog.blog_materials(
        db,
        date_,
        tz_name,
        max_records=max_records,
        chars=chars,
        ai_chars=ai_chars,
        include_report=include_report,
    )


def _get_blog_post(db: Session, slug: str) -> BlogPost:
    p = db.scalars(select(BlogPost).where(BlogPost.slug == slug)).first()
    if p is None:
        raise HTTPException(status_code=404, detail="post not found")
    return p


@app.post("/v1/blog/posts", response_model=BlogPostOut)
def create_blog_post(body: BlogPostCreateIn, db: Session = Depends(get_db)):
    """Create a post (authoring API). slug = date + short alnum id; secrets
    are masked; the excerpt is generated when absent. Drafts are invisible
    to the public list until published."""
    try:
        post = blog.create_post(
            db,
            title=body.title,
            markdown=body.markdown,
            day=body.date,
            type_=body.type,
            tags=body.tags,
            author=body.author,
            status=body.status,
            excerpt=body.excerpt,
        )
    except blog.BlogError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return BlogPostOut.model_validate(post)


@app.patch("/v1/blog/posts/{slug}", response_model=BlogPostOut)
def patch_blog_post(
    slug: str, body: BlogPostPatchIn, db: Session = Depends(get_db)
):
    post = _get_blog_post(db, slug)
    try:
        post = blog.update_post(
            db,
            post,
            title=body.title,
            markdown=body.markdown,
            tags=body.tags,
            excerpt=body.excerpt,
            status=body.status,
        )
    except blog.BlogError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return BlogPostOut.model_validate(post)


@app.post("/v1/blog/posts/{slug}/publish", response_model=BlogPostOut)
def publish_blog_post(slug: str, db: Session = Depends(get_db)):
    post = _get_blog_post(db, slug)
    post.status = "published"
    db.commit()
    return BlogPostOut.model_validate(post)


@app.post("/v1/blog/posts/{slug}/unpublish", response_model=BlogPostOut)
def unpublish_blog_post(slug: str, db: Session = Depends(get_db)):
    post = _get_blog_post(db, slug)
    post.status = "draft"
    db.commit()
    return BlogPostOut.model_validate(post)


@app.delete("/v1/blog/posts/{slug}", response_model=BlogPostOut)
def delete_blog_post(slug: str, db: Session = Depends(get_db)):
    """Archive only — no physical delete (the row and slug stay reserved)."""
    post = _get_blog_post(db, slug)
    post.status = "archived"
    db.commit()
    return BlogPostOut.model_validate(post)


@app.post("/v1/blog/posts/{slug}/rewrite", response_model=BlogRewriteOut)
def rewrite_blog_post(
    slug: str, body: BlogRewriteIn, db: Session = Depends(get_db)
):
    """Ask the built-in LLM for a rewritten version of a post. The result is
    NOT saved — review it, then apply with PATCH /v1/blog/posts/{slug}."""
    post = _get_blog_post(db, slug)
    try:
        return blog.rewrite_post(db, post, body.instruction)
    except blog.BlogError as e:
        # only this request fails; the server keeps running (fail-soft)
        raise HTTPException(status_code=502, detail=f"rewrite failed: {e}") from e


# --- Importers (P3-A): agent-memory records -> ai_work events ---


@app.get("/v1/importers", response_model=ImportersPage)
def list_importers(db: Session = Depends(get_db)):
    rows = db.scalars(select(ImporterState).order_by(ImporterState.name)).fetchall()
    out = [ImporterOut.model_validate(r) for r in rows]
    for o in out:
        if o.name == am_import.IMPORTER_NAME:
            o.enabled = config.AM_IMPORT_ENABLED and bool(config.AM_BASE_URL)
    return ImportersPage(importers=out)


@app.post("/v1/importers/agent-memory/run", response_model=ImporterRunOut)
def run_agent_memory_import():
    try:
        stats = am_import.import_once()
    except Exception as e:  # noqa: BLE001 - surface to the caller; the
        # background worker keeps retrying regardless (fail-soft).
        raise HTTPException(
            status_code=502, detail=f"agent-memory import failed: {e}"
        ) from e
    return ImporterRunOut(ok=True, **stats)


# Management web UI for phone/iPad (static files, tailnet-only like the API).
from pathlib import Path  # noqa: E402

from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_UI_DIR = Path(__file__).parent / "ui"
if _UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=_UI_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/ui/")

# Blog reader pages (static files built separately; mounted only when the
# directory exists so the server runs fine without it).
_BLOG_DIR = Path(__file__).parent / "blog"
if _BLOG_DIR.is_dir():
    app.mount("/blog", StaticFiles(directory=_BLOG_DIR, html=True), name="blog")

# MCP server (P5-B, AI blog authoring): streamable HTTP at /mcp. Mounted
# last at "/" so every explicit route above keeps precedence; the sub-app
# serves its endpoint at /mcp inside itself. Guarded so a missing/broken
# MCP stack never takes the whole API down (fail-soft).
try:
    from . import mcp_server

    app.mount("/", mcp_server.mcp.streamable_http_app(), name="mcp")
except Exception as e:  # noqa: BLE001 - REST API works without MCP
    log.error("MCP server not mounted: %s", e)
