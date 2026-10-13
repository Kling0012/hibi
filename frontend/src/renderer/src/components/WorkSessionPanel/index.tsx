// SPDX-License-Identifier: Apache-2.0

// Work session declaration panel (home page, top). Declaring happens here in
// the app's main GUI - Enter in the input or the start button; no global
// shortcuts. All actions go through the workSessionAPI exposed by the preload
// script; the main process owns the logic and pushes state changes.

import { Button, Input } from '@arco-design/web-react'
import { formatElapsed, useNow, useWorkSession } from '@renderer/hooks/use-work-session'
import { getResolvedLang } from '@renderer/i18n'
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'

const clock = (iso: string): string =>
  new Date(iso).toLocaleTimeString(getResolvedLang() === 'en' ? 'en-GB' : getResolvedLang(), {
    hour: '2-digit',
    minute: '2-digit'
  })

export function WorkSessionPanel() {
  const state = useWorkSession()
  const now = useNow()
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const submit = useCallback(async (): Promise<void> => {
    if (busy) return
    const value = title.trim()
    if (!value) {
      setMessage({ kind: 'err', text: t('declare.noTitle') })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const result = await window.workSessionAPI.declare(value)
      if (result.ok) {
        setTitle('')
      } else {
        setMessage({ kind: 'err', text: result.error ?? t('declare.declareFailed') })
      }
    } finally {
      setBusy(false)
    }
  }, [busy, title, t])

  const endActive = useCallback(async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setMessage(null)
    try {
      const result = await window.workSessionAPI.endActive()
      setMessage(
        result.ok
          ? { kind: 'ok', text: t('declare.ended') }
          : { kind: 'err', text: result.error ?? t('declare.endFailed') }
      )
    } finally {
      setBusy(false)
    }
  }, [busy, t])

  const canDeclare = Boolean(state?.ready && state.enabled && state.connected) && !busy
  const active = state?.active ?? null
  const banner = !state
    ? t('common.loading')
    : !state.ready
      ? t('declare.connectingBackend')
      : !state.enabled
        ? t('declare.syncDisabled')
        : !state.connected
          ? `${t('declare.syncUnreachable')}${state.lastError ? `（${state.lastError}）` : ''}`
          : null

  return (
    <section className="hb-card hb-declare">
      {active ? (
        <div className="hb-active">
          <span className="hb-pulse" />
          <div style={{ minWidth: 0 }}>
            <div className="hb-active-title">{active.title}</div>
            <div className="hb-active-meta">{t('declare.activeMeta', { time: clock(active.startedAt) })}</div>
          </div>
          <span className="hb-elapsed">{formatElapsed(active.startedAt, now)}</span>
          <Button status="danger" loading={busy} onClick={() => void endActive()}>
            {t('declare.end')}
          </Button>
        </div>
      ) : (
        <p className="hb-declare-q">{t('declare.q')}</p>
      )}

      <div className="hb-declare-row">
        <Input
          value={title}
          placeholder={active ? t('declare.placeholderActive') : t('declare.placeholderIdle')}
          disabled={!canDeclare}
          maxLength={200}
          onChange={(value) => setTitle(value)}
          onPressEnter={() => void submit()}
        />
        <Button type="primary" disabled={!canDeclare} onClick={() => void submit()}>
          {active ? t('declare.next') : t('declare.start')}
        </Button>
      </div>

      {state?.recentTitles && state.recentTitles.length > 0 && (
        <div className="hb-recent">
          {state.recentTitles.slice(0, 8).map((recent) => (
            <button key={recent} className="hb-chip" title={recent} onClick={() => setTitle(recent)}>
              {recent}
            </button>
          ))}
        </div>
      )}

      {banner && <p className="hb-hint warn">{banner}</p>}
      {message && <p className={`hb-hint ${message.kind}`}>{message.text}</p>}
      {active && !message && !banner && <p className="hb-hint">{t('declare.nextHint')}</p>}
    </section>
  )
}
