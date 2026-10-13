// SPDX-License-Identifier: Apache-2.0

import fs from 'node:fs'
import path from 'node:path'
import { app, powerMonitor } from 'electron'
import axios from 'axios'
import { getBackendPort } from '@main/backend'
import { getLogger } from '@shared/logger/main'
import liveCaptureService from './LiveCaptureService'

const logger = getLogger('AwayService')
type AwayReason = 'idle' | 'lock' | 'suspend'
interface AwayPeriod {
  started_at: string
  reason: AwayReason
}
interface AwayNotification {
  endpoint: 'start' | 'end'
  period: AwayPeriod & { ended_at?: string }
}

class AwayService {
  private awayMinutes = 5
  private timer: NodeJS.Timeout | null = null
  private away: AwayPeriod | null = null
  private locked = false
  private suspended = false
  private pending: AwayNotification[] = []
  private notifying = false
  private readonly onLock = (): void => {
    this.locked = true
    this.begin('lock', Date.now())
  }
  private readonly onSuspend = (): void => {
    this.suspended = true
    this.begin('suspend', Date.now())
  }
  private readonly onUnlock = (): void => {
    this.locked = false
    this.end()
  }
  private readonly onResume = (): void => {
    this.suspended = false
    this.end()
  }

  start(): void {
    if (this.timer) return
    try {
      const file = this.settingsPath()
      if (fs.existsSync(file)) {
        const settings = JSON.parse(fs.readFileSync(file, 'utf8'))
        if (this.validMinutes(settings.awayMinutes)) this.awayMinutes = settings.awayMinutes
      }
    } catch (error) {
      logger.warn('failed to load away settings; using default:', error)
    }
    try {
      powerMonitor.on('lock-screen', this.onLock)
      powerMonitor.on('unlock-screen', this.onUnlock)
      powerMonitor.on('suspend', this.onSuspend)
      powerMonitor.on('resume', this.onResume)
      this.timer = setInterval(() => this.poll(), 15_000)
    } catch (error) {
      logger.warn('away detection unavailable:', error)
      this.stop()
    }
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
    try {
      powerMonitor.removeListener('lock-screen', this.onLock)
      powerMonitor.removeListener('unlock-screen', this.onUnlock)
      powerMonitor.removeListener('suspend', this.onSuspend)
      powerMonitor.removeListener('resume', this.onResume)
    } catch (error) {
      logger.warn('failed to remove away listeners:', error)
    }
    this.end()
    liveCaptureService.setAwayPaused(false)
  }

  getSettings(): { awayMinutes: number } {
    return { awayMinutes: this.awayMinutes }
  }

  setSettings(settings: { awayMinutes: number }): { awayMinutes: number } {
    if (!settings || !this.validMinutes(settings.awayMinutes)) {
      throw new Error('Invalid awayMinutes')
    }
    try {
      fs.writeFileSync(this.settingsPath(), JSON.stringify(settings, null, 2), 'utf8')
    } catch (error) {
      logger.warn('failed to save away settings:', error)
      throw error
    }
    this.awayMinutes = settings.awayMinutes
    if (this.awayMinutes === 0) this.end()
    return this.getSettings()
  }

  private settingsPath(): string {
    return path.join(app.getPath('userData'), 'away.json')
  }

  private validMinutes(value: unknown): value is number {
    return typeof value === 'number' && [0, 3, 5, 10, 15].includes(value)
  }

  private poll(): void {
    try {
      if (this.awayMinutes > 0) {
        const idle = powerMonitor.getSystemIdleTime()
        if (!Number.isFinite(idle) || idle < 0) throw new Error('Invalid system idle time')
        if (this.away && idle < 10 && !this.locked && !this.suspended) {
          this.end()
        } else if (!this.away && idle >= this.awayMinutes * 60) {
          this.begin('idle', Date.now() - idle * 1000)
        }
      }
    } catch (error) {
      logger.warn('away idle check failed:', error)
      this.end()
    }
    void this.notifyBackend()
  }

  private begin(reason: AwayReason, startedAt: number): void {
    if (this.awayMinutes === 0 || this.away) return
    this.away = { started_at: new Date(startedAt).toISOString(), reason }
    liveCaptureService.setAwayPaused(true)
    this.pending.push({ endpoint: 'start', period: { ...this.away } })
    void this.notifyBackend()
  }

  private end(): void {
    if (!this.away) return
    const period = { ...this.away, ended_at: new Date().toISOString() }
    this.away = null
    liveCaptureService.setAwayPaused(false)
    this.pending.push({ endpoint: 'end', period })
    void this.notifyBackend()
  }

  private async notifyBackend(): Promise<void> {
    if (this.notifying || !this.pending.length) return
    this.notifying = true
    try {
      while (this.pending.length) {
        const notification = this.pending[0]
        const response = await axios.post(
          `http://127.0.0.1:${getBackendPort()}/api/live/away/${notification.endpoint}`,
          notification.period,
          { timeout: 5000 }
        )
        if (response.data?.code !== 0) throw new Error(response.data?.message || 'Away notification failed')
        if (
          notification.endpoint === 'end' &&
          Date.parse(notification.period.ended_at!) - Date.parse(notification.period.started_at) >= 60_000 &&
          !response.data?.data?.enqueued
        ) {
          throw new Error('Away event was not queued')
        }
        this.pending.shift()
      }
      liveCaptureService.setAwayPaused(this.away !== null)
    } catch (error) {
      // Retry in order on the next poll. Only away detection degrades;
      // recording remains available even when its notification fails.
      liveCaptureService.setAwayPaused(false)
      logger.warn('away backend notification failed:', error)
    } finally {
      this.notifying = false
    }
  }
}

export default new AwayService()
