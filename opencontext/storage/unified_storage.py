#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Unified storage system - unified management supporting multiple storage backends

SQLite document storage only: the chromadb vector backend was removed when
search moved to the sync server. Vector-related methods are gone; stale
vector_db entries in the config are skipped (fail-soft).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from opencontext.storage.base_storage import (
    IDocumentStorageBackend,
    IStorageBackend,
    StorageType,
)
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)


class StorageBackendFactory:
    """Storage backend factory class"""

    def __init__(self):
        self._backends = {
            StorageType.DOCUMENT_DB: {
                "sqlite": self._create_sqlite_backend,
            },
        }

    def create_backend(
        self, storage_type: StorageType, config: Dict[str, Any]
    ) -> Optional[IStorageBackend]:
        """Create storage backend"""
        backend_name = config.get("backend", "default")

        if storage_type not in self._backends:
            logger.error(f"Unsupported storage type: {storage_type}")
            return None

        # Get default backend
        type_backends = self._backends[storage_type]
        if backend_name == "default":
            backend_name = list(type_backends.keys())[0]

        if backend_name not in type_backends:
            logger.error(f"Unsupported {storage_type.value} backend: {backend_name}")
            return None

        try:
            backend = type_backends[backend_name](config)
            if backend.initialize(config):
                return backend
            else:
                logger.error(f"Backend {backend_name} initialization failed")
                return None
        except Exception as e:
            logger.exception(f"Creating {backend_name} backend failed: {e}")
            return None

    def _create_sqlite_backend(self, config: Dict[str, Any]):
        from opencontext.storage.backends.sqlite_backend import SQLiteBackend

        return SQLiteBackend()


