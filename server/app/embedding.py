"""Embedding providers and the background embedding worker.

Providers:
- "fake": deterministic pseudo-embeddings (hashed word/char n-grams), no network.
- "openai_compat": OpenAI-compatible POST {base_url}/embeddings.
- "cloudflare": Workers AI OpenAI-compatible endpoint
  POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/embeddings
  with account id + API token read from an env-style file. Request/response
  shape matches the agent-memory gateway, which points graphiti's OpenAI client
  at the same /ai/v1 base URL with no instruction prefix on queries or
  documents (gateway/app/graph_store.py build_cloudflare_embedder_config).

The worker embeds events that have no row in event_embeddings yet. Any
failure only skips embedding for that batch (fail-soft); event ingest and
search never depend on it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import uuid

import httpx
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import config
from .db import SessionLocal, engine
from .models import Event, EventEmbedding

log = logging.getLogger("sync.embed")


def _ngrams(s: str) -> list[str]:
    """Lowercased word tokens and character bigrams (covers Japanese substrings)."""
    out: list[str] = []
    for word in s.lower().split():
        if word:
            out.append(word)
            for i in range(len(word) - 1):
                out.append(word[i : i + 2])
    # Character bigrams across the whole string for CJK text without spaces.
    compact = "".join(s.lower().split())
    for i in range(len(compact) - 1):
        bg = compact[i : i + 2]
        if bg not in out:
            out.append(bg)
    return out


def fake_embed(texts: list[str], dim: int) -> list[list[float]]:
    """Deterministic feature-hashing embeddings; cosine tracks n-gram overlap."""
    vecs: list[list[float]] = []
    for t in texts:
        v = [0.0] * dim
        for gram in _ngrams(t):
            h = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if h[4] & 1 else -1.0
            v[idx] += sign
        norm = math.sqrt(sum(x * x for x in v))
        if norm > 0:
            v = [x / norm for x in v]
        vecs.append(v)
    return vecs


class EmbedError(Exception):
    pass


def _parse_openai_embeddings(body: dict, texts: list[str]) -> list[list[float]]:
    """Parse an OpenAI-shaped /embeddings response into input order."""
    data = body["data"]
    vecs: list[list[float] | None] = [None] * len(texts)
    for i, item in enumerate(data):
        idx = item.get("index", i)  # cloudflare omits index on single inputs
        vecs[idx] = [float(x) for x in item["embedding"]]
    if any(v is None for v in vecs):
        raise EmbedError("provider returned missing embeddings")
    for v in vecs:
        if len(v) != config.EMBED_DIM:
            raise EmbedError(
                f"provider dim {len(v)} != EMBED_DIM {config.EMBED_DIM}"
            )
    return vecs  # type: ignore[return-value]


def _cloudflare_creds() -> tuple[str, str]:
    """Read CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN from an env-style file."""
    path = config.EMBED_CF_ENV_FILE
    if not path:
        raise EmbedError("EMBED_CF_ENV_FILE is not set")
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip().strip("'\"")
    except OSError as e:
        raise EmbedError(f"cannot read cloudflare env file: {e}") from e
    account = values.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = values.get("CLOUDFLARE_API_TOKEN", "")
    if not account or not token:
        raise EmbedError("cloudflare env file lacks CLOUDFLARE_ACCOUNT_ID/API_TOKEN")
    return account, token


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Embed a batch synchronously using the configured provider."""
    if not texts:
        return []
    if config.EMBED_PROVIDER == "fake":
        return fake_embed(texts, config.EMBED_DIM)
    if config.EMBED_PROVIDER == "openai_compat":
        if not config.EMBED_BASE_URL:
            raise EmbedError("EMBED_BASE_URL is not set")
        headers = {"Content-Type": "application/json"}
        if config.EMBED_API_KEY_FILE:
            try:
                with open(config.EMBED_API_KEY_FILE, "r", encoding="utf-8") as f:
                    key = f.read().strip()
                if key:
                    headers["Authorization"] = f"Bearer {key}"
            except OSError as e:
                raise EmbedError(f"cannot read api key file: {e}") from e
        payload = {"model": config.EMBED_MODEL, "input": texts}
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{config.EMBED_BASE_URL}/embeddings", json=payload, headers=headers
            )
        resp.raise_for_status()
        return _parse_openai_embeddings(resp.json(), texts)
    if config.EMBED_PROVIDER == "cloudflare":
        # Workers AI OpenAI-compat endpoint; plain model+input like the
        # agent-memory gateway (no instruction prefix on either side).
        account, token = _cloudflare_creds()
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account}/ai/v1/embeddings"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {"model": config.EMBED_MODEL, "input": texts}
        with httpx.Client(timeout=config.EMBED_CF_TIMEOUT_SEC) as client:
            resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return _parse_openai_embeddings(resp.json(), texts)
    raise EmbedError(f"unknown EMBED_PROVIDER: {config.EMBED_PROVIDER}")


def _pending_events(db, limit: int) -> list[tuple[uuid.UUID, str]]:
    rows = db.execute(
        sql_text(
            "SELECT e.id, e.text FROM events e "
            "LEFT JOIN event_embeddings ee ON ee.event_id = e.id "
            "WHERE ee.event_id IS NULL "
            "ORDER BY e.started_at DESC LIMIT :lim"
        ),
        {"lim": limit},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def embed_pending_once() -> int:
    """Embed one batch of pending events; returns count embedded. Fail-soft."""
    try:
        with SessionLocal() as db:
            pending = _pending_events(db, config.EMBED_BATCH)
        if not pending:
            return 0
        texts = [p[1] for p in pending]
        vecs = embed_sync(texts)
        saved = 0
        with SessionLocal() as db:
            for (eid, source_text), vec in zip(pending, vecs):
                # Serialize with text updates until commit. A plain comparison
                # without this lock could race with invalidation after the read.
                current_text = db.scalar(
                    select(Event.text).where(Event.id == eid).with_for_update()
                )
                if current_text != source_text:
                    continue
                stmt = pg_insert(EventEmbedding).values(
                    event_id=eid,
                    model=config.EMBED_MODEL,
                    embedding=vec,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["event_id"],
                    set_={
                        "model": stmt.excluded.model,
                        "embedding": stmt.excluded.embedding,
                        "embedded_at": func.now(),
                    },
                )
                db.execute(stmt)
                saved += 1
            db.commit()
        return saved
    except Exception as e:  # noqa: BLE001 - embedding must never crash the app
        log.warning("embedding batch failed (will retry next cycle): %s", e)
        return 0


def pending_count() -> int:
    try:
        with engine.connect() as conn:
            return conn.execute(
                sql_text(
                    "SELECT count(*) FROM events e "
                    "LEFT JOIN event_embeddings ee ON ee.event_id = e.id "
                    "WHERE ee.event_id IS NULL"
                )
            ).scalar_one()
    except Exception:  # noqa: BLE001
        return -1


async def embedding_worker():
    """Background loop; one cycle per EMBED_INTERVAL_SEC."""
    while True:
        try:
            n = await asyncio.to_thread(embed_pending_once)
            if n:
                log.info("embedded %d events", n)
        except Exception as e:  # noqa: BLE001 - never let the loop die
            log.warning("embedding worker cycle error: %s", e)
        await asyncio.sleep(config.EMBED_INTERVAL_SEC)
