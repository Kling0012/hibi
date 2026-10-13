// SPDX-License-Identifier: Apache-2.0

import { CSSProperties, ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import VaultTree from '@renderer/components/vault-tree'
import UpdateAvailableButton from '@renderer/components/UpdateAvailableButton'
import { useNavigation } from '@renderer/hooks/use-navigation'
import { formatElapsed, useNow, useWorkSession } from '@renderer/hooks/use-work-session'
import logo from '/src/assets/icons/hibi-logo.svg'
import './index.css'

const icon = (d: string): ReactElement => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d={d} />
  </svg>
)

const NAV_ITEMS = [
  { key: 'home', path: '/', icon: icon('M4 11.5 12 5l8 6.5V20h-5v-5h-6v5H4z') },
  { key: 'timeline', path: '/timeline', icon: icon('M3 7h11M3 12h18M3 17h7M17 4v6M21 14v6') },
  { key: 'report', path: '/report', icon: icon('M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5') },
  { key: 'blog', path: '/blog', icon: icon('M5 4h14v16H5zM8 8h8M8 12h8M8 16h5') },
  { key: 'search', path: '/search', icon: icon('M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm4.6 11.1L20 20') },
  { key: 'screen-monitor', path: '/screen-monitor', icon: icon('M3 5h18v11H3zM8 20h8M12 16v4') },
  {
    key: 'settings',
    path: '/settings',
    icon: icon(
      'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm7.4 3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.4 7.4 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.4 7.4 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1c.6.5 1.3.9 2 1.2l.4 2.6h5l.4-2.6c.7-.3 1.4-.7 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z'
    )
  }
] as const

/** Currently declared work, always visible under the navigation. */
const NowCard = ({ onOpen }: { onOpen: () => void }): ReactElement => {
  const state = useWorkSession()
  const now = useNow()
  const { t } = useTranslation()
  const active = state?.active ?? null
  return (
    <div className="hb-now" onClick={onOpen} title={t('nav.nowHint')}>
      <div className="hb-now-head">
        <span className={`hb-pulse${active ? '' : ' idle'}`} />
        <span>{active ? t('nav.nowWorking') : t('nav.nowIdle')}</span>
        {active && <span className="hb-time">{formatElapsed(active.startedAt, now)}</span>}
      </div>
      <div className="hb-now-title">{active ? active.title : t('nav.q')}</div>
    </div>
  )
}

const Sidebar = (): ReactElement => {
  const { navigateToMainTab, isMainTabActive } = useNavigation()
  const { t } = useTranslation()

  return (
    <aside className="hb-side" style={{ appRegion: 'drag' } as CSSProperties}>
      <div className="hb-brand" onClick={() => navigateToMainTab('home', '/')}>
        <img src={logo} alt="" />
        Hibi
      </div>

      <nav className="hb-nav" style={{ appRegion: 'no-drag' } as CSSProperties}>
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            className={`hb-nav-item${isMainTabActive(item.key) ? ' on' : ''}`}
            onClick={() => navigateToMainTab(item.key, item.path)}>
            {item.icon}
            {t(`nav.${item.key}`)}
          </button>
        ))}
      </nav>

      <div style={{ appRegion: 'no-drag' } as CSSProperties}>
        <NowCard onOpen={() => navigateToMainTab('home', '/')} />
      </div>

      <div className="hb-nav-sep">{t('nav.notes')}</div>
      <div className="hb-side-notes" style={{ appRegion: 'no-drag' } as CSSProperties}>
        <VaultTree />
      </div>
      <div className="mt-2" style={{ appRegion: 'no-drag' } as CSSProperties}>
        <UpdateAvailableButton />
      </div>
    </aside>
  )
}

export default Sidebar
