// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

// Declare window + global shortcut (P4-A): a small always-on-top palette
// opened from the tray menu or the global shortcut (default
// Ctrl+Alt+Space / Cmd+Option+Space, changeable from the window itself).

import path from 'node:path'
import { BrowserWindow, globalShortcut, screen, shell } from 'electron'
import { is } from '@electron-toolkit/utils'
import { getLogger } from '@shared/logger/main'
import { IpcServerPushChannel } from '@shared/ipc-server-push-channel'
import { localStoreService } from './LocalStoreService'
import { workSessionService } from './WorkSessionService'

const logger = getLogger('DeclareWindowService')

const SHORTCUT_KEY = 'workSession.declareShortcut'
const DEFAULT_SHORTCUT = 'CommandOrControl+Alt+Space'

class DeclareWindowService {
  private win: BrowserWindow | null = null
  private currentShortcut: string | null = null

  /** Called once from app.whenReady(). Fail-soft: a failing shortcut or
   * subscription only disables the shortcut/window, never the app. */
  init(): void {
    try {
      this.registerShortcut(this.getShortcut())
    } catch (error) {
      logger.error('failed to register declare shortcut:', error)
    }
    workSessionService.subscribe((state) => {
      this.pushState(state)
    })
    logger.info(`DeclareWindowService initialized (shortcut: ${this.currentShortcut ?? 'none'})`)
  }

  getShortcut(): string {
    const value = localStoreService.getSetting(SHORTCUT_KEY)
    return typeof value === 'string' && value.trim() ? value.trim() : DEFAULT_SHORTCUT
  }

  async setShortcut(accelerator: string): Promise<{ ok: boolean; accelerator?: string; error?: string }> {
    const acc = (accelerator ?? '').trim()
    if (!acc) {
      return { ok: false, error: 'ショートカットが空です' }
    }
    const previous = this.currentShortcut
    if (previous && previous !== acc) {
      globalShortcut.unregister(previous)
    }
    let registered = false
    try {
      registered = globalShortcut.register(acc, () => this.toggle())
    } catch (error) {
      logger.warn('globalShortcut.register threw:', error)
    }
    if (!registered) {
      // restore the previous one if we can
      if (previous && previous !== acc) {
        this.registerShortcut(previous)
      }
      return { ok: false, error: '登録できませんでした（他のアプリとの重複の可能性）' }
    }
    this.currentShortcut = acc
    try {
      localStoreService.setSetting(SHORTCUT_KEY, acc)
    } catch (error) {
      logger.warn('persisting shortcut failed:', error)
    }
    logger.info(`declare shortcut set: ${acc}`)
    return { ok: true, accelerator: acc }
  }

  private registerShortcut(acc: string): void {
    try {
      if (globalShortcut.register(acc, () => this.toggle())) {
        this.currentShortcut = acc
      } else {
        logger.warn(`shortcut not registered (occupied?): ${acc}`)
      }
    } catch (error) {
      logger.warn(`registering shortcut ${acc} failed:`, error)
    }
  }

  toggle(): void {
    if (this.win && !this.win.isDestroyed() && this.win.isVisible()) {
      this.hide()
    } else {
      this.show()
    }
  }

  show(): void {
    try {
      if (!this.win || this.win.isDestroyed()) {
        this.win = this.createWindow()
      }
      this.positionNearCursor()
      this.win.show()
      this.win.focus()
      // pick up fresh state (other devices may have declared meanwhile)
      void workSessionService.refresh()
    } catch (error) {
      logger.error('failed to show declare window:', error)
    }
  }

  hide(): void {
    if (this.win && !this.win.isDestroyed()) {
      this.win.hide()
    }
  }

  destroy(): void {
    if (this.currentShortcut) {
      globalShortcut.unregister(this.currentShortcut)
      this.currentShortcut = null
    }
    if (this.win && !this.win.isDestroyed()) {
      this.win.destroy()
    }
    this.win = null
  }

  /** Open {server_url}/ui/ in the default browser. */
  openManageUi(): void {
    const { serverUrl } = workSessionService.getState()
    if (!serverUrl) {
      logger.warn('manage UI requested but server_url is unknown')
      return
    }
    void shell.openExternal(`${serverUrl.replace(/\/+$/, '')}/ui/`)
  }

  private pushState(state: unknown): void {
    if (this.win && !this.win.isDestroyed()) {
      try {
        this.win.webContents.send(IpcServerPushChannel.WorkSession_StateChanged, state)
      } catch (error) {
        logger.warn('pushing work session state failed:', error)
      }
    }
  }

  private createWindow(): BrowserWindow {
    const win = new BrowserWindow({
      width: 460,
      height: 360,
      useContentSize: true,
      show: false,
      frame: false,
      resizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      webPreferences: {
        preload: path.join(__dirname, '../preload/index.js'),
        sandbox: false
      }
    })
    // stronger always-on-top level (macOS mission control / windows topmost)
    win.setAlwaysOnTop(true, 'screen-saver')

    win.on('closed', () => {
      this.win = null
    })
    win.webContents.setWindowOpenHandler((details) => {
      void shell.openExternal(details.url)
      return { action: 'deny' }
    })

    if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
      void win.loadURL(`${process.env['ELECTRON_RENDERER_URL']}/declare.html`)
    } else {
      void win.loadFile(path.join(__dirname, '../renderer/declare.html'))
    }
    return win
  }

  /** Top-center of the work area of the display under the cursor. */
  private positionNearCursor(): void {
    if (!this.win || this.win.isDestroyed()) {
      return
    }
    try {
      const point = screen.getCursorScreenPoint()
      const display = screen.getDisplayNearestPoint(point)
      const { x, y, width } = display.workArea
      const [w] = this.win.getSize()
      this.win.setPosition(Math.round(x + (width - w) / 2), Math.round(y + 24), false)
    } catch (error) {
      this.win.center()
    }
  }
}

export const declareWindowService = new DeclareWindowService()
