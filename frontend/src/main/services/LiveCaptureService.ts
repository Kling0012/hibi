// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Live screen capture (P1-B). Captures selected sources as JPEG at
// capture.live_fps per selected source and posts each frame to /api/live/frame.
// App identity is retained for exclusion checks; titles/URLs are included only
// when the frontmost window belongs entirely to the captured source.
// Everything is fail-soft: a failed capture or upload is
// logged and skipped; the loop keeps running.

import type { CaptureSource } from '@interface/common/source'
import { getBackendPort } from '@main/backend'
import { getLogger } from '@shared/logger/main'
import axios from 'axios'
import type { DesktopCapturerSource } from 'electron'
import { desktopCapturer, screen, systemPreferences } from 'electron'

const logger = getLogger('LiveCaptureService')

interface WindowMeta {
  app: string
  title: string
  url: string
}

interface CapturePolicy {
  paused: boolean
  fps: number
  excludeApps: string[]
  generation?: string
}

interface WindowInfo {
  id?: number
  bounds?: { x: number; y: number; width: number; height: number }
  title?: string
  url?: string
  owner?: { name?: string }
}

type WindowOptions = { accessibilityPermission?: boolean; screenRecordingPermission?: boolean }
// The package is ESM-only; loaded dynamically at runtime.
type ActiveWindowFn = (options?: WindowOptions) => Promise<WindowInfo | undefined>
type OpenWindowsFn = (options?: WindowOptions) => Promise<WindowInfo[]>

class LiveCaptureService {
  private timer: NodeJS.Timeout | null = null
  private running = false
  private generation = 0
  private busy = false
  private awayPaused = false
  private fps = 1.0
  private activeWindow: ActiveWindowFn | null = null
  private openWindows: OpenWindowsFn | null = null
  private activeWindowLoadFailed = false
  private consecutiveErrors = 0
  private sources: Pick<CaptureSource, 'id' | 'type' | 'windowId'>[] = []
  private canRecord: () => boolean = () => false
  private settingsRevision = 0

  public setSources(sources: CaptureSource[]): void {
    const selection = sources.map(({ id, type, windowId }) => ({ id, type, windowId }))
    if (JSON.stringify(selection) === JSON.stringify(this.sources)) return
    this.sources = selection
    this.settingsRevision++
  }

  public setRecordingPolicy(canRecord: () => boolean): void {
    this.canRecord = canRecord
    this.settingsRevision++
  }

  public async start(): Promise<void> {
    if (this.running) {
      return
    }
    this.running = true
    const generation = ++this.generation
    const policy = await this.fetchCapturePolicy()
    if (!this.running || generation !== this.generation) return
    this.fps = policy?.fps ?? 1.0
    const intervalMs = Math.max(200, Math.round(1000 / this.fps))
    logger.info(`LiveCaptureService started (fps=${this.fps}, interval=${intervalMs}ms)`)
    this.timer = setInterval(() => {
      this.tick().catch((e) => logger.error('live capture tick failed:', e))
    }, intervalMs)
  }

  public stop(): void {
    if (!this.running) {
      return
    }
    this.running = false
    this.generation++
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
    logger.info('LiveCaptureService stopped')
  }

  public isRunning(): boolean {
    return this.running
  }

  public setAwayPaused(paused: boolean): void {
    this.awayPaused = paused
  }

  private async fetchCapturePolicy(): Promise<CapturePolicy | null> {
    try {
      const res = await axios.get(`http://127.0.0.1:${getBackendPort()}/api/settings/general`, {
        timeout: 5000
      })
      const data = res.data?.data
      if (!data?.capture || typeof data.capture !== 'object') return null
      const fps = Number(data.capture.live_fps)
      return {
        paused: !!data.capture.paused,
        fps: Number.isFinite(fps) && fps > 0 ? Math.min(1, Math.max(0.2, fps)) : 1.0,
        excludeApps: Array.isArray(data.capture.exclude_apps)
          ? data.capture.exclude_apps.filter(
              (entry: unknown): entry is string => typeof entry === 'string' && !!entry.trim()
            )
          : [],
        generation: data.capture_generation
      }
    } catch (e) {
      logger.warn('failed to fetch capture settings; skipping capture', e)
    }
    return null
  }

