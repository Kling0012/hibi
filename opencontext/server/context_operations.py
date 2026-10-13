#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Context search operations for OpenContext.
Separated from main OpenContext class for better maintainability.

Local chromadb search was removed: search now relays to the sync server's
/v1/search endpoint. When the sync server is unset or unreachable the
result is simply empty (fail-soft).
"""

from typing import Any, Dict, List, Optional

from opencontext.models.enums import get_context_type_options
from opencontext.storage.global_storage import get_storage
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Upper bound mirrors the sync server's k limit (1-200).
_MAX_SEARCH_K = 200


class ContextOperations:
    """Handles context search operations (relayed to the sync server)."""

    def __init__(self):
        self.storage = get_storage()

    def search(
        self,
        query: str,
        top_k: int = 10,
        context_types: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Relay a search to the sync server without LLM processing.

        Args:
            query: Search query text
            top_k: Number of results to return
            context_types: Ignored (kept for API compatibility)
            filters: Optional {'from': epoch, 'to': epoch} time range

        Returns:
            List of search results with context and scores (empty on failure)
        """
        from opencontext.tools.retrieval_tools.sync_tools import _get_server_url

        import httpx

        server_url = _get_server_url()
        if not server_url:
            return []

        body: Dict[str, Any] = {"query": query, "k": min(int(top_k or 10), _MAX_SEARCH_K)}
        if filters:
            # Reuse the sync server's time-range parameters when provided
            if filters.get("from") is not None:
                body["from"] = filters["from"]
            if filters.get("to") is not None:
                body["to"] = filters["to"]

        try:
            response = httpx.post(
                f"{server_url}/v1/search", json=body, timeout=15.0
            )
            response.raise_for_status()
            hits = response.json().get("hits", [])

            results = []
            for hit in hits:
                event = hit.get("event", {})
                results.append(
                    {
                        "context": {
                            "id": event.get("id"),
                            "extracted_data": {
                                "title": event.get("title"),
                                "summary": event.get("text"),
                                "context_type": event.get("kind"),
                                "keywords": [],
                            },
                            "properties": {
                                "create_time": event.get("started_at"),
                                "app": event.get("app"),
                            },
                        },
                        "score": hit.get("score"),
                    }
                )
            return results

        except Exception as e:
            logger.warning(f"Sync search relay failed: {e}")
            return []

    def get_context_types(self) -> List[str]:
        """
        Get all available context types.

        Returns:
            List of context types
        """
        return get_context_type_options()
