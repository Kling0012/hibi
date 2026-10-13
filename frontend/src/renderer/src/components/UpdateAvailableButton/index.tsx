import rocketSvg from '@renderer/assets/icons/rocket.svg'
import { useSetting } from '@renderer/hooks/use-setting'
import { IpcChannel } from '@shared/IpcChannel'
import { UpdateInfo } from 'electron-updater'
import { debounce } from 'lodash'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

// First check 30s after startup, then every 6 hours.
const CHECK_UPDATE_DELAY_MS = 30 * 1000
const CHECK_UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000
const RELEASE_PAGE_URL_PREFIX = 'https://github.com/Kling0012/hibi/releases/tag/v'

export default function UpdateAvailableButton() {
  const { t } = useTranslation()
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [isDownloaded, setIsDownloaded] = useState(false)
  const { autoCheckUpdate } = useSetting()
  const isWindows = window.api.platform === 'win32'

  useEffect(() => {
    // Follows the "check for updates automatically" setting. Fail-soft:
    // network errors and missing releases are logged and do nothing else.
    if (!autoCheckUpdate) return
    const check = async () => {
      try {
        const { updateInfo: _updateInfo } = await window.api.checkForUpdate()
        if (_updateInfo) setUpdateInfo(_updateInfo)
      } catch (err) {
        console.error('checkForUpdate failed', err)
      }
    }
    const timeout = setTimeout(check, CHECK_UPDATE_DELAY_MS)
    const interval = setInterval(check, CHECK_UPDATE_INTERVAL_MS)
    return () => {
      clearTimeout(timeout)
      clearInterval(interval)
    }
  }, [autoCheckUpdate])

  useEffect(() => {
    if (!window.electron) return

    const ipcRenderer = window.electron.ipcRenderer

    const f1 = ipcRenderer.on(IpcChannel.UpdateDownloaded, () => {
      console.log('UpdateDownloaded')
      setIsDownloaded(true)
    })

    const f2 = ipcRenderer.on(IpcChannel.UpdateError, (_, err) => {
      console.error('UpdateError', err)
    })
    return () => {
      f1()
      f2()
    }
  }, [])

  // Windows: download finished, restart to install. macOS (unsigned build):
  // open the release page of the new version in the default browser.
  const handleClick = debounce(
    () => {
      if (isWindows) {
        window.api.quitAndInstall()
      } else if (updateInfo) {
        window.api.openWebsite(`${RELEASE_PAGE_URL_PREFIX}${updateInfo.version}`)
      }
    },
    2000,
    { leading: true, trailing: false }
  )

  // Windows shows the button after the download; macOS as soon as an update is found.
  if (!updateInfo || (isWindows && !isDownloaded)) return null
  return (
    <div
      className="relative w-full flex items-center bg-[#FFFEF9] justify-center rounded-[6px] px-2 py-1.5 bg-gradient-to-tr hover:from-[rgba(246,236,255,0.43)] to-[#FFFEF9] transition cursor-pointer"
      onClick={handleClick}>
      <img src={rocketSvg} className="w-3 h-3" />
      <span className="text-[#3F3F51] text-[12px] ml-1">
        {isWindows ? t('update.restartToUpdatePrefix') : `${t('update.newVersion')} `}
        <span className="font-['Roboto'] text-[10px] text-[#AEAFC2]">v{updateInfo.version}</span>
        {isWindows ? '）' : ` ${t('update.hasNewVersion')}`}
      </span>

      <span className="w-1.5 h-1.5 bg-[#5252FF] rounded-full absolute top-[-1px] right-[-1px]"></span>
    </div>
  )
}
