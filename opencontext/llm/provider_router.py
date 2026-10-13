#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Priority-based AI provider router.

Routes (vision / chat / structured / embedding) each hold an ordered list of
provider ids. Requests try providers in order; on failure (rate limit, quota,
5xx, timeout, connection loss, invalid model, ...) the next provider is used.
Failed providers enter a cooldown (Retry-After header if present, otherwise
60s doubling up to 15min). When every provider fails, only the individual
request fails (fail-soft); the application keeps running.

Provider kinds:
- openai_compat: OpenAI-compatible chat/embeddings API (GLM, etc.)
- gemini_live:  Google Gemini Live API over websocket (audio responses;
                text is taken from output_audio_transcription)

Configuration (config.yaml ``ai`` section)::

    ai:
      output_language: ja
      providers:
        - id: gemini_live_38
          kind: gemini_live
          model: gemini-3.8-live
          api_key_file: ~/.config/minecontext/gemini-key
          options: {thinking_level: low}
        - id: glm_flash
          kind: openai_compat
          model: glm-5.3-flash
          base_url: https://open.bigmodel.cn/api/coding/paas/v4
          api_key_file: ~/.config/minecontext/zhipu-key
          options: {max_tokens: 8192}
      routes:
        vision: [gemini_live_38, glm_flash]
        chat: [glm_flash, glm_53]
        structured: [glm_flash]
        embedding: []

openai_compat providers may set ``options.base_url_from_sync: true`` instead
of ``base_url``: the URL is derived from ``sync.server_url`` (runtime value,
trailing "/" stripped) + "/v1" — the sync server relays an OpenAI-compatible
/v1 API (embeddings). When sync.server_url is empty the provider is built
unusable and dropped from its routes (fail-soft: only embedding-dependent
features stop; the application keeps running). The embedding route is
currently unused: local vector storage / search moved to the sync server,
but the mechanism is kept for future providers.

Legacy ``vlm_model`` / ``embedding_model`` sections are converted into a
single openai_compat provider per section when no ``ai.providers`` exist.
"""

import asyncio
import base64
import io
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from opencontext.utils.json_parser import parse_json_from_response
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

ROUTE_VISION = "vision"
ROUTE_CHAT = "chat"
ROUTE_STRUCTURED = "structured"
ROUTE_EMBEDDING = "embedding"
ROUTE_EXACT = "exact"  # verbatim screen-text capture (single-shot, P1-B)
ALL_ROUTES = (ROUTE_VISION, ROUTE_CHAT, ROUTE_STRUCTURED, ROUTE_EMBEDDING, ROUTE_EXACT)

# Language directive appended to prompts (design: output_language, default ja)
_LANGUAGE_NAMES = {
    "ja": "日本語 (Japanese)",
    "en": "English",
    "zh": "简体中文 (Simplified Chinese)",
}

COOLDOWN_BASE_SECONDS = 60
COOLDOWN_MAX_SECONDS = 900


class ProviderRouterError(RuntimeError):
    """All providers for a route failed (or none configured)."""


class _LoopRunner:
    """Dedicated event loop thread for all provider network I/O.

    The application calls the router from several different event loops
    (FastAPI loop, one asyncio.run() per screenshot batch in the processor
    thread, scheduler threads). httpx/websocket clients bind to the loop they
    first run on, so all provider clients live on this single runner loop and
    callers dispatch onto it.
    """

    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="provider-router")
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def submit(self, coro) -> "concurrent.futures.Future":
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


_RUNNER = _LoopRunner()


async def _dispatch(coro):
    """Await a coroutine on the runner loop from any event loop."""
    return await asyncio.wrap_future(_RUNNER.submit(coro))


def _dispatch_sync(coro, timeout: Optional[float] = None):
    """Run a coroutine on the runner loop from synchronous code."""
    return _RUNNER.submit(coro).result(timeout=timeout)


class _AsyncGenBridge:
    """Relay items produced on the runner loop to an async generator running
    on the caller's loop (thread-safe via a plain queue + to_thread)."""

    _DONE = object()

    def __init__(self):
        import queue as _queue

        self._queue = _queue.Queue()
        self.error: Optional[BaseException] = None

    def put(self, item):
        self._queue.put(item)

    def close(self):
        self._queue.put(self._DONE)

    def fail(self, error: BaseException):
        self.error = error
        self.close()

    async def items(self):
        import asyncio as _asyncio

        while True:
            item = await _asyncio.to_thread(self._queue.get)
            if item is self._DONE:
                if self.error:
                    raise self.error
                return
            yield item


