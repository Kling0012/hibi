#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Live screen recording and sync routes (P1-B).

- POST /api/live/frame: one JPEG frame (binary body) + OS metadata (query
  params app/title/url) from the Electron frontend. Localhost only.
- POST /api/live/away/start|end: the user left the desk / came back; while
  away, frames are refused and one kind='away' event is queued on return.
- GET /api/live/status: monitor runtime status (for the settings UI).
- GET/POST /api/sync_settings: sync server configuration + outbox status.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from opencontext.config.global_config import GlobalConfig
from opencontext.server.middleware.auth import auth_dependency
from opencontext.server.utils import convert_resp
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["live"])

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_local(request: Request) -> bool:
    client = request.client
    return bool(client and client.host in _LOCAL_HOSTS)


@router.post("/api/live/frame", response_class=JSONResponse)
async def post_live_frame(request: Request, _auth: str = auth_dependency):
    """Receive one screen frame (JPEG binary) with frontmost-window metadata."""
    if not _is_local(request):
        return convert_resp(code=403, status=403, message="live frame endpoint is localhost only")
    try:
        jpeg = await request.body()
        if not jpeg:
            return convert_resp(code=400, status=400, message="empty body")
        app = request.query_params.get("app", "")
        title = request.query_params.get("title", "")
        url = request.query_params.get("url", "")

        from opencontext.screen_record.live_monitor import get_live_monitor

        result = get_live_monitor().submit_frame(jpeg, app=app, title=title, url=url)
        return convert_resp(data=result)
    except Exception as e:
        # fail-soft: never let a bad frame break the API
        logger.exception(f"Error handling live frame: {e}")
        return convert_resp(data={"accepted": False, "reason": "error"})


@router.get("/api/live/status")
async def get_live_status(_auth: str = auth_dependency):
    """Live monitor runtime status (frames, live availability, records)."""
    try:
        from opencontext.screen_record.live_monitor import get_live_monitor

        return convert_resp(data={"monitor": get_live_monitor().status})
    except Exception as e:
        logger.exception(f"Failed to get live status: {e}")
        return convert_resp(code=500, status=500, message=f"Failed to get live status: {str(e)}")


# ==================== Away detection ====================


class AwayStartRequest(BaseModel):
    started_at: str
    reason: str = "idle"


class AwayEndRequest(BaseModel):
    started_at: str
    ended_at: str
    reason: str = "idle"


@router.post("/api/live/away/start")
async def post_live_away_start(request: AwayStartRequest, _auth: str = auth_dependency):
    """The user left the desk: mark away (frames are refused until away/end)."""
    try:
        from opencontext.screen_record.live_monitor import get_live_monitor

        monitor = get_live_monitor()
        monitor.away_start(request.started_at, request.reason)
        return convert_resp(data={
            "away_since": monitor.status.get("away_since"),
            "away_reason": monitor.status.get("away_reason"),
        })
    except Exception as e:
        logger.exception(f"Failed to start away: {e}")
        return convert_resp(code=500, status=500, message=f"Failed to start away: {str(e)}")


@router.post("/api/live/away/end")
async def post_live_away_end(request: AwayEndRequest, _auth: str = auth_dependency):
    """The user came back: clear the away state; an away period of 60 s or
    longer queues one kind='away' event for the sync server."""
    try:
        from opencontext.screen_record.live_monitor import get_live_monitor

        result = get_live_monitor().away_end(request.started_at, request.ended_at, request.reason)
        return convert_resp(data=result)
    except Exception as e:
        logger.exception(f"Failed to end away: {e}")
        return convert_resp(code=500, status=500, message=f"Failed to end away: {str(e)}")


# ==================== Sync settings ====================


class SyncSettingsRequest(BaseModel):
    sync: dict


@router.get("/api/sync_settings")
async def get_sync_settings(_auth: str = auth_dependency):
    """Get sync configuration and outbox status"""
    try:
        from opencontext.screen_record.sync_outbox import get_sync_outbox

        config = (GlobalConfig.get_instance().get_config() or {}).get("sync") or {}
        return convert_resp(data={"sync": config, "status": get_sync_outbox().status()})
    except Exception as e:
        logger.exception(f"Failed to get sync settings: {e}")
        return convert_resp(code=500, status=500, message=f"Failed to get sync settings: {str(e)}")


@router.post("/api/sync_settings")
async def update_sync_settings(request: SyncSettingsRequest, _auth: str = auth_dependency):
    """Save the sync section, reload config and wake the outbox worker"""
    try:
        config_mgr = GlobalConfig.get_instance().get_config_manager()
        if not config_mgr:
            return convert_resp(code=500, status=500, message="Config manager not initialized")

        merged = dict((GlobalConfig.get_instance().get_config() or {}).get("sync") or {})
        merged.update(request.sync or {})
        if not config_mgr.save_user_settings({"sync": merged}):
            return convert_resp(code=500, status=500, message="Failed to save settings")
        config_mgr.load_config(config_mgr.get_config_path())

        # Providers with options.base_url_from_sync derive their base_url from
        # sync.server_url: rebuild the router so the new URL is used right
        # away. Fail-soft: never block the sync settings save.
        try:
            from opencontext.llm.provider_router import reinitialize_provider_router

            reinitialize_provider_router()
        except Exception as e:
            logger.warning(f"Failed to reinitialize provider router: {e}")

        from opencontext.screen_record.sync_outbox import get_sync_outbox

        outbox = get_sync_outbox()
        outbox.start()
        outbox.wake()
        logger.info("Sync settings updated successfully")
        return convert_resp(data={"status": outbox.status()})
    except Exception as e:
        logger.exception(f"Failed to update sync settings: {e}")
        return convert_resp(code=500, status=500, message=f"Failed to update sync settings: {str(e)}")
