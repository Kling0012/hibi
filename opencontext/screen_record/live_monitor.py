#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Live screen monitor (P1-B).

The Electron frontend captures the screen as JPEG at ``capture.live_fps``
and posts each frame to ``POST /api/live/frame`` together with OS metadata
(frontmost app / window title / browser URL). This module:

1. streams every frame into a Gemini Live session (the first gemini_live
   provider of the 'vision' route) via ``send_realtime_input``;
2. every ``capture.live_summary_interval`` seconds asks the Live session what
   the user was doing during the interval and stores the answer as an
   activity record (meta: the interval's frontmost apps/titles/urls);
3. when the frontmost window changed or the picture changed a lot (dhash
   distance over ``capture.change_hash_threshold``) and at least
   ``capture.capture_min_interval`` seconds passed since the last capture,
   runs the frame through the 'exact' route (single-shot GLM) and stores a
   screen_summary record with the verbatim on-screen text.

Everything is fail-soft: Live/provider failures stop only the live stream
(item 1) while screen_summary (item 3) keeps working; recording failures
never propagate to the API caller. Excluded apps (``capture.exclude_apps``)
and ``capture.paused`` drop frames before anything is sent to a provider.
"""

import asyncio
import base64
import datetime
import json
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from opencontext.utils.image import calculate_bytes2phash
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Prompts (Japanese by design; the router additionally appends the
# output-language directive for the 'exact' route).
_LIVE_ASK_PROMPT = (
    "いまあなたにはこのユーザーの画面が動画として流れています。"
    "直近{seconds}秒間にユーザーが何をしていたかを、具体的に日本語で説明してください。"
    "使っていたアプリ・開いていたファイルやサイト・作業の内容に触れてください。"
    "見出しや箇条書きは使わず、2〜4文の文章で答えてください。"
    "{meta_hint}"
)

_EXACT_SYSTEM_PROMPT = (
    "あなたはパソコンの画面を記録するアシスタントです。"
    "画面に写っている主要な文字を、できるだけ原文のまま残すことが最優先です。"
    "要約や解釈で文字を言い換えないでください。"
)

_EXACT_USER_PROMPT = (
    "これは {time} の画面キャプチャです（アプリ: {app} / タイトル: {title} / URL: {url}）。"
    "画面に見える主要な文字（タイトルバー・メニュー・ファイル名・コード・メッセージ・"
    "URL・日本語文など）を、原文のまま列挙してください。"
    "最初の1行に内容を短く表す見出し（30字以内）を出し、その後に文字列を改行区切りで"
    "並べてください。自分の言葉で要約しないでください。"
)


def _hamming_hex(a: str, b: str) -> Optional[int]:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return None


class _Frame:
    __slots__ = ("jpeg", "app", "title", "url", "ts")

    def __init__(self, jpeg: bytes, app: str, title: str, url: str, ts: float):
        self.jpeg = jpeg
        self.app = app or ""
        self.title = title or ""
        self.url = url or ""
        self.ts = ts


class _Trigger:
    """Work item for the summarizer thread ('exact' or 'interval')."""

    def __init__(self, kind: str, frame: Optional[_Frame] = None,
                 interval_start: Optional[float] = None,
                 interval_end: Optional[float] = None,
                 interval_meta: Optional[List[Dict[str, str]]] = None):
        self.kind = kind  # 'exact' | 'interval'
        self.frame = frame
        self.interval_start = interval_start
        self.interval_end = interval_end
        self.interval_meta = interval_meta or []


class ScreenLiveMonitor:
    """Singleton live screen monitor (frame ingestion + background workers)."""

    _instance: Optional["ScreenLiveMonitor"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ScreenLiveMonitor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._state_lock = threading.Lock()
        self._frame_queue: "queue.Queue[_Frame]" = queue.Queue(maxsize=60)
        self._trigger_queue: "queue.Queue[_Trigger]" = queue.Queue(maxsize=20)
        self._stop = threading.Event()
        self._push_thread: Optional[threading.Thread] = None
        self._summarizer_thread: Optional[threading.Thread] = None
        self._started = False
        # interval / change-detection state
        self._interval_start: Optional[float] = None
        self._interval_meta: List[Dict[str, str]] = []
        self._last_hash: Optional[str] = None
        self._last_meta: tuple = ()
        self._last_summary_ts: float = 0.0
        self._last_drop_log: float = 0.0
        self._last_push_error_log: float = 0.0
        # runtime status for the settings UI
        self.status: Dict[str, Any] = {
            "frames_received": 0,
            "frames_pushed": 0,
            "frames_excluded": 0,
            "frames_dropped": 0,
            "live_activities": 0,
            "screen_summaries": 0,
            "live_available": None,
            "last_frame_at": None,
        }

    # -- lifecycle ---------------------------------------------------------------
    def start(self):
        if self._started:
            return
        self._started = True
        self._push_thread = threading.Thread(
            target=self._push_loop, daemon=True, name="screen-live-push")
        self._summarizer_thread = threading.Thread(
            target=self._summarizer_loop, daemon=True, name="screen-live-summarize")
        self._push_thread.start()
        self._summarizer_thread.start()
        logger.info("[ScreenLive] monitor started")

    # -- ingestion (called from the API route) ------------------------------------
    def submit_frame(self, jpeg: bytes, app: str = "", title: str = "", url: str = "") -> Dict[str, Any]:
        """Queue one frame. Returns a small dict describing the decision."""
        self.start()
        capture = self._capture_config()
        if capture.get("paused"):
            return {"accepted": False, "reason": "paused"}
        if self._is_excluded(capture, app):
            with self._state_lock:
                # a fresh interval starts when recording resumes
                self._interval_start = None
                self._interval_meta = []
            self.status["frames_excluded"] += 1
            return {"accepted": False, "reason": "excluded_app"}
        try:
            self._frame_queue.put_nowait(
                _Frame(jpeg, app, title, url, time.time()))
            self.status["frames_received"] += 1
            self.status["last_frame_at"] = datetime.datetime.now().isoformat()
            return {"accepted": True}
        except queue.Full:
            self.status["frames_dropped"] += 1
            now = time.time()
            if now - self._last_drop_log > 30:
                self._last_drop_log = now
                logger.warning("[ScreenLive] frame backlog, dropping frames")
            return {"accepted": False, "reason": "backlog"}

    def _capture_config(self) -> Dict[str, Any]:
        try:
            from opencontext.config.global_config import get_config

            return get_config("capture") or {}
        except Exception:
            return {}

    @staticmethod
    def _is_excluded(capture: Dict[str, Any], app: str) -> bool:
        exclude = capture.get("exclude_apps") or []
        app_l = (app or "").lower()
        if not app_l:
            return False
        for entry in exclude:
            if isinstance(entry, str) and entry.strip() and entry.strip().lower() in app_l:
                return True
        return False

    # -- push worker --------------------------------------------------------------
    def _push_loop(self):
        while not self._stop.is_set():
            try:
                frame = self._frame_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._handle_frame(frame)
            except Exception as e:
                logger.error(f"[ScreenLive] frame handling failed: {e}")

    def _handle_frame(self, frame: _Frame):
        capture = self._capture_config()
        new_hash = calculate_bytes2phash(frame.jpeg)

        # 1. stream the frame into the Live session (fail-soft)
        pushed = False
        try:
            from opencontext.llm.provider_router import get_provider_router

            router = get_provider_router()
            self.status["live_available"] = router.has_live_provider()
            if self.status["live_available"]:
                asyncio.run(router.live_stream_frame(frame.jpeg))
                pushed = True
                self.status["frames_pushed"] += 1
        except Exception as e:
            now = time.time()
            if now - self._last_push_error_log > 30:
                self._last_push_error_log = now
                logger.warning(f"[ScreenLive] live push failed: {e}")
            self.status["live_available"] = False

        # 2. change detection (hash distance + window switch)
        distance = None
        if new_hash is not None:
            if self._last_hash is not None:
                distance = _hamming_hex(new_hash, self._last_hash)
            self._last_hash = new_hash

        meta_key = (frame.app, frame.title, frame.url)
        window_changed = meta_key != self._last_meta
        self._last_meta = meta_key

        threshold = int(capture.get("change_hash_threshold", 10))
        big_change = distance is not None and distance > threshold
        min_interval = float(capture.get("capture_min_interval", 20))

        if (window_changed or big_change) and (frame.ts - self._last_summary_ts) >= min_interval:
            self._last_summary_ts = frame.ts
            self._enqueue_trigger(_Trigger("exact", frame=frame))

        # 3. interval bookkeeping
        with self._state_lock:
            if self._interval_start is None:
                self._interval_start = frame.ts
                self._interval_meta = []
            self._remember_meta(frame)
            elapsed = frame.ts - self._interval_start
            interval = float(capture.get("live_summary_interval", 30))
            if elapsed >= max(5.0, interval):
                trigger = _Trigger(
                    "interval",
                    interval_start=self._interval_start,
                    interval_end=frame.ts,
                    interval_meta=list(self._interval_meta),
                )
                self._interval_start = frame.ts
                self._interval_meta = []
                self._enqueue_trigger(trigger)

        if pushed:
            logger.debug(
                f"[ScreenLive] frame pushed (app={frame.app!r} hash_dist={distance} "
                f"window_changed={window_changed})")

    def _remember_meta(self, frame: _Frame):
        entry = {"app": frame.app, "title": frame.title, "url": frame.url}
        for existing in self._interval_meta:
            if (existing["app"], existing["title"], existing["url"]) == (
                entry["app"], entry["title"], entry["url"]
            ):
                return
        if len(self._interval_meta) < 100:
            self._interval_meta.append(entry)

    def _enqueue_trigger(self, trigger: _Trigger):
        try:
            self._trigger_queue.put_nowait(trigger)
        except queue.Full:
            logger.warning("[ScreenLive] summarizer backlog, dropping trigger")

    # -- summarizer worker ----------------------------------------------------------
    def _summarizer_loop(self):
        while not self._stop.is_set():
            try:
                trigger = self._trigger_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if trigger.kind == "exact":
                    self._process_exact(trigger.frame)
                elif trigger.kind == "interval":
                    self._process_interval(trigger)
            except Exception as e:
                logger.error(f"[ScreenLive] summarizer failed ({trigger.kind}): {e}")

    def _process_exact(self, frame: _Frame):
        from opencontext.llm.provider_router import get_provider_router

        router = get_provider_router()
        if not router.has_route("exact"):
            logger.info("[ScreenLive] 'exact' route not configured; screen_summary skipped")
            return
        now_str = datetime.datetime.fromtimestamp(frame.ts).strftime("%Y-%m-%d %H:%M:%S")
        user_prompt = _EXACT_USER_PROMPT.format(
            time=now_str,
            app=frame.app or "不明",
            title=frame.title or "不明",
            url=frame.url or "なし",
        )
        b64 = base64.b64encode(frame.jpeg).decode("ascii")
        messages = [
            {"role": "system", "content": _EXACT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            },
        ]
        text = asyncio.run(router.generate(route="exact", messages=messages))
        if not text or not text.strip():
            logger.info("[ScreenLive] empty exact response; screen_summary skipped")
            return
        title = text.strip().splitlines()[0][:60] if text.strip() else ""
        self._save_screen_summary(title, text.strip(), frame)

    def _save_screen_summary(self, title: str, content: str, frame: _Frame):
        from opencontext.storage.global_storage import get_storage

        meta = {
            "kind": "screen_summary",
            "app": frame.app,
            "title": frame.title,
            "url": frame.url,
            "frame_ts": frame.ts,
        }
        storage = get_storage()
        if not storage:
            logger.warning("[ScreenLive] storage unavailable; screen_summary dropped")
            return
        summary_id = storage.insert_screen_summary(
            title=title,
            content=content,
            app=frame.app,
            window_title=frame.title,
            url=frame.url,
            metadata=json.dumps(meta, ensure_ascii=False),
        )
        self.status["screen_summaries"] += 1
        logger.info(f"[ScreenLive] screen_summary saved (id={summary_id}, app={frame.app!r})")
        self._enqueue_outbox(
            kind="screen_summary",
            started_at=datetime.datetime.fromtimestamp(frame.ts),
            ended_at=datetime.datetime.fromtimestamp(frame.ts),
            app=frame.app,
            title=frame.title,
            text=content,
            meta={"url": frame.url},
        )

    def _process_interval(self, trigger: _Trigger):
        from opencontext.llm.provider_router import get_provider_router

        seconds = int(max(1, (trigger.interval_end - trigger.interval_start)))
        meta_hint = ""
        if trigger.interval_meta:
            compact = [
                f"{m.get('app', '')} — {m.get('title', '')} {('— ' + m['url']) if m.get('url') else ''}".strip()
                for m in trigger.interval_meta[:10]
            ]
            meta_hint = "（参考: この区間の前面ウィンドウ: " + " / ".join(compact) + "）"
        prompt = _LIVE_ASK_PROMPT.format(seconds=seconds, meta_hint=meta_hint)

        router = get_provider_router()
        if not router.has_live_provider():
            logger.info("[ScreenLive] no live provider; interval summary skipped")
            return
        answer = asyncio.run(router.live_ask(prompt))
        if not answer or not answer.strip():
            logger.info("[ScreenLive] empty live answer; activity skipped")
            return

        from opencontext.storage.global_storage import get_storage

        storage = get_storage()
        if not storage:
            logger.warning("[ScreenLive] storage unavailable; live activity dropped")
            return
        start_dt = datetime.datetime.fromtimestamp(trigger.interval_start)
        end_dt = datetime.datetime.fromtimestamp(trigger.interval_end)
        title = answer.strip().splitlines()[0][:60]
        meta = {"kind": "live", "windows": trigger.interval_meta}
        activity_id = storage.insert_activity(
            title=title,
            content=answer.strip(),
            resources=None,
            metadata=json.dumps(meta, ensure_ascii=False),
            start_time=start_dt,
            end_time=end_dt,
        )
        self.status["live_activities"] += 1
        logger.info(
            f"[ScreenLive] live activity saved (id={activity_id}, interval={seconds}s)")
        self._enqueue_outbox(
            kind="activity",
            started_at=start_dt,
            ended_at=end_dt,
            app=(trigger.interval_meta[0].get("app") if trigger.interval_meta else ""),
            title=(trigger.interval_meta[0].get("title") if trigger.interval_meta else ""),
            text=answer.strip(),
            meta={"windows": trigger.interval_meta},
        )

    @staticmethod
    def _enqueue_outbox(kind: str, started_at, ended_at, app: str, title: str, text: str, meta: Dict[str, Any]):
        try:
            from opencontext.screen_record.sync_outbox import enqueue_outbox_event

            enqueue_outbox_event(
                kind=kind,
                started_at=started_at,
                ended_at=ended_at,
                app=app,
                title=title,
                text=text,
                meta=meta,
                source="minecontext-desktop",
            )
        except Exception as e:
            logger.warning(f"[ScreenLive] outbox enqueue failed (non-fatal): {e}")


def get_live_monitor() -> ScreenLiveMonitor:
    return ScreenLiveMonitor.get_instance()
