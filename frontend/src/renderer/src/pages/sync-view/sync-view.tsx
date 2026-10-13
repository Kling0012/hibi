// SPDX-License-Identifier: Apache-2.0

// Pages served by the sync server (timeline / daily report / blog / search),
// shown inside the app in embed mode so phone, iPad and desktop share one UI.

import { Button } from '@arco-design/web-react'
import { syncBase, useWorkSession } from '@renderer/hooks/use-work-session'
import { getResolvedLang } from '@renderer/i18n'
import { ReactElement, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

/** Builds `${server}${path}` with embed=1 and the UI language before the hash. */
export function embedUrl(base: string, path: string, query: Record<string, string> = {}): string {
  const [pathname, hash = ''] = path.split('#')
  const params = new URLSearchParams({ embed: '1', lang: getResolvedLang(), ...query })
  return `${base}${pathname}?${params}${hash ? `#${hash}` : ''}`
}

/** Reachability of the sync server: null while checking. */
export function useServerReachable(base: string): boolean | null {
  const [ok, setOk] = useState<boolean | null>(null)
  useEffect(() => {
    if (!base) return
    let alive = true
    setOk(null)
    const ctl = new AbortController()
    const timer = window.setTimeout(() => ctl.abort(), 6000)
    fetch(`${base}/healthz`, { signal: ctl.signal })
      .then((r) => alive && setOk(r.ok))
      .catch(() => alive && setOk(false))
      .finally(() => window.clearTimeout(timer))
    return () => {
      alive = false
      ctl.abort()
    }
  }, [base])
  return ok
}

export function SyncUnavailable({ base, reachable }: { base: string; reachable: boolean | null }): ReactElement {
  const navigate = useNavigate()
  const { t } = useTranslation()
  if (base && reachable === null) return <div className="hb-empty">{t('sync.connecting')}</div>
  return (
    <div className="hb-empty">
      <h2>{base ? t('sync.unreachableTitle') : t('sync.notConfiguredTitle')}</h2>
      <p>{base ? t('sync.unreachableBody', { base }) : t('sync.notConfiguredBody')}</p>
      <Button type="primary" onClick={() => navigate('/settings')}>
        {t('sync.openSettings')}
      </Button>
    </div>
  )
}

interface Props {
  title: string
  sub?: string
  /** path on the sync server, e.g. "/ui/#report" */
  path: string
}

export default function SyncView({ title, sub, path }: Props): ReactElement {
  const state = useWorkSession()
  const base = syncBase(state)
  const reachable = useServerReachable(base)
  const { t } = useTranslation()
  const [nonce, setNonce] = useState(0)
  // Changing language re-renders this component (useTranslation) and thus
  // recomputes src; the iframe key changes → it reloads with the new lang.
  const src = base ? embedUrl(base, path) : ''

  return (
    <div className="hb-page">
      <div className="hb-page-head">
        <h1>{title}</h1>
        {sub && <span className="hb-sub">{sub}</span>}
        {base && reachable && (
          <div className="hb-actions">
            <button className="hb-link" onClick={() => setNonce((n) => n + 1)}>
              {t('sync.reload')}
            </button>
            <button className="hb-link" onClick={() => window.open(`${base}${path}`, '_blank')}>
              {t('sync.openInBrowser')}
            </button>
          </div>
        )}
      </div>
      <div className="hb-frame-wrap">
        {state && base && reachable ? (
          <iframe key={`${src}|${nonce}`} className="hb-frame" src={src} title={title} />
        ) : (
          state && <SyncUnavailable base={base} reachable={reachable} />
        )}
      </div>
    </div>
  )
}
