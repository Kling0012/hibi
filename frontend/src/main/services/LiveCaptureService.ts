// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Live screen capture (P1-B). Captures selected sources as JPEG at
// capture.live_fps and posts each frame to the backend /api/live/frame
// together with the frontmost window metadata (app / title / url) from
// get-windows. Everything is fail-soft: a failed capture or upload is
// logged and skipped; the loop keeps running.

import type { CaptureSource } from '@interface/common/source'
import { getBackendPort } from '@main/backend'
import { getLogger } from '@shared/logger/main'
import axios from 'axios'
import { desktopCapturer, systemPreferences } from 'electron'

const logger = getLogger('LiveCaptureService')

interface WindowMeta {
  app: string
  title: string
  url: string
}

// type only import (the package is ESM-only; loaded dynamically at runtime)
type ActiveWindowFn = (options?: { accessibilityPermission?: boolean; screenRecordingPermission?: boolean }) => Promise<
  | {
      title?: string
      url?: string
      owner?: { name?: string }
    }
  | undefined
>

class LiveCaptureService {
  private timer: NodeJS.Timeout | null = null
  private running = false
  private generation = 0
  private busy = false
  private awayPaused = false
  private fps = 1.0
  private activeWindow: ActiveWindowFn | null = null
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
    const fps = await this.fetchLiveFps()
    if (!this.running || generation !== this.generation) return
    this.fps = fps
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

  private async fetchLiveFps(): Promise<number> {
    try {
      const res = await axios.get(`http://127.0.0.1:${getBackendPort()}/api/settings/general`, {
        timeout: 5000
      })
      const fps = Number(res.data?.data?.capture?.live_fps)
      if (Number.isFinite(fps) && fps > 0) {
        return Math.min(1, Math.max(0.2, fps))
      }
    } catch (e) {
      logger.warn('failed to fetch capture settings; using default fps', e)
    }
    return 1.0
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
        const meta = await this.getWindowMeta()
        const params = new URLSearchParams({
          app: meta.app,
          title: meta.title,
          url: meta.url
        })
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

  private async getWindowMeta(): Promise<WindowMeta> {
    if (!this.activeWindow && !this.activeWindowLoadFailed) {
      try {
        // ESM-only package: dynamic import from the CJS bundle
        const mod = await import('get-windows')
        this.activeWindow = mod.activeWindow as ActiveWindowFn
      } catch (e) {
        this.activeWindowLoadFailed = true
        logger.warn('get-windows unavailable; window metadata disabled:', e)
      }
    }
    try {
      // On macOS get-windows prompts for Accessibility / Screen Recording on
      // every call while the permission is missing (we call it every frame).
      // Only ask for url/title when the permission is already granted.
      const win = await this.activeWindow?.(
        process.platform === 'darwin'
          ? {
              accessibilityPermission: systemPreferences.isTrustedAccessibilityClient(false),
              screenRecordingPermission: systemPreferences.getMediaAccessStatus('screen') === 'granted'
            }
          : undefined
      )
      return {
        app: win?.owner?.name ?? '',
        title: win?.title ?? '',
        url: win?.url ?? ''
      }
    } catch (e) {
      logger.debug('activeWindow() failed:', e)
      return { app: '', title: '', url: '' }
    }
  }
}

export default new LiveCaptureService()
