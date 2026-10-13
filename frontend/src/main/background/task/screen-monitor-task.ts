import { ScreenSettings } from './../../../renderer/src/store/setting';
import { CaptureSource } from '@interface/common/source'
import { IpcServerPushChannel } from '@shared/ipc-server-push-channel'
import { BrowserWindow, ipcMain } from 'electron'
import { pick } from 'lodash'
import liveCaptureService from '../../services/LiveCaptureService'
import { getLogger } from '@shared/logger/main'
import { IpcChannel } from '@shared/IpcChannel'
import { powerWatcher } from '../os/Power'
import dayjs from 'dayjs'
import isBetween from 'dayjs/plugin/isBetween'
import customParseFormat from 'dayjs/plugin/customParseFormat'
import isSameOrAfter from 'dayjs/plugin/isSameOrAfter'
import isSameOrBefore from 'dayjs/plugin/isSameOrBefore'

dayjs.extend(isBetween)
dayjs.extend(customParseFormat)
dayjs.extend(isSameOrAfter)
dayjs.extend(isSameOrBefore)

const logger = getLogger('ScreenMonitorTask')

// Screen recording now consists of the live capture service only
// (frames streamed to /api/live/frame). The legacy screenshot pipeline
// (periodic screenshots uploaded to /api/add_screenshot for VLM analysis)
// was removed; do not add it back here.
class ScreenMonitorTask {
  static globalStatus: 'running' | 'stopped' = 'stopped'
  private status: 'running' | 'stopped' = 'stopped'
  private modelConfig: Partial<ScreenSettings> = {}

  constructor() {}

  public async init() {
    this.listenToScreenMonitorEvents()
    logger.info('ScreenMonitorTask initialized')
  }
  private listenToScreenMonitorEvents() {
    ipcMain.handle(IpcChannel.Task_Update_Current_Record_App, (_, appInfo: CaptureSource[]) => {
      logger.info(
        'ScreenMonitorTask updateCurrentRecordApp -->',
        appInfo.map((v) => pick(v, ['name', 'type']))
      )
    })
    ipcMain.handle(IpcChannel.Task_Update_Model_Config, (_, config: ScreenSettings) => {
      this.modelConfig = config
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
    liveCaptureService.start().catch((e) => logger.warn('live capture failed to start:', e))
    this.status = 'running'
    this.broadcastStatus()
  }
  private stopTask() {
    if (this.status === 'stopped') {
      return
    }
    logger.info('ScreenMonitorTask stopTask')
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
    this.status = 'stopped'

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
      const [startTimeStr, endTimeStr] = recordingHours as [string, string] // e.g., ["09:00", "18:00"]

      const start = dayjs(startTimeStr, 'HH:mm')
      const end = dayjs(endTimeStr, 'HH:mm')

      if (!start.isValid() || !end.isValid()) {
        logger.warn(`invalid record time format: ${startTimeStr}-${endTimeStr}。skip check`)
        return true
      }

      if (start.isAfter(end)) {
        return now.isSameOrAfter(start) || now.isSameOrBefore(end)
      } else {
        return now.isBetween(start, end, null, '[]')
      }
    }
    return true
  }
}
export { ScreenMonitorTask }
