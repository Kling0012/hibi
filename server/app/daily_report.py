"""Daily report generation (P3-B): a day's timeline -> LLM -> report event.

POST /v1/reports/daily collects the day's material in the requested tz —
declared work sessions, the user's activity/screen_summary events and AI
ai_work records (superseded ones hidden) — and asks the LLM (LLM_* settings
for transport; the report itself uses REPORT_LLM_MODEL, and over-long
material is first summarized per time block by REPORT_LLM_FLASH_MODEL) for a
Japanese Markdown daily report with six fixed sections. "Write nothing that
is not in the material" and "attach evidence event times" are part of the
prompt. The result is stored as a kind='report' event whose id is a uuid5
of the date (idempotent; force=true regenerates) on the synthetic 'server'
device, with meta={report_type, date, tz, model, source_counts, ...}.

LLM_PROVIDER=fake returns deterministic count-only Markdown (E2E).
The scheduler generates today's report at 23:55 (TZ_DEFAULT) and
force-regenerates yesterday's at 06:00 to capture late-night work;
failures are logged only and retried at the next slot (fail-soft).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import config
from . import yomiyasu
from .away import effective_away_intervals, session_minutes
from .db import SessionLocal
from .models import Device, Event, WorkSession

log = logging.getLogger("sync.daily_report")

# Synthetic device that owns generated report events (FK target).
REPORT_DEVICE_ID = "server"
REPORT_EVENT_KINDS = ("activity", "screen_summary", "ai_work", "away")
# Auto-generation slots (TZ_DEFAULT): (hour, minute, force). 23:55 writes
# the same day's report; 06:00 force-regenerates yesterday's so that
# late-night work makes it in.
SCHEDULE_SLOTS = ((23, 55, False), (6, 0, True))
BLOCK_HOURS = 4  # chunk granularity for the flash pre-summary


class ReportError(Exception):
    pass


def report_id_for_date(day: date_type) -> uuid.UUID:
    """Deterministic event id for a day's report (idempotent upserts)."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"minecontext:sync-server:daily-report:{day.isoformat()}"
    )


