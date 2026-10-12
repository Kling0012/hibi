// SPDX-License-Identifier: Apache-2.0

// Shared access to the work-session state owned by the main process
// (declare panel, sidebar indicator, sync-server pages).

import { useEffect, useState } from 'react'
import type { WorkSessionState } from '@shared/work-session'

export function useWorkSession(): WorkSessionState | null {
  const [state, setState] = useState<WorkSessionState | null>(null)
  useEffect(() => {
    void window.workSessionAPI.getState().then(setState)
    return window.workSessionAPI.onStateChanged(setState)
  }, [])
  return state
}

/** Re-renders every `intervalMs` while mounted. */
export function useNow(intervalMs = 1000): Date {
  const [now, setNow] = useState<Date>(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

/** "h:mm:ss" over an hour, otherwise "mm:ss". */
export function formatElapsed(startedAtIso: string, now: Date): string {
  const started = new Date(startedAtIso).getTime()
  if (!Number.isFinite(started)) return '00:00'
  let sec = Math.max(0, Math.floor((now.getTime() - started) / 1000))
  const h = Math.floor(sec / 3600)
  sec -= h * 3600
  const m = Math.floor(sec / 60)
  const s = sec - m * 60
  const pad = (n: number): string => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

/** "1時間20分" / "45分". */
export function formatMinutes(min: number): string {
  const m = Math.max(0, Math.round(min))
  const h = Math.floor(m / 60)
  return h > 0 ? `${h}時間${m % 60 ? `${m % 60}分` : ''}` : `${m}分`
}

export function syncBase(state: WorkSessionState | null): string {
  return (state?.serverUrl ?? '').trim().replace(/\/+$/, '')
}
