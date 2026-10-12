// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Work session declaration (P4-A): shared types between main / preload /
// renderer declare window and the sync-server client.

/** One work session row as returned by the sync-server /v1/sessions API. */
export interface WorkSession {
  id: string
  device_id: string | null
  title: string
  note: string | null
  started_at: string
  ended_at: string | null
  status: string
  end_reason: string | null
  duration_minutes: number
}

/** Currently declared work (null while nothing is active). */
export interface ActiveWork {
  id: string
  title: string
  startedAt: string
}

/** Snapshot of the work-session feature, pushed to tray / declare window. */
export interface WorkSessionState {
  /** local backend answered /api/sync_settings */
  ready: boolean
  /** sync.enabled from backend settings */
  enabled: boolean
  /** last sync-server round-trip succeeded */
  connected: boolean
  serverUrl: string
  deviceId: string
  active: ActiveWork | null
  /** recent titles (server list merged with local history, newest first) */
  recentTitles: string[]
  /** non-empty when the feature is degraded (fail-soft, shown in UI) */
  lastError: string
}

export interface WorkSessionResult {
  ok: boolean
  session?: WorkSession | null
  error?: string
}
