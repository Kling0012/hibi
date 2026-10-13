#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Vaults document management API routes
Focuses on document CRUD operations, AI chat functionality handled by advanced_chat
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from opencontext.models.enums import VaultType
from opencontext.server.middleware.auth import auth_dependency
from opencontext.storage.global_storage import get_storage
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["vaults"])


# API model definitions
class VaultDocument(BaseModel):
    """Vault document model"""

    id: Optional[int] = None
    title: str
    content: str
    summary: Optional[str] = None
    tags: Optional[str] = None
    document_type: str = VaultType.NOTE.value


@router.get("/api/vaults/list")
async def get_documents_list(
    limit: int = Query(default=50, description="Return limit"),
    offset: int = Query(default=0, description="Offset"),
    _auth: str = auth_dependency,
):
    """
    Get document list
    """
    try:
        storage = get_storage()
        documents = storage.get_vaults(limit=limit, offset=offset, is_deleted=False)

        # Format return data
        result = []
        for doc in documents:
            result.append(
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "summary": (
                        doc["summary"][:100] + "..."
                        if doc["summary"] and len(doc["summary"]) > 100
                        else doc["summary"]
                    ),
                    "created_at": doc["created_at"],
                    "updated_at": doc["updated_at"],
                    "document_type": doc["document_type"],
                    "content_length": len(doc["content"]) if doc["content"] else 0,
                }
            )

        return JSONResponse({"success": True, "data": result, "total": len(result)})

    except Exception as e:
        logger.exception(f"Failed to get document list: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/api/vaults/create")
async def create_document(
    document: VaultDocument, background_tasks: BackgroundTasks, _auth: str = auth_dependency
):
    """
    Create new document
    """
    try:
        logger.info(f"Creating document with data: {document}")
        storage = get_storage()

        # Create new document - use insert_vaults method
        doc_id = storage.insert_vaults(
            title=document.title,
            summary=document.summary,
            content=document.content,  # insert_vaults will automatically handle None
            document_type=document.document_type,
            tags=document.tags,
        )

        return JSONResponse(
            {
                "success": True,
                "message": "Document created successfully",
                "doc_id": doc_id,
                "table_name": "vaults",
            }
        )

    except Exception as e:
        logger.exception(f"Failed to create document: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/api/vaults/{document_id}")
async def get_document(document_id: int, _auth: str = auth_dependency):
    """
    Get document details
    """
    try:
        storage = get_storage()
        # Get all documents to find the document with specified ID
        documents = storage.get_vaults(limit=100, offset=0, is_deleted=False)

        # Find the document with specified ID
        document = None
        for doc in documents:
            if doc["id"] == document_id:
                document = doc
                break

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        return JSONResponse(
            {
                "success": True,
                "data": {
                    "id": document["id"],
                    "title": document["title"],
                    "content": document["content"],
                    "summary": document["summary"],
                    "tags": document["tags"],
                    "created_at": document["created_at"],
                    "updated_at": document["updated_at"],
                    "document_type": document["document_type"],
                },
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get document details: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/api/vaults/{document_id}")
async def save_document(
    document_id: int,
    document: VaultDocument,
    background_tasks: BackgroundTasks,
    _auth: str = auth_dependency,
):
    """
    Save document
    """
    try:
        storage = get_storage()

        # Update existing document
        success = storage.update_vault(
            vault_id=document_id,
            title=document.title,
            content=document.content,
            summary=document.summary,
            tags=document.tags,
        )

        if success:
            return JSONResponse(
                {
                    "success": True,
                    "message": "Document saved successfully",
                    "doc_id": document_id,
                    "table_name": "vaults",
                }
            )
        else:
            raise HTTPException(status_code=404, detail="Document not found or update failed")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to save document: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.delete("/api/vaults/{document_id}")
async def delete_document(
    document_id: int, background_tasks: BackgroundTasks, _auth: str = auth_dependency
):
    """
    Delete document (soft delete)
    """
    try:
        storage = get_storage()

        # Soft delete document
        success = storage.update_vault(vault_id=document_id, is_deleted=True)

        if success:
            return JSONResponse(
                {
                    "success": True,
                    "message": "Document deleted successfully",
                }
            )
        else:
            raise HTTPException(status_code=404, detail="Document not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to delete document: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/api/vaults/{document_id}/context")
async def get_document_context_status(document_id: int, _auth: str = auth_dependency):
    """
    Get document context processing status

    The document context pipeline was removed with the VLM processors; vault
    notes are no longer chunked, so this always reports no context.
    """
    return JSONResponse(
        {
            "success": True,
            "document_id": document_id,
            "has_context": False,
            "total_chunks": 0,
        }
    )
