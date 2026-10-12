// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Pure sync-server work-session client (P4-A). Deliberately free of any
// Electron import so the declare/end logic can be exercised from plain Node
// (see ~/.claude/work/minecontext/ verification scripts). Endpoints follow
// sync-server/README.md "sessions" section.

import type { WorkSession } from '@shared/work-session'

const REQUEST_TIMEOUT_MS = 10_000

export class WorkSessionApiError extends Error {
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'WorkSessionApiError'
    this.status = status
  }
}

function joinUrl(baseUrl: string, path: string): string {
  return baseUrl.replace(/\/+$/, '') + path
}

async function requestJson(baseUrl: string, path: string, init?: {
  method?: string
  body?: unknown
}): Promise<any> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(joinUrl(baseUrl, path), {
      method: init?.method ?? 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: init?.body === undefined ? undefined : JSON.stringify(init.body),
      signal: controller.signal
    })
    const text = await res.text()
    let parsed: any = null
    try {
      parsed = text ? JSON.parse(text) : null
    } catch {
      // non-JSON body (proxy error page etc.) -> fall through with null
    }
    if (!res.ok) {
      const detail = parsed && (parsed.detail || parsed.message)
      throw new WorkSessionApiError(
        typeof detail === 'string' ? detail : `HTTP ${res.status} on ${path}`,
        res.status
      )
    }
    return parsed
  } finally {
    clearTimeout(timer)
  }
}

/** POST /v1/sessions - declare a new work (supersedes the current active one server-side). */
export async function createWorkSession(
  baseUrl: string,
  params: { title: string; note?: string; deviceId?: string }
): Promise<WorkSession> {
  const body = await requestJson(baseUrl, '/v1/sessions', {
    method: 'POST',
    body: {
      title: params.title,
      note: params.note ?? null,
      device_id: params.deviceId ?? null
    }
  })
  return body?.session ?? null
}

/** GET /v1/sessions/active - the single active declaration (any device), null when none. */
export async function getActiveWorkSession(baseUrl: string): Promise<WorkSession | null> {
  const body = await requestJson(baseUrl, '/v1/sessions/active')
  return body?.session ?? null
}

/** POST /v1/sessions/{id}/end - manual end (confirmed). */
export async function endWorkSession(
  baseUrl: string,
  sessionId: string,
  endedAt?: string
): Promise<WorkSession> {
  return requestJson(baseUrl, `/v1/sessions/${sessionId}/end`, {
    method: 'POST',
    body: { ended_at: endedAt ?? new Date().toISOString() }
  })
}

/** POST /v1/sessions/{id}/cancel - withdraw a declaration. */
export async function cancelWorkSession(baseUrl: string, sessionId: string): Promise<WorkSession> {
  return requestJson(baseUrl, `/v1/sessions/${sessionId}/cancel`, { method: 'POST', body: {} })
}

/**
 * GET /v1/sessions?status=ended - recent unique titles, newest first.
 * Server returns ascending order; we dedupe and keep the newest `max` titles.
 */
export async function listRecentWorkTitles(
  baseUrl: string,
  opts?: { fromDays?: number; limit?: number; max?: number }
): Promise<string[]> {
  const fromDays = opts?.fromDays ?? 30
  const limit = opts?.limit ?? 200
  const max = opts?.max ?? 5
  const from = new Date(Date.now() - fromDays * 86_400_000).toISOString()
  const body = await requestJson(
    baseUrl,
    `/v1/sessions?from=${encodeURIComponent(from)}&status=ended&limit=${limit}`
  )
  const sessions: WorkSession[] = Array.isArray(body?.sessions) ? body.sessions : []
  const titles: string[] = []
  for (const s of sessions) {
    if (s.title && !titles.includes(s.title)) titles.push(s.title)
  }
  return titles.slice(-max).reverse()
}

/** PUT /v1/devices/{id} - upsert device registration (best effort). */
export async function registerDevice(
  baseUrl: string,
  deviceId: string,
  name: string,
  os: string
): Promise<void> {
  await requestJson(baseUrl, `/v1/devices/${encodeURIComponent(deviceId)}`, {
    method: 'PUT',
    body: { name, os }
  })
}
