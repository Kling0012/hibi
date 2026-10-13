// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Work session declaration service.
//
// Reads the sync settings (server_url / device_id / device_name) from the
// local backend (/api/sync_settings), then keeps the single global "active"
// declaration in sync with the sync-server by polling GET /v1/sessions/active
// every 30s. Everything here is fail-soft: any failure only degrades this
// feature (the home page panel shows the error) and never affects the rest.

import os from 'node:os'
import { getLogger } from '@shared/logger/main'
import { getBackendPort } from '@main/backend'
import { tMain } from '@main/i18n'
import type { ActiveWork, WorkSession, WorkSessionResult, WorkSessionState } from '@shared/work-session'
import { localStoreService } from './LocalStoreService'
import {
  createWorkSession,
  endWorkSession,
  getActiveWorkSession,
  listRecentWorkTitles,
  registerDevice
} from './workSessionApi'

const logger = getLogger('WorkSessionService')

const POLL_INTERVAL_MS = 30_000
const FAST_RETRY_MS = 5_000
const RECENT_TITLES_KEY = 'workSession.recentTitles'
const RECENT_TITLES_LOCAL_MAX = 10

type Listener = (state: WorkSessionState) => void

interface SyncSettings {
  enabled: boolean
  serverUrl: string
  deviceId: string
}

class WorkSessionService {
  private state: WorkSessionState = {
    ready: false,
    enabled: false,
    connected: false,
    serverUrl: '',
    deviceId: '',
    active: null,
    recentTitles: [],
    lastError: ''
  }
  private listeners = new Set<Listener>()
  private timer: NodeJS.Timeout | null = null
  private refreshing = false
  private stopped = false

