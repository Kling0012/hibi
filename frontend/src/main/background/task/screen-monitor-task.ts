import { CaptureSource } from '@interface/common/source'
import { IpcServerPushChannel } from '@shared/ipc-server-push-channel'
import { IpcChannel } from '@shared/IpcChannel'
import { getLogger } from '@shared/logger/main'
import dayjs from 'dayjs'
import customParseFormat from 'dayjs/plugin/customParseFormat'
import isBetween from 'dayjs/plugin/isBetween'
import isSameOrAfter from 'dayjs/plugin/isSameOrAfter'
import { BrowserWindow, ipcMain } from 'electron'
import { pick } from 'lodash'

import liveCaptureService from '../../services/LiveCaptureService'
import { powerWatcher } from '../os/Power'
import { ScreenSettings } from './../../../renderer/src/store/setting'

dayjs.extend(isBetween)
dayjs.extend(customParseFormat)
dayjs.extend(isSameOrAfter)

const logger = getLogger('ScreenMonitorTask')

// Screen recording now consists of the live capture service only
// (frames streamed to /api/live/frame). The legacy screenshot pipeline
// (periodic screenshots uploaded to /api/add_screenshot for VLM analysis)
// was removed; do not add it back here.
class ScreenMonitorTask {
  static globalStatus: 'running' | 'stopped' = 'stopped'
  private status: 'running' | 'stopped' = 'stopped'
  private modelConfig: Partial<ScreenSettings> = {}
  private policyTimer: NodeJS.Timeout | null = null

  public async init() {
    liveCaptureService.setRecordingPolicy(this.checkCanRecord)
    this.listenToScreenMonitorEvents()
    logger.info('ScreenMonitorTask initialized')
  }
  private listenToScreenMonitorEvents() {
    ipcMain.handle(IpcChannel.Task_Update_Current_Record_App, (_, appInfo: CaptureSource[]) => {
      logger.info(
        'ScreenMonitorTask updateCurrentRecordApp -->',
        appInfo.map((v) => pick(v, ['name', 'type']))
      )
      liveCaptureService.setSources(appInfo)
    })
    ipcMain.handle(IpcChannel.Task_Update_Model_Config, (_, config: ScreenSettings) => {
      this.modelConfig = config
      liveCaptureService.setRecordingPolicy(this.checkCanRecord)
      if (this.status === 'running') this.syncRecordingPolicy()
    })
    ipcMain.handle(IpcChannel.Task_Start, () => {
      logger.info('render notify ScreenMonitorTask start')
      ScreenMonitorTask.globalStatus = 'running'
      this.startTask()
    })
    ipcMain.handle(IpcChannel.Task_Stop, () => {
      logger.info('render notify ScreenMonitorTask stop')
      ScreenMonitorTask.globalStatus = 'stopped'
      this.stopTask()
    })
    ipcMain.handle(IpcChannel.Task_Check_Can_Record, () => {
      return {
        canRecord: this.checkCanRecord(),
        status: this.status
      }
    })
    powerWatcher.registerResumeCallback(() => {
      logger.info('ScreenMonitorTask resume')
      if (ScreenMonitorTask.globalStatus === 'running') {
        this.startTask()
      }
    })
    powerWatcher.registerSuspendCallback(() => {
      logger.info('ScreenMonitorTask suspend')
      this.stopTask()
    })
    powerWatcher.registerLockScreenCallback(() => {
      logger.info('ScreenMonitorTask lock-screen', ScreenMonitorTask.globalStatus)
      this.stopTask()
    })
    powerWatcher.registerUnlockScreenCallback(() => {
      logger.info('ScreenMonitorTask unlock-screen', ScreenMonitorTask.globalStatus)
      if (ScreenMonitorTask.globalStatus === 'running') {
        this.startTask()
      }
    })
  }
  private async startTask() {
    if (this.status === 'running') {
      return
    }

    logger.info('ScreenMonitorTask startTask')
    this.status = 'running'
    this.syncRecordingPolicy()
    this.policyTimer = setInterval(() => this.syncRecordingPolicy(), 1000)
    this.broadcastStatus()
  }

  private syncRecordingPolicy() {
    if (this.checkCanRecord()) {
      liveCaptureService.start().catch((e) => logger.warn('live capture failed to start:', e))
    } else {
      liveCaptureService.stop()
    }
  }

  private stopTask() {
    if (this.status === 'stopped') {
      return
    }
    logger.info('ScreenMonitorTask stopTask')
    if (this.policyTimer) {
      clearInterval(this.policyTimer)
      this.policyTimer = null
    }
    liveCaptureService.stop()
    this.status = 'stopped'
    this.broadcastStatus()
  }

  private broadcastStatus() {
    BrowserWindow.getAllWindows().forEach((window) => {
      window.webContents.send(IpcServerPushChannel.PushScreenMonitorStatus, this.status)
    })
  }

  public unregister() {
    this.stopTask()

    ipcMain.removeHandler(IpcChannel.Task_Check_Can_Record)
    ipcMain.removeHandler(IpcChannel.Task_Update_Model_Config)
    ipcMain.removeHandler(IpcChannel.Task_Start)
    ipcMain.removeHandler(IpcChannel.Task_Stop)
    ipcMain.removeHandler(IpcChannel.Task_Update_Current_Record_App)
  }

  private checkCanRecord = () => {
    const { enableRecordingHours, applyToDays, recordingHours } = this.modelConfig

    if (!enableRecordingHours) {
      return true
    }

    const now = dayjs()

    if (applyToDays === 'weekday') {
      const currentDay = now.day()
      if (currentDay === 0 || currentDay === 6) {
        return false
      }
    }

    if (recordingHours && Array.isArray(recordingHours) && recordingHours.length === 2) {
      const [startTimeStr, endTimeStr] = recordingHours as [string, string]

      const start = dayjs(startTimeStr, ['HH:mm:ss', 'HH:mm'], true)
      const end = dayjs(endTimeStr, ['HH:mm:ss', 'HH:mm'], true)

      if (!start.isValid() || !end.isValid()) {
        logger.warn(`invalid record time format: ${startTimeStr}-${endTimeStr}; recording paused`)
        return false
      }

      if (start.isAfter(end)) {
        return now.isSameOrAfter(start) || now.isBefore(end)
      } else {
        return now.isBetween(start, end, null, '[)')
      }
    }
    return false
  }
}
export { ScreenMonitorTask }