class UnifiedStorage:
    """
    Unified storage system - manages the document storage backend
    """

    def __init__(self):
        self._factory = StorageBackendFactory()
        self._initialized = False
        self._document_backend: IDocumentStorageBackend = None

    def initialize(self) -> bool:
        """
        Initialize unified storage system

        Args:
            storage_configs: Storage configuration list, each configuration contains:
                - name: Backend name
                - storage_type: Storage type (document_db)
                - backend: Specific backend (sqlite)
                - config: Backend specific configuration
                - default: Whether it's the default backend for this type
                - data_types: List of supported data types
        """
        try:
            from opencontext.config.global_config import get_config

            storage_config = get_config("storage")
            backend_configs = storage_config.get("backends", [])
            if not backend_configs:
                logger.error("No storage backends configured")
                return False

            for config in backend_configs:
                try:
                    storage_type = StorageType(config["storage_type"])
                except ValueError:
                    storage_type = None

                if storage_type == StorageType.VECTOR_DB:
                    # chromadb was removed; stale config entries are skipped
                    logger.info(
                        f"Storage backend {config.get('name', 'unknown')} is a vector_db "
                        "entry; vector storage was removed, skipping"
                    )
                    continue

                backend = self._factory.create_backend(storage_type, config)
                if backend:
                    # Set dedicated backend reference
                    if storage_type == StorageType.DOCUMENT_DB and isinstance(
                        backend, IDocumentStorageBackend
                    ):
                        if self._document_backend is None or config.get("default", False):
                            self._document_backend = backend

                    logger.info(
                        f"Storage backend {config['name']} ({storage_type.value}) initialized successfully"
                    )
                else:
                    logger.error(f"Storage backend {config['name']} initialization failed")
                    return False

            if not self._document_backend:
                logger.error("No document storage backend initialized")
                return False

            self._initialized = True
            return True

        except Exception as e:
            logger.exception(f"Unified storage system initialization failed: {e}")
            return False

    def create_conversation(
        self,
        page_name: str,
        user_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new conversation record."""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            logger.error("Document database backend not initialized")
            return None

        return self._document_backend.create_conversation(
            page_name=page_name,
            user_id=user_id,
            title=title,
            metadata=metadata,
        )

    def get_conversation(self, conversation_id: int) -> Optional[Dict[str, Any]]:
        """Query single conversation details."""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            logger.error("Document database backend not initialized")
            return None

        return self._document_backend.get_conversation(conversation_id)

    def get_conversation_list(
        self,
        limit: int = 20,
        offset: int = 0,
        page_name: Optional[str] = None,
        user_id: Optional[str] = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        """List conversations with pagination/filtering."""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return {"items": [], "total": 0}

        if not self._document_backend:
            logger.error("Document database backend not initialized")
            return {"items": [], "total": 0}

        return self._document_backend.get_conversation_list(
            limit=limit,
            offset=offset,
            page_name=page_name,
            user_id=user_id,
            status=status,
        )

    def update_conversation(
        self,
        conversation_id: int,
        title: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update conversation metadata (title/status)."""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            logger.error("Document database backend not initialized")
            return None

        return self._document_backend.update_conversation(
            conversation_id=conversation_id,
            title=title,
            status=status,
        )

    def delete_conversation(self, conversation_id: int) -> Dict[str, Any]:
        """Soft delete conversation."""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return {"success": False, "id": conversation_id}

        if not self._document_backend:
            logger.error("Document database backend not initialized")
            return {"success": False, "id": conversation_id}

        return self._document_backend.delete_conversation(conversation_id)

    def insert_vaults(
        self,
        title: str,
        summary: str,
        content: str,
        document_type: str,
        tags: str = None,
        parent_id: int = None,
        is_folder: bool = False,
    ) -> int:
        """Insert report"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            return None
        return self._document_backend.insert_vaults(
            title, summary, content, document_type, tags, parent_id, is_folder
        )

    def update_vault(
        self,
        vault_id: int,
        title: str = None,
        content: str = None,
        summary: str = None,
        tags: str = None,
        is_deleted: bool = None,
    ) -> bool:
        """Update report"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return False
        if not self._document_backend:
            return False

        # Build kwargs, only include non-None values
        kwargs = {}
        if title is not None:
            kwargs["title"] = title
        if content is not None:
            kwargs["content"] = content
        if summary is not None:
            kwargs["summary"] = summary
        if tags is not None:
            kwargs["tags"] = tags
        if is_deleted is not None:
            kwargs["is_deleted"] = is_deleted

        return self._document_backend.update_vault(vault_id, **kwargs)

    def get_reports(
        self, limit: int = 100, offset: int = 0, is_deleted: bool = False
    ) -> List[Dict]:
        """Get report"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return []

        if not self._document_backend:
            return []
        return self._document_backend.get_reports(limit, offset, is_deleted)

    def get_vaults(
        self,
        limit: int = 100,
        offset: int = 0,
        is_deleted: bool = False,
        document_type: str = None,
        created_after: datetime = None,
        created_before: datetime = None,
        updated_after: datetime = None,
        updated_before: datetime = None,
    ) -> List[Dict]:
        """Get vaults list, supports more filtering conditions"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return []

        if not self._document_backend:
            return []

        return self._document_backend.get_vaults(
            limit=limit,
            offset=offset,
            is_deleted=is_deleted,
            document_type=document_type,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
        )

    def get_vault(self, vault_id: int) -> Optional[Dict]:
        """Get vaults by ID"""
        if not self._initialized:
            return None

        if not self._document_backend:
            return None
        return self._document_backend.get_vault(vault_id)

    def insert_todo(
        self,
        content: str,
        start_time: datetime = None,
        end_time: datetime = None,
        status: int = 0,
        urgency: int = 0,
        assignee: str = None,
        reason: str = None,
    ) -> int:
        """Insert todo item"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            return None
        return self._document_backend.insert_todo(
            content, start_time, end_time, status, urgency, assignee, reason
        )

    def get_todos(
        self,
        status: int = None,
        limit: int = 100,
        offset: int = 0,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> List[Dict]:
        """Get todo items"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return []

        if not self._document_backend:
            return []
        return self._document_backend.get_todos(status, limit, offset, start_time, end_time)

    def insert_activity(
        self,
        title: str,
        content: str,
        resources: str = None,
        metadata: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> int:
        """Insert activity

        Args:
            title: Activity title
            content: Activity content
            resources: Resource information (JSON string)
            metadata: Metadata information (JSON string), including categories, insights etc.
            start_time: Start time
            end_time: End time

        Returns:
            int: Activity record ID
        """
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            return None
        return self._document_backend.insert_activity(
            title, content, resources, metadata, start_time, end_time
        )

    def get_activities(
        self,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """Get activities"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return []

        if not self._document_backend:
            return []
        return self._document_backend.get_activities(start_time, end_time, limit, offset)

    def insert_screen_summary(
        self,
        title: str,
        content: str,
        app: str = None,
        window_title: str = None,
        url: str = None,
        metadata: str = None,
    ) -> int:
        """Insert a screen summary record (P1-B)"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            return None
        return self._document_backend.insert_screen_summary(
            title, content, app, window_title, url, metadata
        )

    def get_screen_summaries(
        self,
        limit: int = 100,
        offset: int = 0,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> List[Dict]:
        """Get screen summary records (P1-B)"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return []

        if not self._document_backend:
            return []
        return self._document_backend.get_screen_summaries(limit, offset, start_time, end_time)

    def get_sqlite_db_path(self) -> Optional[str]:
        """sqlite database file path (for separate connections, P1-B sync)."""
        if not self._document_backend:
            return None
        getter = getattr(self._document_backend, "get_db_path", None)
        return getter() if getter else None

    def insert_tip(self, content: str) -> int:
        """Insert tip"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return None

        if not self._document_backend:
            return None
        return self._document_backend.insert_tip(content)

    def get_tips(
        self,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """Get tips"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return []

        if not self._document_backend:
            return []
        return self._document_backend.get_tips(start_time, end_time, limit, offset)

    def update_todo_status(self, todo_id: int, status: int, end_time: datetime = None) -> bool:
        """Update todo item status"""
        if not self._initialized:
            logger.error("Unified storage system not initialized")
            return False

        if not self._document_backend:
            return False
        return self._document_backend.update_todo_status(
            todo_id=todo_id, status=status, end_time=end_time
        )

    # Monitoring data operations - delegated to document backend
    def save_monitoring_token_usage(
        self, model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int
    ) -> bool:
        """Save token usage monitoring data"""
        return self._document_backend.save_monitoring_token_usage(
            model, prompt_tokens, completion_tokens, total_tokens
        )

    def save_monitoring_stage_timing(
        self,
        stage_name: str,
        duration_ms: int,
        status: str = "success",
        metadata: Optional[str] = None,
    ) -> bool:
        """Save stage timing monitoring data"""
        return self._document_backend.save_monitoring_stage_timing(
            stage_name, duration_ms, status, metadata
        )

    def save_monitoring_data_stats(
        self,
        data_type: str,
        count: int = 1,
        context_type: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> bool:
        """Save data statistics monitoring data"""
        return self._document_backend.save_monitoring_data_stats(
            data_type, count, context_type, metadata
        )

    def query_monitoring_token_usage(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Query token usage monitoring data"""
        return self._document_backend.query_monitoring_token_usage(hours)

    def query_monitoring_stage_timing(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Query stage timing monitoring data"""
        return self._document_backend.query_monitoring_stage_timing(hours)

    def query_monitoring_data_stats(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Query data statistics monitoring data"""
        return self._document_backend.query_monitoring_data_stats(hours)

    def query_monitoring_data_stats_by_range(
        self, start_time: Any, end_time: Any
    ) -> List[Dict[str, Any]]:
        """Query data statistics monitoring data by custom time range"""
        return self._document_backend.query_monitoring_data_stats_by_range(start_time, end_time)

    def query_monitoring_data_stats_trend(
        self, hours: int = 24, interval_hours: int = 1
    ) -> List[Dict[str, Any]]:
        """Query data statistics trend with time grouping"""
        return self._document_backend.query_monitoring_data_stats_trend(hours, interval_hours)

    def cleanup_old_monitoring_data(self, days: int = 7) -> bool:
        """Clean up monitoring data older than specified days"""
        return self._document_backend.cleanup_old_monitoring_data(days)

    # Message management operations - delegated to document backend
    def create_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        is_complete: bool = True,
        token_count: int = 0,
        parent_message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Create a new message, returns message ID"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return None
        result = self._document_backend.create_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            is_complete=is_complete,
            token_count=token_count,
            parent_message_id=parent_message_id,
            metadata=metadata,
        )
        # create_message returns a dict, extract the ID
        return result.get("id") if result else None

    def create_streaming_message(
        self,
        conversation_id: int,
        role: str,
        parent_message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Create a streaming message (initial content is empty), returns message ID"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return None
        result = self._document_backend.create_streaming_message(
            conversation_id=conversation_id,
            role=role,
            parent_message_id=parent_message_id,
            metadata=metadata,
        )
        # create_streaming_message returns a dict, extract the ID
        return result.get("id") if result else None

    def update_message(
        self,
        message_id: int,
        new_content: str,
        is_complete: Optional[bool] = None,
        token_count: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update message content"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return None
        return self._document_backend.update_message(
            message_id=message_id,
            new_content=new_content,
            is_complete=is_complete,
            token_count=token_count,
        )

    def append_message_content(
        self, message_id: int, content_chunk: str, token_count: int = 0
    ) -> bool:
        """Append content to a streaming message"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return False
        return self._document_backend.append_message_content(
            message_id=message_id, content_chunk=content_chunk, token_count=token_count
        )

    def update_message_metadata(
        self, message_id: int, metadata: Dict[str, Any]
    ) -> bool:
        """Update message metadata"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return False
        return self._document_backend.update_message_metadata(
            message_id=message_id, metadata=metadata
        )

    def mark_message_finished(
        self, message_id: int, status: str = "completed", error_message: Optional[str] = None
    ) -> bool:
        """Mark a message as finished (completed, failed, or cancelled)"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return False
        return self._document_backend.mark_message_finished(
            message_id=message_id, status=status, error_message=error_message
        )

    def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get a single message"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return None
        return self._document_backend.get_message(message_id)

    def get_conversation_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
        """Get all messages for a conversation"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return []
        return self._document_backend.get_conversation_messages(conversation_id)

    def delete_message(self, message_id: int) -> bool:
        """Delete a message"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return False
        return self._document_backend.delete_message(message_id)

    def interrupt_message(self, message_id: int) -> bool:
        """Interrupt message generation (mark as cancelled)"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return False
        return self._document_backend.interrupt_message(message_id)

    # Message Thinking operations - delegated to document backend
    def add_message_thinking(
        self,
        message_id: int,
        content: str,
        stage: Optional[str] = None,
        progress: float = 0.0,
        sequence: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Add a thinking record to a message"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return None
        return self._document_backend.add_message_thinking(
            message_id=message_id,
            content=content,
            stage=stage,
            progress=progress,
            sequence=sequence,
            metadata=metadata,
        )

    def get_message_thinking(self, message_id: int) -> List[Dict[str, Any]]:
        """Get all thinking records for a message"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return []
        return self._document_backend.get_message_thinking(message_id)

    def clear_message_thinking(self, message_id: int) -> bool:
        """Clear all thinking records for a message"""
        if not self._initialized or not self._document_backend:
            logger.error("Storage not initialized")
            return False
        return self._document_backend.clear_message_thinking(message_id)
