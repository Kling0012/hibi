// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Declare window (P4-A): one input + Enter to declare, Esc to close, and the
// current active work with an end button. Look-and-feel is intentionally
// minimal here; it will be polished later. All actions go through the
// workSessionAPI exposed by the preload script (main process owns the logic).

import { useCallback, useEffect, useRef, useState } from 'react'
import type { WorkSessionState } from '@shared/work-session'
import './declare.css'

/** "h:mm:ss" over an hour, otherwise "mm:ss". */
function formatElapsed(startedAtIso: string, now: Date): string {
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

interface ShortcutResult {
  ok: boolean
  accelerator?: string
  error?: string
}

declare global {
  interface Window {
    workSessionAPI: {
      getState: () => Promise<WorkSessionState>
      declare: (title: string) => Promise<{ ok: boolean; error?: string }>
      endActive: () => Promise<{ ok: boolean; error?: string }>
      openManageUi: () => Promise<void>
      getShortcut: () => Promise<string>
      setShortcut: (accelerator: string) => Promise<ShortcutResult>
      hide: () => Promise<void>
      onStateChanged: (callback: (state: WorkSessionState) => void) => () => void
    }
  }
}

/** Map a DOM keyboard event to an Electron accelerator string. */
function eventToAccelerator(event: KeyboardEvent): string | null {
  const mod = event.ctrlKey || event.metaKey ? ['CommandOrControl'] : []
  const alt = event.altKey ? ['Alt'] : []
  const shift = event.shiftKey ? ['Shift'] : []
  const key = event.key
  let main = ''
  if (key === ' ') main = 'Space'
  else if (/^F\d{1,2}$/.test(key)) main = key
  else if (/^Arrow(Up|Down|Left|Right)$/.test(key)) main = key.replace('Arrow', '')
  else if (key.length === 1) main = key.toUpperCase()
  else return null // Enter/Esc/Tab and other named keys are poor triggers
  const parts = [...mod, ...alt, ...shift, main]
  if (parts.length === 1) return null // a bare key without modifiers
  return parts.join('+')
}

export function DeclareApp() {
  const [state, setState] = useState<WorkSessionState | null>(null)
  const [now, setNow] = useState<Date>(() => new Date())
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [shortcut, setShortcut] = useState('')
  const [recordingShortcut, setRecordingShortcut] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    void window.workSessionAPI.getState().then(setState)
    void window.workSessionAPI.getShortcut().then(setShortcut)
    const off = window.workSessionAPI.onStateChanged(setState)
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    inputRef.current?.focus()
    return () => {
      off()
      window.clearInterval(timer)
    }
  }, [])

  const hideWindow = useCallback((): void => {
    void window.workSessionAPI.hide()
  }, [])

  const submit = useCallback(async (): Promise<void> => {
    if (busy) return
    const value = title.trim()
    if (!value) {
      setMessage({ kind: 'err', text: '作業名を入力してください' })
      inputRef.current?.focus()
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const result = await window.workSessionAPI.declare(value)
      if (result.ok) {
        setTitle('')
        setMessage({ kind: 'ok', text: `宣言しました: ${value}` })
        window.setTimeout(hideWindow, 700)
      } else {
        setMessage({ kind: 'err', text: result.error ?? '宣言できませんでした' })
      }
    } finally {
      setBusy(false)
    }
  }, [busy, title, hideWindow])

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

  useEffect(() => {
    if (!recordingShortcut) return
    const onKeyDown = (event: KeyboardEvent): void => {
      event.preventDefault()
      event.stopPropagation()
      if (event.key === 'Escape') {
        setRecordingShortcut(false)
        setMessage(null)
        return
      }
      const accelerator = eventToAccelerator(event)
      if (!accelerator) return
      setRecordingShortcut(false)
      void window.workSessionAPI.setShortcut(accelerator).then((result) => {
        if (result.ok && result.accelerator) {
          setShortcut(result.accelerator)
          setMessage({ kind: 'ok', text: `ショートカットを ${result.accelerator} に変更しました` })
        } else {
          setMessage({ kind: 'err', text: result.error ?? '変更できませんでした' })
        }
      })
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [recordingShortcut])

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
    <div className="declare-root">
      {banner && <div className="banner">{banner}</div>}

      {active && (
        <div className="active-row">
          <div className="active-info">
            <div className="active-title">{active.title}</div>
            <div className="active-elapsed">経過 {formatElapsed(active.startedAt, now)}</div>
          </div>
          <button className="end-button" onClick={() => void endActive()} disabled={busy || !state?.connected}>
            終了
          </button>
        </div>
      )}

      <input
        ref={inputRef}
        className="declare-input"
        value={title}
        placeholder="作業名を入力して Enter"
        disabled={!canDeclare}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            void submit()
          } else if (event.key === 'Escape') {
            event.preventDefault()
            hideWindow()
          }
        }}
      />

      {state?.recentTitles && state.recentTitles.length > 0 && (
        <div className="recent-row">
          {state.recentTitles.map((recent) => (
            <button
              key={recent}
              className="recent-chip"
              title={recent}
              disabled={!canDeclare}
              onClick={() => {
                setTitle(recent)
                inputRef.current?.focus()
              }}
            >
              {recent}
            </button>
          ))}
        </div>
      )}

      {message && <div className={`message ${message.kind}`}>{message.text}</div>}

      <div className="footer">
        <span className="shortcut-label">
          ショートカット:{' '}
          {recordingShortcut ? (
            <b>キーを押してください（Escで中止）</b>
          ) : (
            <b>{shortcut || '-'}</b>
          )}
        </span>
        <button
          className="shortcut-button"
          disabled={recordingShortcut}
          onClick={() => {
            setRecordingShortcut(true)
            setMessage(null)
          }}
        >
          変更
        </button>
        <button className="manage-button" onClick={() => void window.workSessionAPI.openManageUi()}>
          管理画面
        </button>
        <span className="spacer" />
        <button className="close-button" onClick={hideWindow}>
          閉じる (Esc)
        </button>
      </div>
    </div>
  )
}
