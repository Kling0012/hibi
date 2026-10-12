#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Global embedding client singleton wrapper.
Embeddings are served by the ProviderRouter's embedding route. When the route
is not configured (empty by default), embedding-dependent features raise a
clear error per request while the rest of the application keeps working
(fail-soft).
"""

import threading
from typing import Dict, List, Optional

from opencontext.llm.provider_router import (
    ProviderRouterError,
    get_provider_router,
    reinitialize_provider_router,
)
from opencontext.models.context import Vectorize
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)


class GlobalEmbeddingClient:
    """
    Global embedding client (singleton pattern)
    """

    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        """Ensure singleton pattern"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize global embedding client"""
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._auto_initialized = False
                    GlobalEmbeddingClient._initialized = True

    @classmethod
    def get_instance(cls) -> "GlobalEmbeddingClient":
        """
        Get global embedding client instance
        """
        instance = cls()
        if not instance._auto_initialized:
            instance._auto_initialize()
        return instance

    def _auto_initialize(self):
        """Auto-initialize (state lives in the ProviderRouter)"""
        if self._auto_initialized:
            return
        self._auto_initialized = True
        router = get_provider_router()
        if router.has_route("embedding"):
            logger.info("GlobalEmbeddingClient initialized (via ProviderRouter)")
        else:
            logger.warning(
                "GlobalEmbeddingClient: embedding route not configured; "
                "embedding-dependent features are disabled"
            )

    def is_initialized(self) -> bool:
        return get_provider_router().has_route("embedding")

    def reinitialize(self, new_config: Optional[Dict] = None):
        """
        Reinitialize embedding client (thread-safe)
        """
        try:
            reinitialize_provider_router()
            logger.info("Embedding client reinitialization completed")
            return True
        except Exception as e:
            logger.error(f"Failed to reinitialize embedding client: {e}")
            return False

    def do_embedding(self, text: str, **kwargs) -> List[float]:
        """
        Get text embeddings
        """
        router = get_provider_router()
        if not router.has_route("embedding"):
            raise ProviderRouterError(
                "embedding route not configured; embedding-dependent features are disabled"
            )
        return router.embed_sync(text, **kwargs)

    def do_vectorize(self, vectorize: Vectorize, **kwargs):
        """
        Vectorize a Vectorize object
        """
        if vectorize.vector:
            return
        vectorize.vector = self.do_embedding(vectorize.get_vectorize_content(), **kwargs)
        return

    async def do_vectorize_async(self, vectorize: Vectorize, **kwargs):
        """
        Vectorize a Vectorize object asynchronously
        """
        if vectorize.vector:
            return
        router = get_provider_router()
        if not router.has_route("embedding"):
            raise ProviderRouterError(
                "embedding route not configured; embedding-dependent features are disabled"
            )
        vectorize.vector = await router.embed(
            vectorize.get_vectorize_content(), **kwargs
        )
        return


def is_initialized() -> bool:
    return GlobalEmbeddingClient.get_instance().is_initialized()


def do_embedding(text: str, **kwargs) -> List[float]:
    return GlobalEmbeddingClient.get_instance().do_embedding(text, **kwargs)


def do_vectorize(vectorize_obj: Vectorize, **kwargs):
    return GlobalEmbeddingClient.get_instance().do_vectorize(vectorize_obj, **kwargs)

async def do_vectorize_async(vectorize_obj: Vectorize, **kwargs):
    return await GlobalEmbeddingClient.get_instance().do_vectorize_async(vectorize_obj, **kwargs)
