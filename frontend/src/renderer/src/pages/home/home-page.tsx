// SPDX-License-Identifier: Apache-2.0

// Home: declare work, see today at a glance (work time, recording state,
// today's lanes from the sync server), plus todos and tips.

import React, { ReactElement, useEffect, useState } from 'react'
import { useSelector } from 'react-redux'
import { useNavigate } from 'react-router-dom'
import { Allotment } from 'allotment'
import { get } from 'lodash'
import { useMemoizedFn, useUnmount } from 'ahooks'
import './home-page.css'
import { useAllotment } from '@renderer/hooks/use-allotment'
import AIToggleButton from '@renderer/components/ai-toggle-button'
import AIAssistant from '@renderer/components/ai-assistant'
import { WorkSessionPanel } from '@renderer/components/WorkSessionPanel'
import { ProactiveFeedCard } from './components/proactive-feed-card'
import { ToDoCard } from './components/to-do-card'
import { setActiveConversationId, toggleHomeAiAssistant } from '@renderer/store/chat-history'
import { RootState, useAppDispatch } from '@renderer/store'
import axiosInstance from '@renderer/services/axiosConfig'
import { formatMinutes, syncBase, useWorkSession } from '@renderer/hooks/use-work-session'
import { embedUrl, useServerReachable } from '../sync-view/sync-view'

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Tokyo'

const todayStr = (): string => {
  const d = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const todayLabel = (): string => {
  const d = new Date()
  return `${d.getMonth() + 1}月${d.getDate()}日（${'日月火水木金土'[d.getDay()]}）`
}

interface DayStat {
  active_minutes: number
  ai_active_minutes: number
  session_minutes: number
  mine: number
  ai_work: number
}

interface LiveStatus {
  last_frame_at: string | null
  screen_summaries: number
  live_activities: number
  live_available: boolean | null
}

/** Polls a loader every `ms` (and once immediately). */
function usePoll<T>(load: () => Promise<T | null>, ms: number, deps: unknown[]): T | null {
  const [value, setValue] = useState<T | null>(null)
  useEffect(() => {
    let alive = true
    const run = (): void => {
      load()
        .then((v) => alive && setValue(v))
        .catch(() => alive && setValue(null))
    }
    run()
    const timer = window.setInterval(run, ms)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return value
}

const TodayCard = ({ base, reachable }: { base: string; reachable: boolean | null }): ReactElement => {
  const navigate = useNavigate()
  const day = todayStr()
  const stat = usePoll<DayStat>(
    async () => {
      if (!base || !reachable) return null
      const r = await fetch(`${base}/v1/stats/daily?from=${day}&to=${day}&tz=${encodeURIComponent(TZ)}`)
      if (!r.ok) return null
      const j = await r.json()
      return (j.days?.[0] as DayStat) ?? null
    },
    60_000,
    [base, reachable, day]
  )
  const live = usePoll<LiveStatus>(
    async () => get(await axiosInstance.get('/api/live/status'), 'data.data.monitor', null),
    15_000,
    []
  )
  const lastFrame = live?.last_frame_at ? new Date(live.last_frame_at).getTime() : 0
  const recording = lastFrame > 0 && Date.now() - lastFrame < 2 * 60_000

  return (
    <section className="hb-card">
      <h2>
        今日
        <button className="hb-link" onClick={() => navigate('/timeline')}>
          タイムライン
        </button>
      </h2>
      <div className="hb-stats">
        <div className="hb-stat">
          <span className="k">作業時間</span>
          <span className="v">{stat ? formatMinutes(stat.session_minutes) : '—'}</span>
        </div>
        <div className="hb-stat">
          <span className="k">PC の活動</span>
          <span className="v small">{stat ? `${formatMinutes(stat.active_minutes)}（${stat.mine}件）` : '—'}</span>
        </div>
        <div className="hb-stat">
          <span className="k">AI の作業</span>
          <span className="v small">
            {stat ? `${formatMinutes(stat.ai_active_minutes)}（${stat.ai_work}件）` : '—'}
          </span>
        </div>
        <div className="hb-stat">
          <span className="k">画面の記録</span>
          <span className="v small" style={{ cursor: 'pointer' }} onClick={() => navigate('/screen-monitor')}>
            <span className={`hb-dot${recording ? ' on' : ''}`} />
            {recording ? '記録中' : '停止中'}
            {live && live.screen_summaries > 0 && (
              <span className="hb-sub" style={{ fontWeight: 400, marginLeft: 6 }}>
                要約 {live.screen_summaries}件
              </span>
            )}
          </span>
        </div>
      </div>
    </section>
  )
}

/** Today's lanes from the sync server, sized to its content. */
const TodayLanes = ({ base }: { base: string }): ReactElement => {
  const [height, setHeight] = useState(140)
  useEffect(() => {
    const onMessage = (e: MessageEvent): void => {
      if (e.data?.hibi === 'height' && typeof e.data.h === 'number' && e.data.h > 0) setHeight(e.data.h)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])
  return (
    <iframe
      className="hb-lanes-frame"
      style={{ height }}
      src={embedUrl(base, '/ui/#today', { only: 'lanes' })}
      title="今日のタイムライン"
    />
  )
}

const HomePage: React.FC = () => {
  const isVisible = useSelector((state: RootState) => state.chatHistory.home.aiAssistantVisible)
  const activeConversationId = useSelector((state: RootState) => state.chatHistory.activeConversationId)
  const { controller, defaultSizes, leftMinSize, rightMinSize } = useAllotment(isVisible)
  const dispatch = useAppDispatch()
  const state = useWorkSession()
  const base = syncBase(state)
  const reachable = useServerReachable(base)
  useUnmount(() => {
    dispatch(setActiveConversationId(null))
    dispatch(toggleHomeAiAssistant(false))
  })
  const openChat = useMemoizedFn(() => dispatch(toggleHomeAiAssistant(true)))

  return (
    <div className={`flex flex-row h-full allotmentContainer ${!isVisible ? 'allotment-disabled' : ''}`}>
      <Allotment separator={false} ref={controller} defaultSizes={defaultSizes}>
        <Allotment.Pane minSize={leftMinSize}>
          <div className="hb-page" style={{ height: '100%' }}>
            <div className="hb-page-head" style={{ appRegion: 'drag' } as React.CSSProperties}>
              <h1>{todayLabel()}</h1>
              <span className="hb-sub">今日の記録</span>
              <div className="hb-actions" style={{ appRegion: 'no-drag' } as React.CSSProperties}>
                <AIToggleButton onClick={openChat} isActive={isVisible} />
              </div>
            </div>
            <div className="hb-scroll">
              <div className="hb-home">
                <div className="hb-row">
                  <WorkSessionPanel />
                  <TodayCard base={base} reachable={reachable} />
                </div>
                {base && reachable && (
                  <section className="hb-card">
                    <TodayLanes base={base} />
                  </section>
                )}
                <div className="hb-cols">
                  <ToDoCard selectedDays={null} />
                  <ProactiveFeedCard />
                </div>
              </div>
            </div>
          </div>
        </Allotment.Pane>
        <Allotment.Pane minSize={rightMinSize}>
          {isVisible && (
            <AIAssistant
              visible={isVisible}
              onClose={() => dispatch(toggleHomeAiAssistant(false))}
              pageName="home"
              initConversationId={activeConversationId}
            />
          )}
        </Allotment.Pane>
      </Allotment>
    </div>
  )
}

export default HomePage
