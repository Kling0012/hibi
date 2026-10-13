// SPDX-License-Identifier: Apache-2.0

// Main-process UI text (tray menu, dialogs, work-session errors) in ja/en/zh.
// Fail-soft: reading or writing lang.json never throws, and tMain falls back
// to Japanese, then to the key itself, so a missing entry never breaks menus.

import fs from 'node:fs'
import path from 'node:path'
import { app } from 'electron'
import { getLogger } from '@shared/logger/main'

const logger = getLogger('MainI18n')

export type MainLang = 'ja' | 'en' | 'zh'

const dictionaries: Record<MainLang, Record<string, string>> = {
  ja: {
    'tray.statusRecording': '記録中',
    'tray.statusPaused': '停止中',
    'tray.showWindow': 'メインウィンドウを表示',
    'tray.pauseRecording': '記録を停止',
    'tray.resumeRecording': '記録を再開',
    'tray.quit': 'Hibi を終了',
    'dialog.openFile': 'ファイルを開く',
    'dialog.allFiles': 'すべてのファイル',
    'dialog.saveFile': 'ファイルを保存',
    'dialog.selectFolder': 'フォルダを選択',
    'ws.errBackend': 'ローカルバックエンドに接続できません',
    'ws.errNoTitle': '作業名を入力してください',
    'ws.errNotReady': '同期設定を取得できません（バックエンド起動待ち）',
    'ws.errDisabled': '同期サーバが無効です（設定で有効化してください）',
    'ws.errUnreachable': '同期サーバに接続できません',
    'ws.errNoActive': '作業中の宣言はありません'
  },
  en: {
    'tray.statusRecording': 'Recording',
    'tray.statusPaused': 'Paused',
    'tray.showWindow': 'Show main window',
    'tray.pauseRecording': 'Pause recording',
    'tray.resumeRecording': 'Resume recording',
    'tray.quit': 'Quit Hibi',
    'dialog.openFile': 'Open file',
    'dialog.allFiles': 'All files',
    'dialog.saveFile': 'Save file',
    'dialog.selectFolder': 'Select folder',
    'ws.errBackend': 'Cannot reach the local backend',
    'ws.errNoTitle': 'Enter a work title',
    'ws.errNotReady': 'Sync settings unavailable (backend starting)',
    'ws.errDisabled': 'The sync server is disabled (enable it in Settings)',
    'ws.errUnreachable': 'Cannot reach the sync server',
    'ws.errNoActive': 'No active declaration'
  },
  zh: {
    'tray.statusRecording': '记录中',
    'tray.statusPaused': '已暂停',
    'tray.showWindow': '显示主窗口',
    'tray.pauseRecording': '暂停记录',
    'tray.resumeRecording': '继续记录',
    'tray.quit': '退出 Hibi',
    'dialog.openFile': '打开文件',
    'dialog.allFiles': '所有文件',
    'dialog.saveFile': '保存文件',
    'dialog.selectFolder': '选择文件夹',
    'ws.errBackend': '无法连接本地后端',
    'ws.errNoTitle': '请输入工作名称',
    'ws.errNotReady': '无法获取同步设置（后端启动中）',
    'ws.errDisabled': '同步服务器已禁用（请在设置中启用）',
    'ws.errUnreachable': '无法连接同步服务器',
    'ws.errNoActive': '没有进行中的声明'
  }
}

let currentLang: MainLang = 'ja'

/** OS locale → app language (ja* → ja, zh* → zh, else en). */
export function resolveAutoMainLang(locale: string | undefined): MainLang {
  const v = (locale || '').toLowerCase()
  if (v.startsWith('ja')) return 'ja'
  if (v.startsWith('zh')) return 'zh'
  if (v.startsWith('en')) return 'en'
  return 'en'
}

function langFilePath(): string {
  return path.join(app.getPath('userData'), 'lang.json')
}

/** Reads userData/lang.json, falling back to the OS locale. Never throws. */
export function initMainLanguage(): void {
  let stored: unknown
  try {
    stored = JSON.parse(fs.readFileSync(langFilePath(), 'utf-8'))?.lang
  } catch {
    stored = undefined // no file (first run) or unreadable: use the OS locale
  }
  currentLang =
    stored === 'ja' || stored === 'en' || stored === 'zh' ? stored : resolveAutoMainLang(app.getLocale())
  logger.info(`main language: ${currentLang}`)
}

export function getMainLanguage(): MainLang {
  return currentLang
}

export function setMainLanguage(lang: MainLang): void {
  currentLang = lang
  try {
    fs.mkdirSync(path.dirname(langFilePath()), { recursive: true })
    fs.writeFileSync(langFilePath(), JSON.stringify({ lang }, null, 2))
  } catch (error) {
    logger.warn('saving lang.json failed:', error)
  }
}

export function tMain(key: string): string {
  return dictionaries[currentLang]?.[key] ?? dictionaries.ja[key] ?? key
}