def _day_window(day: date_type, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = datetime.combine(day, time.max, tzinfo=zone).replace(
        microsecond=999999
    )
    return start_local, end_local


def collect_material(
    db: Session, day: date_type, zone: ZoneInfo
) -> tuple[list, list, dict, tuple[datetime, datetime]]:
    """The day's report material: sessions + own activity/screen_summary +
    ai_work (superseded hidden). Returns (sessions, events, counts, window)
    with the window in UTC."""
    start_local, end_local = _day_window(day, zone)
    window = (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )
    sessions = db.scalars(
        select(WorkSession)
        .where(
            WorkSession.started_at <= window[1],
            or_(WorkSession.ended_at.is_(None), WorkSession.ended_at >= window[0]),
        )
        .order_by(WorkSession.started_at.asc(), WorkSession.id.asc())
    ).fetchall()
    q = (
        select(Event)
        .where(
            or_(
                (Event.started_at >= window[0]) & (Event.started_at <= window[1]),
                (Event.kind == "away") & (Event.started_at <= window[1])
                & (Event.ended_at > window[0]),
            ),
            Event.kind.in_(REPORT_EVENT_KINDS),
            # hide superseded ai_work (same rule as the timeline default)
            or_(Event.kind != "ai_work", Event.meta["superseded_by"].astext.is_(None)),
        )
        .order_by(Event.started_at.asc(), Event.id.asc())
    )
    events = db.scalars(q).fetchall()
    counts = {
        "sessions": sum(1 for s in sessions if s.status != "cancelled"),
        "cancelled_sessions": sum(1 for s in sessions if s.status == "cancelled"),
        "unconfirmed_sessions": sum(
            1 for s in sessions if s.status != "cancelled" and not s.confirmed
        ),
        "activity": sum(1 for e in events if e.kind == "activity"),
        "screen_summary": sum(1 for e in events if e.kind == "screen_summary"),
        "ai_work": sum(1 for e in events if e.kind == "ai_work"),
        "away": sum(1 for e in events if e.kind == "away"),
    }
    return sessions, events, counts, window


def _fmt_hm(dt: datetime, zone: ZoneInfo) -> str:
    return dt.astimezone(zone).strftime("%H:%M")


def _sessions_text(
    sessions: list, zone: ZoneInfo, window: tuple[datetime, datetime], now: datetime,
    db: Session,
) -> str:
    """Sessions as material text; durations are clipped to the day window
    (same rule as stats/daily) so cross-midnight work splits correctly."""
    if not sessions:
        return "（この日の宣言は無い）"
    lines = []
    away = effective_away_intervals(db, *window)
    for s in sessions:
        started = max(s.started_at, window[0])
        end = min(s.ended_at or now, window[1])
        minutes, away_minutes = session_minutes(
            db, s, now, from_at=window[0], to_at=window[1], intervals=away
        )
        range_s = f"{_fmt_hm(started, zone)}〜{_fmt_hm(end, zone)}"
        title = s.title + (f"（{s.note}）" if s.note else "")
        if s.status == "cancelled":
            mark = "キャンセル済み（時間集計に含めない）"
        elif s.status == "active":
            mark = "進行中・※未確定"
        elif not s.confirmed:
            mark = f"終了（{s.end_reason}）・※未確定"
        else:
            mark = f"終了（{s.end_reason}）・確定済み"
        lines.append(f"- {range_s}（{minutes:g}分、離席{away_minutes:g}分を除外）{title} [{mark}]")
    return "\n".join(lines)


def _event_line(e: Event, zone: ZoneInfo) -> str:
    t = _fmt_hm(e.started_at, zone)
    if e.kind == "away":
        return f"離席 {t}–{_fmt_hm(e.ended_at or e.started_at, zone)}"
    if e.kind == "ai_work":
        agent = (e.device_id or "?").removeprefix("agent:")
        tags = ",".join(str(x) for x in (e.meta or {}).get("tags") or [])
        tag_s = f" [tags: {tags}]" if tags else ""
        head = e.title or "ai_work"
        return f"- [{t}] (ai_work/{agent}) {head}: {(e.text or '')[:500]}{tag_s}"
    head = " ".join(x for x in (e.app, e.title) if x)
    body = (e.text or "")[:300]
    prefix = f" ({e.kind}/{e.device_id})" + (f" {head}:" if head else "")
    return f"- [{t}]{prefix} {body}"


def _chunk_blocks(
    events: list, zone: ZoneInfo
) -> list[tuple[str, list[str]]]:
    """Group event lines into BLOCK_HOURS time blocks."""
    blocks: dict[int, list[str]] = {}
    for e in events:
        blocks.setdefault(e.started_at.astimezone(zone).hour // BLOCK_HOURS, []).append(
            _event_line(e, zone)
        )
    out = []
    for b in sorted(blocks):
        h0 = b * BLOCK_HOURS
        h1 = min(h0 + BLOCK_HOURS, 24)
        out.append((f"{h0:02d}:00〜{h1:02d}:00", blocks[b]))
    return out


_SECTION_TEMPLATE = """# 日報 {date}
## 1. 今日の概要
## 2. 作業ごとの時間
## 3. 自分がしたこと
## 4. AI がしたこと
## 5. 自分とAIの作業の関係
## 6. 明日に持ち越すこと"""


def _final_messages(
    day: date_type, tz_name: str, sessions_text: str, events_part: str
) -> list[dict]:
    system = (
        "あなたはPC作業ログから日報を書く助手です。次の規則を厳守すること:\n"
        "- 材料に無いことは書かない。推測・創作・一般論を混ぜない。\n"
        "- 各記述には根拠となった記録の時刻を (HH:MM) の形式で括弧付きで添える。\n"
        f"- 出力は「# 日報 {day.isoformat()}」で始まる Markdown のみ。前置き・後書き・コードブロックは書かない。"
    )
    user = (
        f"日付: {day.isoformat()}（時刻は {tz_name} の24時間表記 (HH:MM) に統一）\n\n"
        "## 材料1: 宣言された作業（work_sessions）\n"
        f"{sessions_text}\n\n"
        "## 材料2: この日の記録（時系列。activity/screen_summary=自分の作業、"
        "ai_work=AI の作業）\n"
        f"{events_part}\n\n"
        "## 材料3: 補足\n"
        "- ai_work の device は agent:<エージェント名>。title は「<type> · <先頭タグ>」形式。\n"
        "- 「※未確定」は AI 判定の終了を利用者がまだ確定していないもの。"
        "キャンセル済みは時間集計に含めない。\n\n"
        "以上の材料だけから、次の構成の日本語の日報 Markdown を書くこと"
        "（見出し行はこのまま使う。各節の内容は材料からのみ）:\n\n"
        + _SECTION_TEMPLATE.format(date=day.isoformat())
        + "\n\n各節の書き方:\n"
        "1. 今日の概要: 3〜5行\n"
        "2. 作業ごとの時間: 宣言された作業ごとの合計時間。未確定の作業には「※未確定」を付ける\n"
        "3. 自分がしたこと: 時系列の要点\n"
        "4. AI がしたこと: エージェント別・プロジェクト（タグ）別の要点\n"
        "5. 自分とAIの作業の関係: 同じプロジェクトでの分担など、材料から言えることだけ\n"
        "6. 明日に持ち越すこと: 材料に根拠があるものだけ。無ければ「なし」だけを書く\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _block_messages(day: date_type, label: str, text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "あなたはPC作業ログを要約する助手です。記録に無いことを追加しない。",
        },
        {
            "role": "user",
            "content": (
                f"{day.isoformat()} の {label} の作業記録を日本語の箇条書きに要約してください。"
                "各項目に時刻 (HH:MM) を添えること。"
                "activity/screen_summary=自分の作業、ai_work=AI の作業。\n\n" + text
            ),
        },
    ]


