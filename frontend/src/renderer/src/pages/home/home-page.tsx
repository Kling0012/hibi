// SPDX-License-Identifier: Apache-2.0

// Home: declare work, see today at a glance (work time, recording state,
// today's lanes from the sync server), plus todos and tips.

import React, { ReactElement, useEffect, useState } from 'react'
import { useSelector } from 'react-redux'
import { useNavigate } from 'react-router-dom'
import { Allotment } from 'allotment'
import { get } from 'lodash'
import { useMemoizedFn, useUnmount } from 'ahooks'
import { useTranslation } from 'react-i18next'
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
import { getResolvedLang } from '@renderer/i18n'

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Tokyo'

const todayStr = (): string => {
  const d = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// "10月13日（月）" / "Mon, Oct 13" / "10月13日周一"
const todayLabel = (): string => {
  const d = new Date()
  const lang = getResolvedLang()
  if (lang === 'en') {
    return new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).format(d)
  }
  if (lang === 'zh') {
    return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' }).format(d)
  }
  return new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric', weekday: 'short' }).format(d)
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
  const { t } = useTranslation()
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
        {t('home.today')}
        <button className="hb-link" onClick={() => navigate('/timeline')}>
          {t('home.timelineLink')}
        </button>
      </h2>
      <div className="hb-stats">
        <div className="hb-stat">
          <span className="k">{t('home.workTime')}</span>
          <span className="v">{stat ? formatMinutes(stat.session_minutes) : '—'}</span>
        </div>
        <div className="hb-stat">
          <span className="k">{t('home.pcActivity')}</span>
          <span className="v small">
            {stat ? `${formatMinutes(stat.active_minutes)}${t('home.countItems', { count: stat.mine })}` : '—'}
          </span>
        </div>
        <div className="hb-stat">
          <span className="k">{t('home.aiWork')}</span>
          <span className="v small">
            {stat
              ? `${formatMinutes(stat.ai_active_minutes)}${t('home.countItems', { count: stat.ai_work })}`
              : '—'}
          </span>
        </div>
        <div className="hb-stat">
          <span className="k">{t('home.screenRecording')}</span>
          <span className="v small" style={{ cursor: 'pointer' }} onClick={() => navigate('/screen-monitor')}>
            <span className={`hb-dot${recording ? ' on' : ''}`} />
            {recording ? t('home.recording') : t('home.stopped')}
            {live && live.screen_summaries > 0 && (
              <span className="hb-sub" style={{ fontWeight: 400, marginLeft: 6 }}>
                {t('home.summaries', { count: live.screen_summaries })}
              </span>
            )}
          </span>
        </div>
      </div>
    </section>
  )
}

/** A part of the sync server's page (`only=lanes|todos`), sized to its content. */
const ServerPart = ({ base, only, title, minHeight }: { base: string; only: string; title: string; minHeight: number }): ReactElement => {
  const [height, setHeight] = useState(minHeight)
  useEffect(() => {
    const onMessage = (e: MessageEvent): void => {
      const d = e.data
      if (d?.hibi === 'height' && (d.only ?? only) === only && typeof d.h === 'number' && d.h > 0) setHeight(d.h)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [only])
  return (
    <iframe
      className="hb-lanes-frame"
      style={{ height }}
      src={embedUrl(base, '/ui/#today', { only })}
      title={title}
    />
  )
}

const HomePage: React.FC = () => {
  const { t } = useTranslation()
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
              <span className="hb-sub">{t('home.todayRecord')}</span>
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
                    <ServerPart base={base} only="lanes" title={t('home.todayTimeline')} minHeight={140} />
                  </section>
                )}
                <div className="hb-cols">
                  {base && reachable ? (
                    <section className="hb-card">
                      <ServerPart base={base} only="todos" title={t('home.todo')} minHeight={200} />
                    </section>
                  ) : (
                    <ToDoCard selectedDays={null} />
                  )}
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
