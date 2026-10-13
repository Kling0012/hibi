#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Storage backend package initialization file

SQLite only: the chromadb vector backend was removed when search moved to
the sync server.
"""

from .sqlite_backend import SQLiteBackend

__all__ = ["SQLiteBackend"]