def _llm_chat(model: str, messages: list[dict]) -> str:
    if not config.LLM_BASE_URL:
        raise ReportError("LLM_BASE_URL is not set")
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY_FILE:
        try:
            with open(config.LLM_API_KEY_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
        except OSError as e:
            raise ReportError(f"cannot read api key file: {e}") from e
        if key:
            headers["Authorization"] = f"Bearer {key}"
    payload = {"model": model, "messages": messages, "temperature": 0.2}
    try:
        with httpx.Client(timeout=config.REPORT_LLM_TIMEOUT_SEC) as client:
            resp = client.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001 - caller decides (502 / log only)
        raise ReportError(f"LLM call failed ({model}): {type(e).__name__}: {e}") from e


def _strip_fences(md: str, day: date_type) -> str:
    """Remove accidental code fences and guarantee the H1 title."""
    md = md.strip()
    lines = md.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    md = "\n".join(lines).strip()
    if not md.startswith("# "):
        md = f"# 日報 {day.isoformat()}\n\n{md}"
    return md


def _llm_markdown(
    day: date_type, tz_name: str, zone: ZoneInfo, sessions_text: str, events: list
) -> tuple[str, dict]:
    """LLM path: direct material, or per-block flash summaries when long."""
    direct = (
        "\n".join(_event_line(e, zone) for e in events) or "（この日の記録は無い）"
    )
    extra: dict = {}
    if len(direct) > config.REPORT_CHUNK_CHARS:
        parts = []
        for label, lines in _chunk_blocks(events, zone):
            summary = _llm_chat(
                config.REPORT_LLM_FLASH_MODEL,
                _block_messages(day, label, "\n".join(lines)),
            )
            parts.append(f"### {label}\n{summary.strip()}")
        direct = "（長いため時間帯ごとの要約）\n" + "\n\n".join(parts)
        extra["chunked"] = True
        extra["chunk_model"] = config.REPORT_LLM_FLASH_MODEL
    content = _llm_chat(
        config.REPORT_LLM_MODEL,
        _final_messages(day, tz_name, sessions_text, direct),
    )
    return _strip_fences(content, day), extra


def fake_markdown(day: date_type, tz_name: str, counts: dict) -> str:
    """Deterministic count-only Markdown (LLM_PROVIDER=fake, E2E)."""
    lines = [
        f"# 日報 {day.isoformat()}（fake）",
        "",
        f"tz: {tz_name} / LLM_PROVIDER=fake（材料の件数のみを並べる）",
        "",
        "- sessions: {sessions}"
        "（未確定 {unconfirmed_sessions}、キャンセル {cancelled_sessions}）".format(
            **counts
        ),
        f"- activity: {counts['activity']}",
        f"- screen_summary: {counts['screen_summary']}",
        f"- ai_work: {counts['ai_work']}",
    ]
    return "\n".join(lines) + "\n"


def _ensure_report_device(db: Session) -> None:
    db.execute(
        pg_insert(Device)
        .values(id=REPORT_DEVICE_ID, name="sync-server", os="server")
        .on_conflict_do_nothing(index_elements=["id"])
    )


def generate_daily_report(
    db: Session, day: date_type, tz_name: str, *, force: bool = False
) -> tuple[Event, bool]:
    """Generate (or return the existing) daily report event for `day`.

    Returns (event, generated). Raises ReportError on LLM/config failures
    (nothing is stored then; the server keeps running - fail-soft).
    """
    try:
        zone = ZoneInfo(tz_name)
    except Exception as e:
        raise ReportError(f"unknown tz: {tz_name}") from e
    rid = report_id_for_date(day)
    existing = db.get(Event, rid)
    if existing is not None and not force:
        return existing, False

    sessions, events, counts, window = collect_material(db, day, zone)
    now = datetime.now(timezone.utc)
    sessions_text = _sessions_text(sessions, zone, window, now, db)
    if config.LLM_PROVIDER == "fake":
        markdown = fake_markdown(day, tz_name, counts)
        model = "fake"
        extra: dict = {}
    elif config.LLM_PROVIDER == "openai_compat":
        markdown, extra = _llm_markdown(day, tz_name, zone, sessions_text, events)
        model = config.REPORT_LLM_MODEL
    else:
        raise ReportError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")

    # Japanese body only (title/tags are separate fields); fail-soft, and a
    # no-op rewrite under LLM_PROVIDER=fake (see yomiyasu.polish).
    markdown = yomiyasu.polish(markdown, "daily_report")

    start_local, _ = _day_window(day, zone)
    meta = {
        "report_type": "daily",
        "date": day.isoformat(),
        "tz": tz_name,
        "model": model,
        "source_counts": counts,
        "generated_at": now.isoformat(),
        **extra,
    }
    values = dict(
        id=rid,
        device_id=REPORT_DEVICE_ID,
        kind="report",
        started_at=start_local.astimezone(timezone.utc),
        ended_at=None,
        app=None,
        title=f"日報 {day.isoformat()}",
        text=markdown,
        meta=meta,
        source="sync-server:daily-report",
        session_id=None,
    )
    stmt = pg_insert(Event).values(**values)
    set_ = {k: v for k, v in values.items() if k != "id"}
    set_["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=set_)
    _ensure_report_device(db)
    db.execute(stmt)
    db.commit()
    return db.get(Event, rid), True


def _next_slot(now_local: datetime) -> datetime:
    cands = []
    for offset in (0, 1):
        base = (now_local + timedelta(days=offset)).date()
        for hour, minute, _force in SCHEDULE_SLOTS:
            t = datetime(
                base.year, base.month, base.day, hour, minute,
                tzinfo=now_local.tzinfo,
            )
            if t > now_local:
                cands.append(t)
    return min(cands)


def run_auto(day: date_type, tz_name: str, force: bool) -> bool:
    """One scheduled generation; failures are logged only (fail-soft)."""
    try:
        with SessionLocal() as db:
            _, generated = generate_daily_report(db, day, tz_name, force=force)
            return generated
    except Exception as e:  # noqa: BLE001 - log and retry at the next slot
        log.warning("daily report auto-generation for %s failed: %s", day, e)
        return False


async def report_scheduler():
    """23:55 same-day report + 06:00 force-regeneration of yesterday
    (TZ_DEFAULT). Never raises; failures retry at the next slot."""
    if not config.REPORT_AUTO_ENABLED:
        log.info("daily report scheduler disabled (REPORT_AUTO_ENABLED off)")
        return
    tz_name = config.TZ_DEFAULT
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - bad config must not crash startup
        log.warning(
            "daily report scheduler: bad TZ_DEFAULT %r, scheduler idle", tz_name
        )
        return
    log.info("daily report scheduler on (tz=%s)", tz_name)
    while True:
        now_local = datetime.now(timezone.utc).astimezone(zone)
        nxt = _next_slot(now_local)
        await asyncio.sleep(max(1.0, (nxt - now_local).total_seconds()))
        fired = datetime.now(timezone.utc).astimezone(zone)
        slot = next(
            (
                f
                for h, m, f in SCHEDULE_SLOTS
                if (fired.hour, fired.minute) == (h, m)
            ),
            None,
        )
        if slot is None:
            continue  # overslept past the slot edge; wait for the next one
        target = (fired - timedelta(days=1)).date() if slot else fired.date()
        generated = await asyncio.to_thread(run_auto, target, tz_name, slot)
        log.info(
            "daily report auto: %s%s -> %s",
            target,
            " (force)" if slot else "",
            "generated" if generated else "skipped (already exists)",
        )
        # Blog posts (P5) right after each report slot, same day and same
        # force flag; failures are logged inside blog.run_auto (fail-soft).
        from . import blog  # local import: blog imports this module

        await asyncio.to_thread(blog.run_auto, target, tz_name, slot)