def messages_contain_image(messages: List[Dict[str, Any]]) -> bool:
    """Detect OpenAI-style image parts in a message list."""
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


class _CooldownState:
    __slots__ = ("failures", "until", "last_error")

    def __init__(self):
        self.failures = 0
        self.until = 0.0
        self.last_error = ""


class BaseProvider:
    """Common provider behaviour: config parsing + API key resolution."""

    supports_stream = False
    supports_embedding = False

    def __init__(self, config: Dict[str, Any]):
        self.id: str = config.get("id") or "unnamed"
        self.kind: str = config.get("kind", "openai_compat")
        self.model: str = config.get("model", "")
        self.options: Dict[str, Any] = dict(config.get("options") or {})
        self.timeout: float = float(config.get("timeout", self.options.get("timeout", 300)))
        self._raw_key: str = str(config.get("api_key") or "")
        self._key_file: str = str(config.get("api_key_file") or "")
        self.api_key: Optional[str] = None
        self.unusable_reason: Optional[str] = None
        self._resolve_key()

    def _resolve_key(self):
        key = self._raw_key.strip()
        if not key and self._key_file:
            try:
                import os

                path = os.path.expanduser(self._key_file)
                with open(path, "r", encoding="utf-8") as f:
                    key = f.read().strip()
            except Exception as e:  # fail-soft: provider becomes unusable
                self.unusable_reason = f"cannot read api_key_file {self._key_file}: {e}"
                return
        if not key:
            self.unusable_reason = "no api_key or api_key_file configured"
            return
        if not self.model:
            self.unusable_reason = "no model configured"
            return
        self.api_key = key

    @property
    def usable(self) -> bool:
        return self.unusable_reason is None

    def safe_config(self) -> Dict[str, Any]:
        """Config without the key value (for API responses / logs)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "model": self.model,
            "base_url": getattr(self, "base_url", ""),
            "api_key_file": self._key_file,
            "has_api_key": bool(self.api_key),
            "options": self.options,
        }

    # -- subclasses implement -------------------------------------------------
    async def chat(self, messages, session_key: str = "") -> Tuple[str, Optional[Dict[str, int]]]:
        raise NotImplementedError

    async def chat_completion(self, messages, tools=None):
        raise NotImplementedError

    async def chat_stream(self, messages, tools=None):
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    async def test(self) -> Tuple[bool, str]:
        raise NotImplementedError


class OpenAICompatProvider(BaseProvider):
    supports_stream = True

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = str(config.get("base_url") or "")
        self.max_tokens = self.options.get("max_tokens")
        if self.usable and not self.base_url:
            self.unusable_reason = "no base_url configured"
            self.api_key = None
        self._client = None  # created lazily on the router loop

    def _ensure_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
        return self._client

    def _create_params(self, messages, tools=None, stream: bool = False):
        params: Dict[str, Any] = {"model": self.model, "messages": messages}
        if stream:
            params["stream"] = True
        if self.max_tokens:
            params["max_tokens"] = int(self.max_tokens)
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return params

    @staticmethod
    def _usage_of(response) -> Optional[Dict[str, int]]:
        usage = getattr(response, "usage", None)
        if not usage:
            return None
        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
                "completion_tokens": usage.get("completion_tokens", 0) or 0,
                "total_tokens": usage.get("total_tokens", 0) or 0,
            }
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

    async def chat(self, messages, session_key: str = "") -> Tuple[str, Optional[Dict[str, int]]]:
        response = await self._ensure_client().chat.completions.create(
            **self._create_params(messages)
        )
        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""
        return content, self._usage_of(response)

    async def chat_completion(self, messages, tools=None):
        return await self._ensure_client().chat.completions.create(
            **self._create_params(messages, tools=tools)
        )

    async def chat_stream(self, messages, tools=None):
        stream = await self._ensure_client().chat.completions.create(
            **self._create_params(messages, tools=tools, stream=True)
        )
        async for chunk in stream:
            yield chunk

    async def embed(self, text: str) -> List[float]:
        response = await self._ensure_client().embeddings.create(model=self.model, input=[text])
        return list(response.data[0].embedding)

    async def test(self) -> Tuple[bool, str]:
        try:
            content, _ = await asyncio.wait_for(
                self.chat([{"role": "user", "content": "Hi"}]), timeout=20
            )
            if content is not None:
                return True, "ok"
            return False, "empty response"
        except Exception as e:
            return False, _summarize_error(e)


class GeminiLiveProvider(BaseProvider):
    """Gemini Live (websocket) provider.

    One session per session key (route name) is kept alive and reused.
    Context window compression (sliding window) and session resumption are
    enabled; on disconnect the session is re-established using the stored
    resumption handle. Responses are audio; text comes from
    output_audio_transcription.
    """

    supports_stream = False

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_image_size = int(self.options.get("max_image_size", 1920))
        self.response_timeout = float(self.options.get("response_timeout", 60))
        self.thinking_level = self.options.get("thinking_level")
        self._client = None
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._connect_lock = asyncio.Lock()

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _connect_config(self, handle: Optional[str] = None) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "output_audio_transcription": {},
            "context_window_compression": {"sliding_window": {}},
            "session_resumption": {},
        }
        if self.thinking_level:
            config["thinking_config"] = {"thinking_level": self.thinking_level}
        if handle:
            config["session_resumption"] = {"handle": handle}
        return config

    async def _connect(self, session_key: str, handle: Optional[str] = None):
        client = self._get_client()
        # live.connect() is an @asynccontextmanager: enter it explicitly so
        # the session can be kept alive across requests.
        context = client.aio.live.connect(
            model=self.model, config=self._connect_config(handle)
        )
        session = await context.__aenter__()
        entry = {"context": context, "session": session, "lock": asyncio.Lock(), "handle": handle}
        self._sessions[session_key] = entry
        return entry

    async def _get_session(self, session_key: str):
        entry = self._sessions.get(session_key)
        if entry is None:
            async with self._connect_lock:
                entry = self._sessions.get(session_key)
                if entry is None:
                    entry = await self._connect(session_key)
        return entry

    async def aclose_all(self):
        for key, entry in list(self._sessions.items()):
            try:
                await entry["context"].__aexit__(None, None, None)
            except Exception:
                pass
        self._sessions.clear()

    @staticmethod
    def _extract_images_and_text(messages) -> Tuple[List[bytes], str]:
        """Flatten OpenAI-style messages into (raw image bytes list, text)."""
        image_urls: List[str] = []
        texts: List[str] = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and part.get("text"):
                        texts.append(part["text"])
                    elif part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if url:
                            image_urls.append(url)
        images = []
        for url in image_urls:
            raw = _decode_image_url(url)
            if raw is not None:
                images.append(raw)
        return images, "\n\n".join(texts)

    async def chat(self, messages, session_key: str = "") -> Tuple[str, Optional[Dict[str, int]]]:
        raw_images, text = self._extract_images_and_text(messages)
        images = [_to_jpeg(raw, self.max_image_size) for raw in raw_images]
        entry = await self._get_session(session_key or ROUTE_VISION)
        async with entry["lock"]:
            try:
                return await asyncio.wait_for(
                    self._turn(entry, images, text), timeout=self.response_timeout
                )
            except Exception as first_error:
                logger.warning(
                    f"[ProviderRouter] gemini_live {self.id} session error on route"
                    f" '{session_key}' ({_summarize_error(first_error)}), reconnecting"
                )
                # Drop the broken session and reconnect (resume when possible).
                handle = entry.get("handle")
                self._sessions.pop(session_key or ROUTE_VISION, None)
                try:
                    await entry["context"].__aexit__(None, None, None)
                except Exception:
                    pass
                entry = await self._connect(session_key or ROUTE_VISION, handle=handle)
                return await asyncio.wait_for(
                    self._turn(entry, images, text), timeout=self.response_timeout
                )

    async def _turn(self, entry, images: List[bytes], text: str):
        session = entry["session"]
        for image in images:
            await session.send_realtime_input(
                video={"data": image, "mime_type": "image/jpeg"}
            )
            await asyncio.sleep(1.0)
        await session.send_realtime_input(text=text)

        collected = ""
        usage: Optional[Dict[str, int]] = None
        async for message in session.receive():
            server_content = message.server_content
            if message.text:
                collected += message.text
            if server_content:
                if (
                    server_content.output_transcription
                    and server_content.output_transcription.text
                ):
                    collected += server_content.output_transcription.text
                if getattr(message, "go_away", None):
                    logger.info(
                        f"[ProviderRouter] gemini_live {self.id} received GoAway;"
                        " session will reconnect on next request"
                    )
            if message.usage_metadata:
                usage = {
                    "prompt_tokens": message.usage_metadata.prompt_token_count or 0,
                    "completion_tokens": message.usage_metadata.response_token_count or 0,
                    "total_tokens": message.usage_metadata.total_token_count or 0,
                }
            update = getattr(message, "session_resumption_update", None)
            if update and getattr(update, "resumable", False):
                entry["handle"] = update.new_handle
            if server_content and server_content.turn_complete:
                break
        return collected.strip(), usage

    # -- realtime stream API (P1-B live screen recording) ----------------------

    async def push_realtime_frame(self, session_key: str, jpeg: bytes):
        """Push one JPEG frame into a Live session without waiting for a
        response. The websocket is full-duplex, so this is safe to call while
        another coroutine is receiving. On send failure the session is dropped
        and re-established with the resumption handle (one retry)."""
        entry = await self._get_session(session_key)
        session = entry["session"]
        try:
            await session.send_realtime_input(
                video={"data": jpeg, "mime_type": "image/jpeg"}
            )
            return
        except Exception as first_error:
            logger.warning(
                f"[ProviderRouter] gemini_live {self.id} live push failed on"
                f" session '{session_key}' ({_summarize_error(first_error)}),"
                " reconnecting"
            )
        handle = entry.get("handle")
        self._sessions.pop(session_key, None)
        try:
            await entry["context"].__aexit__(None, None, None)
        except Exception:
            pass
        entry = await self._connect(session_key, handle=handle)
        await entry["session"].send_realtime_input(
            video={"data": jpeg, "mime_type": "image/jpeg"}
        )

    async def ask_realtime(self, session_key: str, text: str) -> Tuple[str, Optional[Dict[str, int]]]:
        """Send a text turn on a Live session and collect the (transcribed)
        response. Reconnects with the resumption handle once on failure."""
        entry = await self._get_session(session_key)
        async with entry["lock"]:
            try:
                return await asyncio.wait_for(
                    self._turn(entry, [], text), timeout=self.response_timeout
                )
            except Exception as first_error:
                logger.warning(
                    f"[ProviderRouter] gemini_live {self.id} live ask failed on"
                    f" session '{session_key}' ({_summarize_error(first_error)}),"
                    " reconnecting"
                )
                handle = entry.get("handle")
                self._sessions.pop(session_key, None)
                try:
                    await entry["context"].__aexit__(None, None, None)
                except Exception:
                    pass
                entry = await self._connect(session_key, handle=handle)
                return await asyncio.wait_for(
                    self._turn(entry, [], text), timeout=self.response_timeout
                )

    async def test(self) -> Tuple[bool, str]:
        try:
            text, _ = await asyncio.wait_for(
                self.chat(
                    [{"role": "user", "content": "接続テストです。「OK」とだけ答えてください。"}],
                    session_key="_test",
                ),
                timeout=30,
            )
            return True, f"ok: {text[:30]}"
        except Exception as e:
            return False, _summarize_error(e)


_IMAGE_DATA_URI = re.compile(r"^data:image/[a-zA-Z+.-]+;base64,(.*)$", re.S)


def _decode_image_url(url: str) -> Optional[bytes]:
    """Decode a data URI or raw base64 image url into raw bytes."""
    match = _IMAGE_DATA_URI.match(url)
    b64 = match.group(1) if match else url
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def _to_jpeg(raw: bytes, max_size: int) -> bytes:
    """Re-encode an image as JPEG with the long edge capped at max_size."""
    from PIL import Image

    image = Image.open(io.BytesIO(raw))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    width, height = image.size
    long_edge = max(width, height)
    if max_size and long_edge > max_size:
        scale = max_size / float(long_edge)
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _summarize_error(error: Any) -> str:
    message = str(error)
    return message if len(message) <= 200 else message[:197] + "..."


def _apply_sync_base_url(provider_config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply ``options.base_url_from_sync`` to one provider config.

    base_url is derived from ``sync.server_url`` (runtime value including
    user_setting.yaml) with trailing "/" stripped plus "/v1". When the sync
    URL is empty any configured base_url is removed so the provider is built
    unusable (fail-soft: only its routes go empty; nothing else stops).
    """
    options = provider_config.get("options") or {}
    if not options.get("base_url_from_sync"):
        return provider_config
    base_url = ""
    try:
        from opencontext.config.global_config import get_config

        server_url = str((get_config("sync") or {}).get("server_url") or "")
        server_url = server_url.strip().rstrip("/")
        if server_url:
            base_url = f"{server_url}/v1"
    except Exception as e:
        logger.warning(f"[ProviderRouter] cannot resolve sync.server_url: {e}")
    if base_url:
        return {**provider_config, "base_url": base_url}
    return {k: v for k, v in provider_config.items() if k != "base_url"}