  private async tick(): Promise<void> {
    if (this.busy) return
    const generation = this.generation
    const revision = this.settingsRevision
    const canCapture = () =>
      this.running &&
      !this.awayPaused &&
      generation === this.generation &&
      revision === this.settingsRevision &&
      this.canRecord()
    if (!canCapture() || this.sources.length === 0) return
    const selection = this.sources
    this.busy = true
    try {
      const policy = await this.fetchCapturePolicy()
      if (!canCapture() || !policy || policy.paused) return
      const sources = await desktopCapturer.getSources({
        types: [...new Set(selection.map((source) => source.type))],
        thumbnailSize: { width: 1280, height: 1280 }
      })
      if (!canCapture()) return
      for (const selected of selection) {
        const source = sources.find(
          (candidate) =>
            candidate.id === selected.id ||
            (selected.type === 'window' &&
              selected.id.startsWith('virtual-window:') &&
              selected.windowId !== undefined &&
              candidate.id.startsWith(`window:${selected.windowId}:`))
        )
        if (!source) continue
        if (!canCapture()) return
        const jpeg = source.thumbnail.toJPEG(70)
        if (!jpeg || jpeg.length === 0) continue
        const meta = await this.getWindowMeta(source, selected.type)
        const current = await this.fetchCapturePolicy()
        if (!canCapture() || !current || current.paused || current.generation !== policy.generation) return
        const app = meta.app.trim().toLowerCase()
        // Unknown identity cannot establish safety when exclusions are configured.
        if (
          current.excludeApps.length &&
          (!app || current.excludeApps.some((entry) => app.includes(entry.trim().toLowerCase())))
        ) {
          continue
        }
        const params = new URLSearchParams({
          app: meta.app,
          title: meta.title,
          url: meta.url
        })
        if (policy.generation) params.set('capture_generation', policy.generation)
        // A POST already started cannot be retracted. The backend checks this
        // token too, so pause/resume during transport cannot revive an old frame.
        if (!canCapture()) return
        await axios.post(`http://127.0.0.1:${getBackendPort()}/api/live/frame?${params.toString()}`, jpeg, {
          headers: { 'Content-Type': 'image/jpeg' },
          timeout: 5000
        })
        this.consecutiveErrors = 0
      }
    } catch (e) {
      this.consecutiveErrors++
      if (this.consecutiveErrors % 30 === 1) {
        logger.warn(`live frame upload failed (${this.consecutiveErrors} consecutive):`, e)
      }
    } finally {
      this.busy = false
    }
  }

  private async getWindowMeta(source: DesktopCapturerSource, type: CaptureSource['type']): Promise<WindowMeta> {
    const fallback = { app: '', title: '', url: '' }
    let identified = fallback
    if (!this.activeWindow && !this.activeWindowLoadFailed) {
      try {
        // ESM-only package: dynamic import from the CJS bundle
        const mod = await import('get-windows')
        this.activeWindow = mod.activeWindow as ActiveWindowFn
        this.openWindows = mod.openWindows as OpenWindowsFn
      } catch (e) {
        this.activeWindowLoadFailed = true
        logger.warn('get-windows unavailable; window metadata disabled:', e)
      }
    }
    try {
      // On macOS get-windows prompts for Accessibility / Screen Recording on
      // every call while the permission is missing (we call it every frame).
      // Only ask for url/title when the permission is already granted.
      const options =
        process.platform === 'darwin'
          ? {
              accessibilityPermission: systemPreferences.isTrustedAccessibilityClient(false),
              screenRecordingPermission: systemPreferences.getMediaAccessStatus('screen') === 'granted'
            }
          : undefined
      let win: WindowInfo | undefined
      try {
        win = await this.activeWindow?.(options)
      } catch (e) {
        logger.debug('active window lookup failed:', e)
      }
      if (
        type === 'window' &&
        (win?.id === undefined || source.id.split(':')[1] !== String(win.id) || win.owner?.name === undefined)
      ) {
        try {
          const windows = await this.openWindows?.(options)
          const target = windows?.find((win) => win.id !== undefined && source.id.split(':')[1] === String(win.id))
          identified = { ...fallback, app: target?.owner?.name ?? '' }
        } catch (e) {
          logger.debug('open windows lookup failed:', e)
        }
      }
      if (!win) return identified
      if (type === 'window') {
        if (win.id === undefined || source.id.split(':')[1] !== String(win.id)) return identified
        identified = { ...fallback, app: win.owner?.name ?? identified.app }
      } else {
        if (!source.display_id || !win.bounds) return fallback
        // get-windows uses physical pixels on Windows; Electron displays use DIP.
        const bounds = process.platform === 'win32' ? screen.screenToDipRect(null, win.bounds) : win.bounds
        const display = screen.getAllDisplays().find((candidate) => String(candidate.id) === source.display_id)
        if (
          !display ||
          ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite) ||
          bounds.width <= 0 ||
          bounds.height <= 0 ||
          bounds.x >= display.bounds.x + display.bounds.width ||
          bounds.y >= display.bounds.y + display.bounds.height ||
          bounds.x + bounds.width <= display.bounds.x ||
          bounds.y + bounds.height <= display.bounds.y
        ) {
          return fallback
        }
        identified = { ...fallback, app: win.owner?.name ?? '' }
        if (
          bounds.x < display.bounds.x ||
          bounds.y < display.bounds.y ||
          bounds.x + bounds.width > display.bounds.x + display.bounds.width ||
          bounds.y + bounds.height > display.bounds.y + display.bounds.height
        ) {
          // Retain app identity for exclusion without attaching unrelated details.
          return identified
        }
      }
      return {
        app: identified.app,
        title: win.title ?? fallback.title,
        url: win.url ?? ''
      }
    } catch (e) {
      logger.debug('window metadata lookup failed:', e)
      return identified
    }
  }
}

export default new LiveCaptureService()
