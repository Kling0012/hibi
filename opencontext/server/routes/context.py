# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Context management routes
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from opencontext.models.enums import ContentFormat, ContextSource
from opencontext.server.middleware.auth import auth_dependency
from opencontext.server.opencontext import OpenContext
from opencontext.server.utils import convert_resp, get_context_lab
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["context"])


class ContextIn(BaseModel):
    source: ContextSource
    content_format: ContentFormat
    data: Any
    metadata: Optional[dict] = {}


class UpdateContextIn(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None


class QueryIn(BaseModel):
    query: str


class ConsumeIn(BaseModel):
    query: str
    context_ids: List[str]


class VectorSearchRequest(BaseModel):
    query: str
    top_k: int = 10
    context_types: Optional[List[str]] = None
    filters: Optional[Dict[str, Any]] = None


@router.get("/api/context_types")
async def get_context_types(
    opencontext: OpenContext = Depends(get_context_lab), _auth: str = auth_dependency
):
    """Get all available context types."""
    try:
        context_types = opencontext.get_context_types()
        return context_types
    except Exception as e:
        logger.exception(f"Error getting context types: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get context types: {str(e)}")


@router.post("/api/vector_search")
async def vector_search(
    request: VectorSearchRequest,
    opencontext: OpenContext = Depends(get_context_lab),
    _auth: str = auth_dependency,
):
    """Directly search vector database without using LLM."""
    try:
        results = opencontext.search(
            query=request.query,
            top_k=request.top_k,
            context_types=request.context_types,
            filters=request.filters,
        )

        return convert_resp(
            data={
                "results": results,
                "total": len(results),
                "query": request.query,
                "top_k": request.top_k,
                "context_types": request.context_types,
                "filters": request.filters,
            }
        )

    except Exception as e:
        logger.exception(f"Error in vector search: {e}")
        return convert_resp(code=500, status=500, message=f"Vector search failed: {str(e)}")