def _retry_after_seconds(error: Any) -> Optional[float]:
    """Extract Retry-After from an OpenAI SDK error, if present."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            value = headers.get("retry-after")
            if value:
                return float(value)
        except Exception:
            return None
    return None


class ProviderRouter:
    """Routes requests to providers by priority with cooldown-based failover."""

    _instance: Optional["ProviderRouter"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        ai_config: Optional[Dict[str, Any]] = None,
        legacy_vlm_config: Optional[Dict[str, Any]] = None,
        legacy_embedding_config: Optional[Dict[str, Any]] = None,
    ):
        ai_config = ai_config or {}
        self.output_language = str(ai_config.get("output_language", "ja") or "")
        self.providers: Dict[str, BaseProvider] = {}
        self.routes: Dict[str, List[str]] = {route: [] for route in ALL_ROUTES}
        self._cooldowns: Dict[str, _CooldownState] = {}
        self._build(ai_config, legacy_vlm_config or {}, legacy_embedding_config or {})

    # -- construction ----------------------------------------------------------
    def _build(
        self,
        ai_config: Dict[str, Any],
        legacy_vlm: Dict[str, Any],
        legacy_embedding: Dict[str, Any],
    ):
        raw_providers = ai_config.get("providers") or []
        if raw_providers:
            for provider_config in raw_providers:
                try:
                    provider = self._create_provider(_apply_sync_base_url(provider_config))
                except Exception as e:
                    logger.error(
                        f"[ProviderRouter] failed to init provider "
                        f"{provider_config.get('id')}: {e}"
                    )
                    continue
                if provider.usable:
                    self.providers[provider.id] = provider
                else:
                    logger.warning(
                        f"[ProviderRouter] provider '{provider.id}' unusable: "
                        f"{provider.unusable_reason}"
                    )
            raw_routes = ai_config.get("routes") or {}
            for route in ALL_ROUTES:
                ids = raw_routes.get(route) or []
                self.routes[route] = [pid for pid in ids if pid in self.providers]
                dropped = [pid for pid in ids if pid not in self.providers]
                if dropped:
                    logger.warning(
                        f"[ProviderRouter] route '{route}' references unknown/"
                        f"unusable providers, dropped: {dropped}"
                    )
        else:
            # Backward compatibility: convert legacy single-model sections.
            self._convert_legacy(legacy_vlm, legacy_embedding)
        for route in ALL_ROUTES:
            logger.info(
                f"[ProviderRouter] route '{route}' -> {self.routes.get(route, [])}"
            )

    def _convert_legacy(self, legacy_vlm: Dict[str, Any], legacy_embedding: Dict[str, Any]):
        def usable(cfg: Dict[str, Any]) -> bool:
            return bool(cfg.get("api_key") and cfg.get("base_url") and cfg.get("model"))

        if usable(legacy_vlm):
            try:
                provider = OpenAICompatProvider(
                    {"id": "vlm", "kind": "openai_compat", **legacy_vlm}
                )
                if provider.usable:
                    self.providers["vlm"] = provider
                    for route in (ROUTE_VISION, ROUTE_CHAT, ROUTE_STRUCTURED):
                        self.routes[route] = ["vlm"]
                    logger.info("[ProviderRouter] converted legacy vlm_model to provider 'vlm'")
            except Exception as e:
                logger.error(f"[ProviderRouter] legacy vlm_model conversion failed: {e}")
        if usable(legacy_embedding):
            try:
                provider = OpenAICompatProvider(
                    {"id": "embedding", "kind": "openai_compat", **legacy_embedding}
                )
                if provider.usable:
                    self.providers["embedding"] = provider
                    self.routes[ROUTE_EMBEDDING] = ["embedding"]
                    logger.info(
                        "[ProviderRouter] converted legacy embedding_model to provider 'embedding'"
                    )
            except Exception as e:
                logger.error(
                    f"[ProviderRouter] legacy embedding_model conversion failed: {e}"
                )

    @staticmethod
    def _create_provider(config: Dict[str, Any]) -> BaseProvider:
        kind = config.get("kind", "openai_compat")
        if kind == "gemini_live":
            return GeminiLiveProvider(config)
        if kind == "openai_compat":
            return OpenAICompatProvider(config)
        raise ValueError(f"unknown provider kind: {kind}")

    # -- singleton --------------------------------------------------------------
    @classmethod
    def get_instance(cls) -> "ProviderRouter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls._from_config()
        return cls._instance

    @classmethod
    def _from_config(cls) -> "ProviderRouter":
        from opencontext.config.global_config import get_config

        try:
            return cls(
                ai_config=get_config("ai"),
                legacy_vlm_config=get_config("vlm_model"),
                legacy_embedding_config=get_config("embedding_model"),
            )
        except Exception as e:
            logger.error(f"[ProviderRouter] initialization failed: {e}")
            return cls(ai_config={})

    @classmethod
    def reset(cls):
        """Drop the singleton so the next access rebuilds from config."""
        with cls._lock:
            instance = cls._instance
            cls._instance = None
        if instance is not None:
            try:
                _RUNNER.submit(instance.aclose()).result(timeout=15)
            except Exception:
                pass  # fail-soft: old sessions are dropped on next use

    async def aclose(self):
        for provider in self.providers.values():
            if isinstance(provider, GeminiLiveProvider):
                try:
                    await provider.aclose_all()
                except Exception:
                    pass

    # -- helpers -----------------------------------------------------------------
    def has_route(self, route: str) -> bool:
        return bool(self._eligible(route, consider_cooldown=False))

    def _eligible(self, route: str, consider_cooldown: bool = True) -> List[BaseProvider]:
        chain = []
        for pid in self.routes.get(route, []):
            provider = self.providers.get(pid)
            if provider is None or not provider.usable:
                continue
            if consider_cooldown:
                state = self._cooldowns.get(pid)
                if state and time.time() < state.until:
                    continue
            chain.append(provider)
        return chain

    def _mark_failure(self, provider: BaseProvider, error: Any):
        state = self._cooldowns.setdefault(provider.id, _CooldownState())
        state.failures += 1
        state.last_error = _summarize_error(error)
        retry_after = _retry_after_seconds(error)
        if retry_after:
            seconds = min(float(retry_after), COOLDOWN_MAX_SECONDS)
        else:
            seconds = min(COOLDOWN_BASE_SECONDS * (2 ** (state.failures - 1)), COOLDOWN_MAX_SECONDS)
        state.until = time.time() + seconds
        logger.warning(
            f"[ProviderRouter] provider '{provider.id}' failed "
            f"({state.last_error}); cooldown {int(seconds)}s "
            f"(consecutive failures: {state.failures})"
        )

    def _mark_success(self, provider: BaseProvider):
        state = self._cooldowns.get(provider.id)
        if state is not None:
            state.failures = 0
            state.until = 0.0

    def _record_usage(self, provider: BaseProvider, usage: Optional[Dict[str, int]]):
        if not usage:
            return
        try:
            from opencontext.monitoring import record_token_usage

            record_token_usage(
                model=provider.id,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )
        except Exception:
            pass  # monitoring is optional (fail-soft)

    def _apply_output_language(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Append the output-language directive to the last message (mechanism
        for ai.output_language; prompts files themselves stay untranslated)."""
        language = (self.output_language or "").strip().lower()
        if not language:
            return messages
        name = _LANGUAGE_NAMES.get(language, language)
        directive = (
            f"\n\nIMPORTANT: Write all natural-language output in {name}. "
            "If you return JSON, keep keys and enum values unchanged but write "
            "all natural-language field values (title, summary, keywords, ...) in "
            f"{name}."
        )
        messages = [dict(m) for m in messages]
        for message in reversed(messages):
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = content + directive
                return messages
            if isinstance(content, list):
                for index in range(len(content) - 1, -1, -1):
                    part = content[index]
                    if isinstance(part, dict) and part.get("type") == "text":
                        new_part = dict(part)
                        new_part["text"] = part.get("text", "") + directive
                        content = list(content)
                        content[index] = new_part
                        message["content"] = content
                        return messages
            # skip messages without text (e.g. image-only)
        return messages

    def _fail(self, route: str, reason: str) -> ProviderRouterError:
        return ProviderRouterError(
            f"all providers failed for route '{route}': {reason}"
        )

    # -- public API (all network I/O runs on the dedicated runner loop) -----------
    async def generate(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        expect_json: bool = False,
        **kwargs,
    ) -> str:
        """Generate text, trying providers of the route in priority order."""
        return await _dispatch(
            self._generate(route, messages, expect_json=expect_json, **kwargs)
        )

    def generate_sync(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        expect_json: bool = False,
        **kwargs,
    ) -> str:
        """Synchronous variant of generate() for non-async threads."""
        return _dispatch_sync(
            self._generate(route, messages, expect_json=expect_json, **kwargs)
        )

    async def _generate(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        expect_json: bool = False,
        **kwargs,
    ) -> str:
        """Generate text, trying providers of the route in priority order."""
        chain = self._eligible(route)
        if not chain:
            raise self._fail(route, "no eligible provider configured or all in cooldown")
        messages = self._apply_output_language(messages)
        errors = []
        for provider in chain:
            start = time.time()
            try:
                if isinstance(provider, GeminiLiveProvider):
                    text, usage = await provider.chat(messages, session_key=route)
                else:
                    text, usage = await provider.chat(messages, **kwargs)
                if expect_json:
                    parsed = parse_json_from_response(text)
                    if parsed is None:
                        raise ValueError("response is not valid JSON")
                self._mark_success(provider)
                self._record_usage(provider, usage)
                logger.info(
                    f"[ProviderRouter] route '{route}' served by '{provider.id}' "
                    f"in {int((time.time() - start) * 1000)}ms "
                    f"(tokens: {(usage or {}).get('total_tokens', 'n/a')})"
                )
                return text
            except Exception as e:
                self._mark_failure(provider, e)
                errors.append(f"{provider.id}: {_summarize_error(e)}")
        raise self._fail(route, "; ".join(errors))

    async def generate_completion(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        **kwargs,
    ):
        """OpenAI-compatible ChatCompletion object (tool-calling flows)."""
        return await _dispatch(self._generate_completion(route, messages, tools=tools, **kwargs))

    def generate_completion_sync(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        **kwargs,
    ):
        """Synchronous variant for non-async threads."""
        return _dispatch_sync(
            self._generate_completion(route, messages, tools=tools, **kwargs)
        )

    async def _generate_completion(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        **kwargs,
    ):
        """OpenAI-compatible ChatCompletion object (tool-calling flows)."""
        chain = [
            p for p in self._eligible(route) if not isinstance(p, GeminiLiveProvider)
        ]
        if not chain:
            raise self._fail(route, "no openai_compat provider available for tool calls")
        messages = self._apply_output_language(messages)
        errors = []
        for provider in chain:
            start = time.time()
            try:
                response = await provider.chat_completion(messages, tools=tools, **kwargs)
                self._mark_success(provider)
                self._record_usage(provider, OpenAICompatProvider._usage_of(response))
                logger.info(
                    f"[ProviderRouter] route '{route}' completion served by "
                    f"'{provider.id}' in {int((time.time() - start) * 1000)}ms"
                )
                return response
            except Exception as e:
                self._mark_failure(provider, e)
                errors.append(f"{provider.id}: {_summarize_error(e)}")
        raise self._fail(route, "; ".join(errors))

    async def stream_completion(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        **kwargs,
    ):
        """Stream OpenAI-compatible chunks from the first healthy provider.

        The pump coroutine runs on the runner loop; chunks are relayed to the
        caller's loop through a thread-safe bridge so streaming works from any
        event loop.
        """
        bridge = _AsyncGenBridge()

        async def pump():
            try:
                async for chunk in self._stream_completion(route, messages, tools=tools, **kwargs):
                    bridge.put(chunk)
                bridge.close()
            except BaseException as e:
                bridge.fail(e)

        pump_future = _RUNNER.submit(pump())
        try:
            async for item in bridge.items():
                yield item
        finally:
            pump_future.cancel()

    async def _stream_completion(
        self,
        route: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        **kwargs,
    ):
        """Stream OpenAI-compatible chunks from the first healthy provider."""
        chain = [
            p for p in self._eligible(route) if not isinstance(p, GeminiLiveProvider)
        ]
        if not chain:
            raise self._fail(route, "no streaming provider available")
        messages = self._apply_output_language(messages)
        errors = []
        for provider in chain:
            yielded = False
            try:
                self._mark_success(provider)
                async for chunk in provider.chat_stream(messages, tools=tools, **kwargs):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                self._mark_failure(provider, e)
                if yielded:
                    # Content was already delivered; switching providers would
                    # duplicate it. Abort this request only (fail-soft).
                    raise ProviderRouterError(
                        f"stream from '{provider.id}' aborted after partial output: "
                        f"{_summarize_error(e)}"
                    )
                errors.append(f"{provider.id}: {_summarize_error(e)}")
                continue
        raise self._fail(route, "; ".join(errors))

    async def embed(
        self, text: str, output_dim: Optional[int] = None
    ) -> List[float]:
        """Embed text via the embedding route (raises when unconfigured)."""
        return await _dispatch(self._embed(text, output_dim=output_dim))

    def embed_sync(self, text: str, output_dim: Optional[int] = None) -> List[float]:
        """Synchronous variant for non-async threads."""
        return _dispatch_sync(self._embed(text, output_dim=output_dim))

    async def _embed(
        self, text: str, output_dim: Optional[int] = None
    ) -> List[float]:
        """Embed text via the embedding route (raises when unconfigured)."""
        chain = self._eligible(ROUTE_EMBEDDING)
        if not chain:
            raise ProviderRouterError(
                "embedding route not configured; embedding-dependent features are disabled"
            )
        errors = []
        for provider in chain:
            try:
                embedding = await provider.embed(text)
                dim = output_dim or provider.options.get("output_dim", 0)
                if dim and len(embedding) > dim:
                    import math

                    embedding = embedding[:dim]
                    norm = math.sqrt(sum(x * x for x in embedding))
                    if norm > 0:
                        embedding = [x / norm for x in embedding]
                self._mark_success(provider)
                self._record_usage(provider, None)
                return embedding
            except Exception as e:
                self._mark_failure(provider, e)
                errors.append(f"{provider.id}: {_summarize_error(e)}")
        raise self._fail(ROUTE_EMBEDDING, "; ".join(errors))

    # -- live stream (P1-B) ------------------------------------------------------
    def _live_provider(self) -> Optional[GeminiLiveProvider]:
        """First usable Gemini Live provider of the vision route (ignores
        cooldown: live pushes handle their own reconnection; cooldown is
        only applied when a push hard-fails)."""
        for provider in self._eligible(ROUTE_VISION, consider_cooldown=False):
            if isinstance(provider, GeminiLiveProvider) and provider.usable:
                return provider
        return None

    def has_live_provider(self) -> bool:
        return self._live_provider() is not None

    async def live_stream_frame(self, jpeg: bytes, session_key: str = "live_monitor") -> None:
        """Push one JPEG frame into the Live session (no response expected)."""
        provider = self._live_provider()
        if provider is None:
            raise ProviderRouterError(
                "no gemini_live provider on route 'vision'; live recording disabled"
            )
        await _dispatch(provider.push_realtime_frame(session_key, jpeg))

    async def live_ask(self, text: str, session_key: str = "live_monitor") -> str:
        """Ask a question on the Live session and return the transcribed answer."""
        provider = self._live_provider()
        if provider is None:
            raise ProviderRouterError(
                "no gemini_live provider on route 'vision'; live recording disabled"
            )
        start = time.time()
        try:
            answer, usage = await _dispatch(provider.ask_realtime(session_key, text))
        except Exception as e:
            self._mark_failure(provider, e)
            raise
        self._mark_success(provider)
        self._record_usage(provider, usage)
        logger.info(
            f"[ProviderRouter] live ask served by '{provider.id}' "
            f"in {int((time.time() - start) * 1000)}ms "
            f"(tokens: {(usage or {}).get('total_tokens', 'n/a')})"
        )
        return answer

    def status(self) -> Dict[str, Any]:
        """Snapshot for settings UI / monitoring."""
        now = time.time()
        providers = []
        for pid, provider in self.providers.items():
            state = self._cooldowns.get(pid)
            providers.append(
                {
                    **provider.safe_config(),
                    "usable": provider.usable,
                    "in_cooldown": bool(state and now < state.until),
                    "cooldown_remaining_s": max(0, int(state.until - now)) if state else 0,
                    "consecutive_failures": state.failures if state else 0,
                    "last_error": state.last_error if state else "",
                }
            )
        return {
            "providers": providers,
            "routes": {route: list(ids) for route, ids in self.routes.items()},
            "output_language": self.output_language,
        }

    async def test_provider_config(self, provider_config: Dict[str, Any]) -> Tuple[bool, str]:
        """Connection test for a (possibly unsaved) provider config."""
        return await _dispatch(self._test_provider_config(provider_config))

    async def _test_provider_config(self, provider_config: Dict[str, Any]) -> Tuple[bool, str]:
        """Connection test for a (possibly unsaved) provider config."""
        try:
            provider = self._create_provider(_apply_sync_base_url(provider_config))
        except Exception as e:
            return False, f"init failed: {_summarize_error(e)}"
        if not provider.usable:
            return False, provider.unusable_reason or "unusable"
        try:
            return await provider.test()
        finally:
            if isinstance(provider, GeminiLiveProvider):
                try:
                    await provider.aclose_all()
                except Exception:
                    pass


def get_provider_router() -> ProviderRouter:
    return ProviderRouter.get_instance()


def reinitialize_provider_router():
    ProviderRouter.reset()
    return ProviderRouter.get_instance()