  getState(): WorkSessionState {
    return { ...this.state, recentTitles: [...this.state.recentTitles] }
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private notify(): void {
    const snapshot = this.getState()
    for (const listener of this.listeners) {
      try {
        listener(snapshot)
      } catch (error) {
        logger.error('work session listener failed:', error)
      }
    }
  }

  start(): void {
    if (this.timer) {
      return
    }
    this.stopped = false
    this.scheduleRefresh(0)
    logger.info('WorkSessionService started')
  }

  stop(): void {
    this.stopped = true
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    logger.info('WorkSessionService stopped')
  }

  private scheduleRefresh(delayMs: number): void {
    if (this.stopped) {
      return
    }
    if (this.timer) {
      clearTimeout(this.timer)
    }
    this.timer = setTimeout(() => {
      void this.refresh()
    }, delayMs)
  }

  /**
   * One poll cycle: local sync settings -> sync-server active + recent titles.
   * Never throws; failures keep the previous state and retry faster.
   */
  async refresh(): Promise<void> {
    if (this.refreshing) {
      return
    }
    this.refreshing = true
    try {
      const settings = await this.loadSyncSettings()
      if (!settings) {
        // Local backend not up yet (or settings unreachable): retry sooner,
        // keep whatever we knew about the active session.
        this.state.ready = false
        this.state.lastError = tMain('ws.errBackend')
        this.notify()
        this.scheduleRefresh(FAST_RETRY_MS)
        return
      }

      if (!settings.enabled || !settings.serverUrl) {
        this.state.ready = true
        this.state.enabled = false
        this.state.connected = false
        this.state.serverUrl = settings.serverUrl
        this.state.deviceId = settings.deviceId
        this.state.active = null
        this.state.recentTitles = this.readLocalTitles()
        this.state.lastError = ''
        this.notify()
        this.scheduleRefresh(POLL_INTERVAL_MS)
        return
      }

      const serverUrl = settings.serverUrl
      const [activeResult, titlesResult] = await Promise.allSettled([
        getActiveWorkSession(serverUrl),
        listRecentWorkTitles(serverUrl)
      ])

      if (activeResult.status === 'fulfilled') {
        this.state.ready = true
        this.state.enabled = true
        this.state.connected = true
        this.state.serverUrl = serverUrl
        this.state.deviceId = settings.deviceId
        this.state.active = this.toActiveWork(activeResult.value)
        this.state.lastError = ''
        this.state.recentTitles =
          titlesResult.status === 'fulfilled'
            ? this.mergeTitles(titlesResult.value, this.readLocalTitles())
            : this.readLocalTitles()
      } else {
        const message = activeResult.reason instanceof Error ? activeResult.reason.message : String(activeResult.reason)
        this.state.ready = true
        this.state.enabled = true
        this.state.connected = false
        this.state.serverUrl = serverUrl
        this.state.deviceId = settings.deviceId
        this.state.lastError = message
        logger.warn(`sync-server unreachable: ${message}`)
      }
      this.notify()
      this.scheduleRefresh(POLL_INTERVAL_MS)
    } catch (error) {
      // refresh() itself should never throw, but stay fail-soft if it does
      logger.error('refresh failed:', error)
      this.scheduleRefresh(FAST_RETRY_MS)
    } finally {
      this.refreshing = false
    }
  }

  /** Declare a new work session ("これから○○をする"). */
  async declare(title: string): Promise<WorkSessionResult> {
    const trimmed = (title ?? '').trim()
    if (!trimmed) {
      return { ok: false, error: tMain('ws.errNoTitle') }
    }
    const { ready, enabled, connected, serverUrl, deviceId } = this.state
    if (!ready) {
      return { ok: false, error: tMain('ws.errNotReady') }
    }
    if (!enabled || !serverUrl) {
      return { ok: false, error: tMain('ws.errDisabled') }
    }
    if (!connected) {
      return { ok: false, error: tMain('ws.errUnreachable') }
    }
    try {
      const session = await createWorkSession(serverUrl, { title: trimmed, deviceId })
      this.state.active = this.toActiveWork(session)
      this.state.connected = true
      this.state.lastError = ''
      this.writeLocalTitle(trimmed)
      // newest declared title goes first
      this.state.recentTitles = this.mergeTitles([trimmed], this.state.recentTitles)
      this.notify()
      logger.info(`declared work session: ${trimmed} (${session?.id ?? 'unknown id'})`)
      // refresh recent titles / confirm state right away
      void this.refresh()
      return { ok: true, session }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.state.connected = false
      this.state.lastError = message
      this.notify()
      logger.warn(`declare failed: ${message}`)
      return { ok: false, error: message }
    }
  }

  /** Manually end the current active declaration. */
  async endActive(): Promise<WorkSessionResult> {
    const { active, connected, serverUrl } = this.state
    if (!active) {
      return { ok: false, error: tMain('ws.errNoActive') }
    }
    if (!connected) {
      return { ok: false, error: tMain('ws.errUnreachable') }
    }
    try {
      const session: WorkSession | null = await endWorkSession(serverUrl, active.id)
      this.state.active = null
      this.state.lastError = ''
      this.notify()
      logger.info(`ended work session: ${active.title} (${active.id})`)
      void this.refresh()
      return { ok: true, session }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.state.connected = false
      this.state.lastError = message
      this.notify()
      logger.warn(`end failed: ${message}`)
      return { ok: false, error: message }
    }
  }

  private toActiveWork(session: WorkSession | null): ActiveWork | null {
    if (!session || session.status !== 'active') {
      return null
    }
    return { id: session.id, title: session.title, startedAt: session.started_at }
  }

  // ---- local sync settings (served by the packaged python backend) ----

  private async loadSyncSettings(): Promise<SyncSettings | null> {
    const port = getBackendPort()
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/sync_settings`, {
        headers: { 'X-Auth-Token': 'minecontext_frontend_token' },
        signal: AbortSignal.timeout(5_000)
      })
      if (!res.ok) {
        logger.warn(`/api/sync_settings HTTP ${res.status}`)
        return null
      }
      const body = await res.json()
      const sync = body?.data?.sync ?? {}
      const enabled = Boolean(sync.enabled)
      const serverUrl = typeof sync.server_url === 'string' ? sync.server_url.trim().replace(/\/+$/, '') : ''
      let deviceId = typeof sync.device_id === 'string' ? sync.device_id.trim() : ''
      if (enabled && serverUrl && !deviceId) {
        deviceId = await this.ensureDeviceId(deviceId, serverUrl, sync)
      }
      return { enabled, serverUrl, deviceId }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      logger.info(`sync settings unavailable (backend starting?): ${message}`)
      return null
    }
  }

  /**
   * The backend normally generates sync.device_id on first outbox start; if it
   * is still empty we generate one, persist it back through /api/sync_settings
   * and register the device on the sync-server. All best effort.
   */
  private async ensureDeviceId(
    _current: string,
    serverUrl: string,
    sync: Record<string, unknown>
  ): Promise<string> {
    const deviceId = crypto.randomUUID()
    const deviceName =
      (typeof sync.device_name === 'string' && sync.device_name.trim()) || os.hostname()
    try {
      const port = getBackendPort()
      const res = await fetch(`http://127.0.0.1:${port}/api/sync_settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Auth-Token': 'minecontext_frontend_token' },
        body: JSON.stringify({ sync: { device_id: deviceId } }),
        signal: AbortSignal.timeout(5_000)
      })
      if (!res.ok) {
        logger.warn(`persisting generated device_id failed: HTTP ${res.status}`)
      }
    } catch (error) {
      logger.warn('persisting generated device_id failed:', error)
    }
    try {
      await registerDevice(serverUrl, deviceId, deviceName, process.platform)
    } catch (error) {
      // registration is cosmetic; the outbox on the backend also does it
      logger.info(`device registration deferred: ${error instanceof Error ? error.message : error}`)
    }
    return deviceId
  }

  // ---- local recent-title history (fallback when server list is empty) ----

  private readLocalTitles(): string[] {
    const value = localStoreService.getSetting(RECENT_TITLES_KEY)
    return Array.isArray(value) ? value.filter((t): t is string => typeof t === 'string' && t.length > 0) : []
  }

  private writeLocalTitle(title: string): void {
    const titles = [title, ...this.readLocalTitles().filter((t) => t !== title)].slice(
      0,
      RECENT_TITLES_LOCAL_MAX
    )
    try {
      localStoreService.setSetting(RECENT_TITLES_KEY, titles)
    } catch (error) {
      logger.warn('saving recent titles failed:', error)
    }
  }

  /** server titles first, then unseen local ones, capped at 5, newest first. */
  private mergeTitles(serverTitles: string[], localTitles: string[]): string[] {
    const merged: string[] = []
    for (const title of [...serverTitles, ...localTitles]) {
      if (title && !merged.includes(title)) {
        merged.push(title)
      }
    }
    return merged.slice(0, 5)
  }
}

export const workSessionService = new WorkSessionService()
export type { WorkSessionState }
