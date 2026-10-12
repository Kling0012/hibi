// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Work session declaration panel (home page, top). Declaring happens here in
// the app's main GUI - Enter in the input or the start button; no global
// shortcuts. All actions go through the workSessionAPI exposed by the preload
// script; the main process owns the logic and pushes state changes.

import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Tag, Typography } from '@arco-design/web-react'
import type { WorkSessionState } from '@shared/work-session'

const { Text } = Typography

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

export function WorkSessionPanel() {
  const [state, setState] = useState<WorkSessionState | null>(null)
  const [now, setNow] = useState<Date>(() => new Date())
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  useEffect(() => {
    void window.workSessionAPI.getState().then(setState)
    const off = window.workSessionAPI.onStateChanged(setState)
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => {
      off()
      window.clearInterval(timer)
    }
  }, [])

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
        setMessage({ kind: 'ok', text: `宣言しました: ${value}` })
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
    <Card
      headerStyle={{ width: '100%' }}
      bodyStyle={{ width: '100%' }}
      title={
        <div className="flex items-center gap-2 self-stretch">
          <div className="flex-1 text-black font-['Roboto'] text-sm font-medium leading-[22px]">作業宣言</div>
          <div
            className="text-[var(--text-color-text-2,#3F3F51)] font-['PingFang SC'] text-xs leading-[20px] cursor-pointer font-medium"
            onClick={() => void window.workSessionAPI.openManageUi()}>
            管理画面を開く
          </div>
        </div>
      }
      className="flex w-full p-3 flex-col items-start gap-2 self-stretch rounded-[10px] border border-[rgba(225,227,239,0.80)] bg-white">
      {banner && (
        <Text type="warning" style={{ fontSize: 12 }}>
          {banner}
        </Text>
      )}

      {active && (
        <div className="flex w-full items-center justify-between gap-2 self-stretch">
          <div className="flex min-w-0 items-baseline gap-2">
            <Text className="truncate" style={{ fontSize: 14, fontWeight: 600 }}>
              {active.title}
            </Text>
            <Text type="secondary" style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
              経過 {formatElapsed(active.startedAt, now)}
            </Text>
          </div>
          <Button size="mini" status="danger" loading={busy} onClick={() => void endActive()}>
            終了
          </Button>
        </div>
      )}

      <div className="flex w-full items-center gap-2 self-stretch">
        <Input
          value={title}
          placeholder="これから何をしますか？（Enter で宣言）"
          disabled={!canDeclare}
          onChange={(value) => setTitle(value)}
          onPressEnter={() => void submit()}
        />
        <Button type="primary" disabled={!canDeclare} onClick={() => void submit()}>
          {active ? '次の作業を開始' : '開始'}
        </Button>
      </div>
      {active && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          次の作業を始めると、今の作業はここで終わります。
        </Text>
      )}

      {state?.recentTitles && state.recentTitles.length > 0 && (
        <div className="flex w-full flex-wrap items-center gap-1 self-stretch">
          {state.recentTitles.map((recent) => (
            <Tag
              key={recent}
              className="cursor-pointer"
              title={recent}
              onClick={() => setTitle(recent)}>
              {recent}
            </Tag>
          ))}
        </div>
      )}

      {message && (
        <Text type={message.kind === 'ok' ? 'success' : 'error'} style={{ fontSize: 12 }}>
          {message.text}
        </Text>
      )}
    </Card>
  )
}
