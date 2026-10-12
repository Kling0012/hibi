# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Screen recording package (P1-B).

- live_monitor: continuous screen stream into a Gemini Live session with
  periodic activity summaries, plus event-driven verbatim screen-text
  captures (screen_summary) through the 'exact' provider route.
- sync_outbox: local outbox that uploads activity / screen_summary / todo /
  report events to the central sync server.
"""
