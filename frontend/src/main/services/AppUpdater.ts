import { IpcChannel } from '@shared/IpcChannel'
import { getLogger } from '@shared/logger/main'
import { isPrereleaseVersion } from '@shared/version'
import { app, BrowserWindow } from 'electron'
import {
  AppUpdater as _AppUpdater,
  autoUpdater,
  CancellationToken,
  UpdateCheckResult,
  UpdateInfo
} from 'electron-updater'
// import { isDev } from '@main/constant'
// import path from 'path'
const logger = getLogger('AppUpdater')

// The Mac build is unsigned, so Squirrel.Mac cannot auto-install. Windows uses
// electron-updater to download and install on restart.
const isWindows = process.platform === 'win32'

export default class AppUpdater {
  autoUpdater: _AppUpdater = autoUpdater
  private updateCheckResult: UpdateCheckResult | null = null
  private cancellationToken: CancellationToken = new CancellationToken()
  constructor(mainWindow: BrowserWindow) {
    autoUpdater.logger = logger
    // for dev test
    autoUpdater.forceDevUpdateConfig = !app.isPackaged

    // if (isDev) {
    //     const devConfigPath = path.join(process.cwd(), 'dev-app-update.yml')
    //     autoUpdater.updateConfigPath = devConfigPath
    //   // updateConfig
    //   logger.info('use dev update config2',  autoUpdater.updateConfigPath)
    // }

    // Pre-release builds (e.g. 0.1.0-dev.3) also pick up pre-release updates.
    autoUpdater.allowPrerelease = isPrereleaseVersion(app.getVersion())
    // Windows downloads in the background; the Mac build only notifies.
    autoUpdater.autoDownload = isWindows
    autoUpdater.autoInstallOnAppQuit = false

    autoUpdater.on('checking-for-update', () => {
      logger.info('Checking for update...')
    })
    autoUpdater.on('update-available', (info: UpdateInfo) => {
      logger.info('Update available', info)
      mainWindow.webContents.send(IpcChannel.UpdateAvailable, info)
    })
    autoUpdater.on('update-not-available', () => {
      logger.info('Update not available.')
      mainWindow.webContents.send(IpcChannel.UpdateNotAvailable)
    })
    autoUpdater.on('error', (err) => {
      logger.info('Update error.', err)
      mainWindow.webContents.send(IpcChannel.UpdateError, err)
    })
    autoUpdater.on('update-downloaded', (info: UpdateInfo) => {
      logger.info('update downloaded', info)
      mainWindow.webContents.send(IpcChannel.UpdateDownloaded, info)
    })
  }
  public async checkForUpdates() {
    try {
      this.updateCheckResult = await this.autoUpdater.checkForUpdates()
      logger.info(
        `update check result: ${this.updateCheckResult?.isUpdateAvailable}, channel: ${this.autoUpdater.channel}, currentVersion: ${this.autoUpdater.currentVersion}`
      )
      return {
        currentVersion: this.autoUpdater.currentVersion,
        updateInfo: this.updateCheckResult?.isUpdateAvailable ? this.updateCheckResult?.updateInfo : null
      }
    } catch (error) {
      // Fail-soft: a missing release or a network failure only skips the
      // update notice, the rest of the app keeps working.
      logger.error('Failed to check for update:', error as Error)
      return {
        currentVersion: app.getVersion(),
        updateInfo: null
      }
    }
  }
  public downloadUpdate() {
    this.autoUpdater.downloadUpdate()
  }

  public quitAndInstall() {
    setImmediate(() => autoUpdater.quitAndInstall())
  }

  public cancelDownload() {
    this.cancellationToken.cancel()
    this.cancellationToken = new CancellationToken()
    if (this.autoUpdater.autoDownload) {
      this.updateCheckResult?.cancellationToken?.cancel()
    }
  }
}
