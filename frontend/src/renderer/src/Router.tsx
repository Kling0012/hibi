// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// import '@renderer/databases'

import { FC, useEffect, useMemo } from 'react'
import { HashRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import HomePage from './pages/home/home-page'
import VaultPage from './pages/vault/Vault'
import ScreenMonitor from './pages/screen-monitor/screen-monitor'
import Settings from './pages/settings/settings'

import SyncView from './pages/sync-view/sync-view'
import Sidebar from './components/Sidebar'
import 'allotment/dist/style.css'
import { useEvents } from './hooks/use-events'
import { useObservableTask } from './atom/event-loop.atom'
import { IpcServerPushChannel } from '@shared/ipc-server-push-channel'

const AppContent: FC = () => {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { startPolling, stopPolling } = useEvents()
  useObservableTask({
    active: startPolling,
    inactive: stopPolling
  })

  // Listen for tray navigation event
  useEffect(() => {
    const handleNavigateToScreenMonitor = () => {
      navigate('/screen-monitor')
    }

    window.electron.ipcRenderer.on(IpcServerPushChannel.Tray_NavigateToScreenMonitor, handleNavigateToScreenMonitor)

    return () => {
      window.electron.ipcRenderer.removeListener(
        IpcServerPushChannel.Tray_NavigateToScreenMonitor,
        handleNavigateToScreenMonitor
      )
    }
  }, [navigate])

  // Global listener for tray toggle recording - navigate to screen-monitor and pass toggle signal
  useEffect(() => {
    const handleTrayToggleRecording = () => {
      // Navigate to screen-monitor page with state indicating recording should be toggled
      navigate('/screen-monitor', { state: { toggleRecording: true } })
    }

    window.electron.ipcRenderer.on(IpcServerPushChannel.Tray_ToggleRecording, handleTrayToggleRecording)

    return () => {
      window.electron.ipcRenderer.removeListener(IpcServerPushChannel.Tray_ToggleRecording, handleTrayToggleRecording)
    }
  }, [navigate])

  useEffect(() => {
    startPolling()

    return () => stopPolling()
  }, [])

  const routes = useMemo(() => {
    return (
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/vault" element={<VaultPage />} />
        <Route path="/screen-monitor" element={<ScreenMonitor />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/timeline" element={<SyncView title={t('sync.timeline')} path="/ui/#today" />} />
        <Route
          path="/report"
          element={<SyncView title={t('sync.report')} sub={t('sync.reportSub')} path="/ui/#report" />}
        />
        <Route path="/blog" element={<SyncView title={t('sync.blog')} path="/blog/" />} />
        <Route
          path="/search"
          element={<SyncView title={t('sync.search')} sub={t('sync.searchSub')} path="/ui/#search" />}
        />
      </Routes>
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t])

  return (
    <div className="hb-app">
      <Sidebar />
      <main className="hb-main">{routes}</main>
    </div>
  )
}

const Router: FC = () => {
  return (
    <HashRouter>
      <AppContent />
    </HashRouter>
  )
}

export default Router
