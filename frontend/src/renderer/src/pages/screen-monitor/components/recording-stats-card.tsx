// SPDX-License-Identifier: Apache-2.0

// Live recording counters (since the app started): frames sent to the AI,
// summaries created and events uploaded to the sync server.

import React, { useEffect, useState } from 'react'
import { get } from 'lodash'
import axiosInstance from '@renderer/services/axiosConfig'

/** Legacy shape still passed by the timeline; the card reads live status itself. */
export interface RecordingStats {
  processed_screenshots: number
  failed_screenshots: number
  generated_activities: number
  next_activity_eta_seconds: number
  recent_errors: Array<{
    error_message: string
    processor_name: string
    timestamp: string
  }>
  recent_screenshots: string[]
}

interface LiveCounts {
  frames: number
  activities: number
  summaries: number
  excluded: number
  sent: number | null
  pending: number | null
  lastError: string
}

async function loadCounts(): Promise<LiveCounts | null> {
  try {
    const [live, sync] = await Promise.all([
      axiosInstance.get('/api/live/status'),
      axiosInstance.get('/api/sync_settings').catch(() => null)
    ])
    const m = get(live, 'data.data.monitor')
    if (!m) return null
    const s = sync ? get(sync, 'data.data.status') : null
    return {
      frames: Number(m.frames_pushed ?? m.frames_received ?? 0),
      activities: Number(m.live_activities ?? 0),
      summaries: Number(m.screen_summaries ?? 0),
      excluded: Number(m.frames_excluded ?? 0),
      sent: s ? Number(s.sent_count ?? 0) : null,
      pending: s ? Number(s.pending_count ?? 0) : null,
      lastError: s?.last_error ?? ''
    }
  } catch {
    return null
  }
}

const RecordingStatsCard: React.FC<{ stats?: RecordingStats | null }> = () => {
  const [c, setC] = useState<LiveCounts | null>(null)

  useEffect(() => {
    let alive = true
    const run = (): void => {
      void loadCounts().then((v) => alive && setC(v))
    }
    run()
    const timer = window.setInterval(run, 10_000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  if (!c) return null
  const n = (v: number): React.ReactElement => <span className="text-[#00B42A] font-medium">{v}</span>

  return (
    <div className="mt-2 text-xs text-[#86909C] leading-5">
      <span>起動してから </span>
      {n(c.frames)}
      <span> 枚を AI に送信 · 活動の要約 </span>
      {n(c.activities)}
      <span> 件 · 画面の書き起こし </span>
      {n(c.summaries)}
      <span> 件</span>
      {c.excluded > 0 && <span> · 除外 {c.excluded} 枚</span>}
      {c.sent !== null && (
        <span>
          {' '}
          · 同期サーバへ {c.sent} 件送信{c.pending ? `（未送信 ${c.pending} 件）` : ''}
        </span>
      )}
      {c.lastError && <div className="text-[#FF4D4F]">同期のエラー: {c.lastError}</div>}
    </div>
  )
}

export default RecordingStatsCard
