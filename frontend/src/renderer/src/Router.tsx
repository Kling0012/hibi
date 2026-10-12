// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// import '@renderer/databases'

import { FC, useEffect, useMemo } from 'react'
import { HashRouter, Route, Routes, useNavigate } from 'react-router-dom'

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
        <Route path="/timeline" element={<SyncView title="タイムライン" path="/ui/#today" />} />
        <Route path="/report" element={<SyncView title="日報" sub="AI がその日の記録からまとめます" path="/ui/#report" />} />
        <Route path="/blog" element={<SyncView title="ブログ" path="/blog/" />} />
        <Route path="/search" element={<SyncView title="検索" sub="自分の記録と AI の作業をまとめて探せます" path="/ui/#search" />} />
      </Routes>
    )
  }, [])

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
