# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Context processing module.

The VLM screenshot/document processors, entity processor and context merger
were removed: screen analysis now happens in live_monitor (SQLite + sync
server), so this package no longer contains any processors. It is kept as an
empty placeholder so old imports fail loudly instead of taking the whole
package tree down.
"""
