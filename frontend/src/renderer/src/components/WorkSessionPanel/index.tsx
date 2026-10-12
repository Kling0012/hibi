// SPDX-License-Identifier: Apache-2.0

// Work session declaration panel (home page, top). Declaring happens here in
// the app's main GUI - Enter in the input or the start button; no global
// shortcuts. All actions go through the workSessionAPI exposed by the preload
// script; the main process owns the logic and pushes state changes.

import { useCallback, useState } from 'react'
import { Button, Input } from '@arco-design/web-react'
import { formatElapsed, useNow, useWorkSession } from '@renderer/hooks/use-work-session'

const clock = (iso: string): string =>
  new Date(iso).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })

export function WorkSessionPanel() {
  const state = useWorkSession()
  const now = useNow()
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const submit = useCallback(async (): Promise<void> => {
    if (busy) return
    const value = title.trim()
    if (!value) {
      setMessage({ kind: 'err', text: '作業名を入力してください' })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const result = await window.workSessionAPI.declare(value)
      if (result.ok) {
        setTitle('')
      } else {
        setMessage({ kind: 'err', text: result.error ?? '宣言できませんでした' })
      }
    } finally {
      setBusy(false)
    }
  }, [busy, title])

  const endActive = useCallback(async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setMessage(null)
    try {
      const result = await window.workSessionAPI.endActive()
      setMessage(
        result.ok
          ? { kind: 'ok', text: '作業を終了しました' }
          : { kind: 'err', text: result.error ?? '終了できませんでした' }
      )
    } finally {
      setBusy(false)
    }
  }, [busy])

  const canDeclare = Boolean(state?.ready && state.enabled && state.connected) && !busy
  const active = state?.active ?? null
  const banner = !state
    ? '読み込み中…'
    : !state.ready
      ? 'バックエンドに接続中…'
      : !state.enabled
        ? '同期サーバが無効です（設定で有効化してください）'
        : !state.connected
          ? `同期サーバに接続できません${state.lastError ? `（${state.lastError}）` : ''}`
          : null

  return (
    <section className="hb-card hb-declare">
      {active ? (
        <div className="hb-active">
          <span className="hb-pulse" />
          <div style={{ minWidth: 0 }}>
            <div className="hb-active-title">{active.title}</div>
            <div className="hb-active-meta">{clock(active.startedAt)} から作業中 · 終わりは AI が画面から判断します</div>
          </div>
          <span className="hb-elapsed">{formatElapsed(active.startedAt, now)}</span>
          <Button status="danger" loading={busy} onClick={() => void endActive()}>
            終了
          </Button>
        </div>
      ) : (
        <p className="hb-declare-q">これから何をする？</p>
      )}

      <div className="hb-declare-row">
        <Input
          value={title}
          placeholder={active ? '次の作業（Enter で切り替え）' : '例：同期サーバの管理画面を作る（Enter で開始）'}
          disabled={!canDeclare}
          maxLength={200}
          onChange={(value) => setTitle(value)}
          onPressEnter={() => void submit()}
        />
        <Button type="primary" disabled={!canDeclare} onClick={() => void submit()}>
          {active ? '次の作業へ' : '開始'}
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
      {active && !message && !banner && (
        <p className="hb-hint">次の作業を始めると、今の作業はその時刻で終わります。</p>
      )}
    </section>
  )
}
