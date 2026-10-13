# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Screenshot management routes

Deprecated: the VLM screenshot analysis pipeline (screenshot_processor)
was removed; screen recording now runs through live_monitor via
/api/live/frame. These endpoints stay only so old clients get an explicit
answer instead of a 404, and do not process anything.
"""

from typing import List

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from opencontext.server.middleware.auth import auth_dependency
from opencontext.server.opencontext import OpenContext
from opencontext.server.utils import get_context_lab

router = APIRouter(tags=["screenshots"])

_DEPRECATED_RESPONSE = {"accepted": False, "reason": "deprecated"}


class AddScreenshotRequest(BaseModel):
    path: str
    window: str
    create_time: str
    source: str = "unknown"


class AddScreenshotsRequest(BaseModel):
    screenshots: List[AddScreenshotRequest]


@router.post("/api/add_screenshot", response_class=JSONResponse)
async def add_screenshot(
    request: AddScreenshotRequest,
    opencontext: OpenContext = Depends(get_context_lab),
    _auth: str = auth_dependency,
):
    return JSONResponse(_DEPRECATED_RESPONSE)


@router.post("/api/add_screenshots", response_class=JSONResponse)
async def add_screenshots(
    request: AddScreenshotsRequest,
    opencontext: OpenContext = Depends(get_context_lab),
    _auth: str = auth_dependency,
):
    return JSONResponse(_DEPRECATED_RESPONSE)
