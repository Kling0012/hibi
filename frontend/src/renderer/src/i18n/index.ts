// SPDX-License-Identifier: Apache-2.0

// Renderer i18n (ja / en / zh). The preference lives in
// localStorage["hibi.lang"]: 'auto' (default, follows navigator.language),
// 'ja', 'en' or 'zh'. Fail-soft: if storage or IPC fails the app keeps
// running in Japanese, and missing keys fall back to Japanese.

import i18next from 'i18next'
import { initReactI18next } from 'react-i18next'
import { IpcChannel } from '@shared/IpcChannel'
import ja from './ja.json'
import en from './en.json'
import zh from './zh.json'

export type AppLang = 'ja' | 'en' | 'zh'
export type LangPref = 'auto' | AppLang

export const LANG_STORAGE_KEY = 'hibi.lang'

export function normalizeLang(v: unknown): AppLang {
  return v === 'en' || v === 'zh' ? v : 'ja'
}

/** navigator.language → app language (ja* → ja, zh* → zh, else en). */
export function resolveAutoLang(): AppLang {
  const candidates =
    typeof navigator !== 'undefined' ? (navigator.languages ?? [navigator.language]) : []
  for (const lang of candidates) {
    const v = (lang || '').toLowerCase()
    if (v.startsWith('ja')) return 'ja'
    if (v.startsWith('zh')) return 'zh'
    if (v.startsWith('en')) return 'en'
  }
  return 'en'
}

export function getLangPref(): LangPref {
  try {
    const v = localStorage.getItem(LANG_STORAGE_KEY)
    return v === 'ja' || v === 'en' || v === 'zh' || v === 'auto' ? v : 'auto'
  } catch {
    return 'auto'
  }
}

export function getResolvedLang(): AppLang {
  const pref = getLangPref()
  return pref === 'auto' ? resolveAutoLang() : pref
}

/** Tells the main process (tray / dialogs) which language to use. Fail-soft. */
function syncMainLanguage(lang: AppLang): void {
  try {
    void window.electron?.ipcRenderer?.invoke(IpcChannel.App_SetLanguage, lang)
  } catch {
    // not running inside Electron (tests) — nothing to notify
  }
}

/** Stores the preference, switches the renderer immediately and informs main. */
export function applyLangPref(pref: LangPref): void {
  try {
    localStorage.setItem(LANG_STORAGE_KEY, pref)
  } catch {
    // storage unavailable: still switch the UI below
  }
  const resolved = pref === 'auto' ? resolveAutoLang() : pref
  void i18next.changeLanguage(resolved)
  syncMainLanguage(resolved)
}

void i18next.use(initReactI18next).init({
  resources: {
    ja: { translation: ja },
    en: { translation: en },
    zh: { translation: zh }
  },
  lng: getResolvedLang(),
  fallbackLng: 'ja',
  interpolation: { escapeValue: false },
  returnEmptyString: false,
  react: { useSuspense: false }
})

// Keep the tray menu and main-process dialogs in the same language on startup.
syncMainLanguage(getResolvedLang())

export default i18next
