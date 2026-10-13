// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import { FC, useCallback, useEffect, useState } from 'react'
import { Button, Input, InputNumber, Message, Select, Spin, Switch, Tabs, Typography } from '@arco-design/web-react'
import { get } from 'lodash'
import { useTranslation } from 'react-i18next'
import { applyLangPref, getLangPref, type LangPref } from '@renderer/i18n'

import {
  AIProviderConfig,
  AIRoutes,
  AISettingsPayload,
  CaptureSettings,
  SyncSettings,
  SyncStatus,
  getAISettings,
  getGeneralSettings,
  getSyncSettings,
  testAIProvider,
  updateAISettings,
  updateCaptureSettings,
  updateSyncSettings
} from '../../services/Settings'
import { useSetting } from '@renderer/hooks/use-setting'

const { Text } = Typography

const ROUTE_KEYS = ['vision', 'chat', 'structured', 'embedding', 'exact'] as const
const ROUTE_LABEL_KEYS: Record<string, string> = {
  vision: 'settings.routeVision',
  chat: 'settings.routeChat',
  structured: 'settings.routeStructured',
  embedding: 'settings.routeEmbedding',
  exact: 'settings.routeExact'
}
const KIND_OPTIONS = [
  { value: 'openai_compat', label: 'openai_compat' },
  { value: 'gemini_live', label: 'gemini_live' }
]
const LANGUAGE_OPTIONS = [
  { value: 'ja', label: '日本語' },
  { value: 'en', label: 'English' },
  { value: 'zh', label: '中文' }
]

interface SettingsProps {
  closeSetting?: () => void
  init?: boolean
}

const emptyProvider = (n: number): AIProviderConfig => ({
  id: `provider_${n}`,
  kind: 'openai_compat',
  model: '',
  base_url: '',
  api_key_file: '',
  api_key: ''
})

const Settings: FC<SettingsProps> = (props) => {
  const { closeSetting, init } = props
  const { autoCheckUpdate, setGeneralSettings } = useSetting()
  const { t } = useTranslation()
  const [langPref, setLangPref] = useState<LangPref>(getLangPref())

  const changeLangPref = (pref: LangPref): void => {
    setLangPref(pref)
    applyLangPref(pref)
  }

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [providers, setProviders] = useState<AIProviderConfig[]>([])
  const [routes, setRoutes] = useState<AIRoutes>({})
  const [outputLanguage, setOutputLanguage] = useState('ja')
  const [testing, setTesting] = useState<Record<string, boolean>>({})
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message: string }>>({})
  const [capture, setCapture] = useState<CaptureSettings>({})
  const [excludeAppsText, setExcludeAppsText] = useState('')
  const [captureSaving, setCaptureSaving] = useState(false)
  const [sync, setSync] = useState<SyncSettings>({})
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [syncSaving, setSyncSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getAISettings()
      const ai = get(data, 'ai')
      if (ai) {
        setProviders(ai.providers || [])
        setRoutes(ai.routes || {})
        setOutputLanguage(ai.output_language || 'ja')
      }
    } catch (e) {
      Message.error(get(e, 'message', t('settings.loadFailed')))
    } finally {
      setLoading(false)
    }
    try {
      const general = await getGeneralSettings()
      const captureData = get(general, 'capture') || {}
      setCapture(captureData)
      setExcludeAppsText(((captureData.exclude_apps as string[]) || []).join('\n'))
    } catch {
      // capture settings are optional (fail-soft)
    }
    try {
      const syncData = await getSyncSettings()
      if (syncData) {
        setSync(syncData.sync || {})
        setSyncStatus(syncData.status || null)
      }
    } catch {
      // sync settings are optional (fail-soft)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const updateProvider = (index: number, patch: Partial<AIProviderConfig>) => {
    setProviders((prev) => prev.map((p, i) => (i === index ? { ...p, ...patch } : p)))
  }

  const removeProvider = (id: string) => {
    setProviders((prev) => prev.filter((p) => p.id !== id))
    setRoutes((prev) => {
      const next: AIRoutes = {}
      for (const key of ROUTE_KEYS) {
        next[key] = (prev[key] || []).filter((pid) => pid !== id)
      }
      return next
    })
  }

  const moveInRoute = (route: string, index: number, dir: -1 | 1) => {
    setRoutes((prev) => {
      const list = [...(prev[route] || [])]
      const target = index + dir
      if (target < 0 || target >= list.length) return prev
      ;[list[index], list[target]] = [list[target], list[index]]
      return { ...prev, [route]: list }
    })
  }

  const toggleInRoute = (route: string, id: string) => {
    setRoutes((prev) => {
      const list = prev[route] || []
      const exists = list.includes(id)
      return { ...prev, [route]: exists ? list.filter((x) => x !== id) : [...list, id] }
    })
  }

  const test = async (provider: AIProviderConfig) => {
    setTesting((prev) => ({ ...prev, [provider.id]: true }))
    try {
      const result = await testAIProvider(provider)
      setTestResults((prev) => ({
        ...prev,
        [provider.id]: result || { ok: false, message: t('settings.noResponse') }
      }))
    } catch (e) {
      setTestResults((prev) => ({ ...prev, [provider.id]: { ok: false, message: get(e, 'message', 'failed') } }))
    } finally {
      setTesting((prev) => ({ ...prev, [provider.id]: false }))
    }
  }

  const save = async () => {
    const ids = providers.map((p) => p.id.trim()).filter(Boolean)
    if (providers.length === 0 || new Set(ids).size !== ids.length) {
      Message.error(t('settings.duplicateId'))
      return
    }
    setSaving(true)
    try {
      const payload: AISettingsPayload = {
        providers: providers.map((p) => ({
          ...p,
          id: p.id.trim(),
          api_key: p.api_key || undefined
        })),
        routes,
        output_language: outputLanguage
      }
      await updateAISettings(payload)
      Message.success(t('settings.aiSaved'))
      await load()
      if (init) closeSetting?.()
    } catch (e) {
      Message.error(get(e, 'response.data.message') || get(e, 'message') || t('settings.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const saveCapture = async () => {
    setCaptureSaving(true)
    try {
      await updateCaptureSettings({
        ...capture,
        exclude_apps: excludeAppsText
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean)
      })
      Message.success(t('settings.captureSaved'))
    } catch (e) {
      Message.error(get(e, 'response.data.message') || get(e, 'message') || t('settings.saveFailed'))
    } finally {
      setCaptureSaving(false)
    }
  }

  const saveSync = async () => {
    setSyncSaving(true)
    try {
      await updateSyncSettings(sync)
      const syncData = await getSyncSettings()
      if (syncData) {
        setSync(syncData.sync || {})
        setSyncStatus(syncData.status || null)
      }
      Message.success(t('settings.syncSaved'))
    } catch (e) {
      Message.error(get(e, 'response.data.message') || get(e, 'message') || t('settings.saveFailed'))
    } finally {
      setSyncSaving(false)
    }
  }

  return (
    <Spin loading={loading} block className="[&_.arco-spin-children]:!h-full !h-full">
      <div className={`hb-page hb-settings${init ? ' hb-settings-init' : ''}`} style={{ height: '100%' }}>
        <div className="hb-page-head">
          <h1>{init ? t('loading.welcome') : t('nav.settings')}</h1>
          {init && <span className="hb-sub">{t('settings.getStartedSub')}</span>}
        </div>
        <div className="hb-scroll">
          <Tabs defaultActiveTab="ai" className="hb-tabs">
          <Tabs.TabPane key="ai" title="AI">
          <div className="mb-[12px]">
            <Text type="secondary" className="text-[13px]">
              {t('settings.aiDesc')}
            </Text>
          </div>

          {/* Providers */}
          <div className="flex flex-col gap-4 mb-6">
            {providers.map((provider, index) => (
              <div key={index} className="border border-[#E5E6EB] rounded-[8px] p-3 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <Input
                    value={provider.id}
                    onChange={(v) => updateProvider(index, { id: v })}
                    placeholder={t('settings.providerIdPlaceholder')}
                    className="!w-[160px]"
                  />
                  <Select
                    value={provider.kind}
                    options={KIND_OPTIONS}
                    onChange={(v) => updateProvider(index, { kind: v })}
                    className="!w-[170px]"
                  />
                  <Input
                    value={provider.model}
                    onChange={(v) => updateProvider(index, { model: v })}
                    placeholder={t('settings.modelPlaceholder')}
                    className="flex-1"
                  />
                  <Button size="small" onClick={() => test(provider)} loading={testing[provider.id]}>
                    {t('settings.testConnection')}
                  </Button>
                  <Button size="small" status="danger" onClick={() => removeProvider(provider.id)}>
                    {t('common.delete')}
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  {provider.kind !== 'gemini_live' &&
                    (provider.options?.base_url_from_sync ? (
                      <Text type="secondary" className="flex-1 text-[13px] leading-[32px]">
                        {t('settings.usesSyncUrl')}
                      </Text>
                    ) : (
                      <Input
                        value={provider.base_url || ''}
                        onChange={(v) => updateProvider(index, { base_url: v })}
                        placeholder={t('settings.baseUrlPlaceholder')}
                        className="flex-1"
                      />
                    ))}
                  <Input
                    value={provider.api_key_file || ''}
                    onChange={(v) => updateProvider(index, { api_key_file: v })}
                    placeholder={t('settings.apiKeyFilePlaceholder')}
                    className="flex-1"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <Input.Password
                    value={provider.api_key || ''}
                    onChange={(v) => updateProvider(index, { api_key: v })}
                    placeholder={
                      provider.has_api_key ? t('settings.apiKeySavedPlaceholder') : t('settings.apiKeyPlaceholder')
                    }
                    className="flex-1"
                    defaultVisibility={false}
                  />
                </div>
                {testResults[provider.id] && (
                  <Text type={testResults[provider.id].ok ? 'success' : 'error'} className="text-[12px]">
                    {testResults[provider.id].ok ? t('common.ok') : t('settings.testFailed')}:{' '}
                    {testResults[provider.id].message}
                  </Text>
                )}
              </div>
            ))}
            <Button
              className="!w-[160px]"
              onClick={() => setProviders((prev) => [...prev, emptyProvider(prev.length + 1)])}
            >
              {t('settings.addProvider')}
            </Button>
          </div>

          {/* Routes */}
          <div className="mb-[12px] text-[16px] font-medium text-[#0B0B0F]">{t('settings.routeOrder')}</div>
          <div className="flex flex-col gap-4 mb-6">
            {ROUTE_KEYS.map((route) => (
              <div key={route} className="flex flex-col gap-1">
                <Text className="text-[14px] font-medium">{t(ROUTE_LABEL_KEYS[route])}</Text>
                <div className="flex flex-col gap-1">
                  {(routes[route] || []).map((pid, i) => (
                    <div key={pid} className="flex items-center gap-2">
                      <span className="text-[13px] w-[24px] text-[#6E718C]">{i + 1}.</span>
                      <span className="text-[13px]">{pid}</span>
                      <Button size="mini" disabled={i === 0} onClick={() => moveInRoute(route, i, -1)}>
                        ↑
                      </Button>
                      <Button
                        size="mini"
                        disabled={i === (routes[route] || []).length - 1}
                        onClick={() => moveInRoute(route, i, 1)}
                      >
                        ↓
                      </Button>
                      <Button size="mini" status="danger" onClick={() => toggleInRoute(route, pid)}>
                        x
                      </Button>
                    </div>
                  ))}
                  {(routes[route] || []).length === 0 && (
                    <Text type="secondary" className="text-[12px]">
                      {t('settings.routeNotSet')}
                    </Text>
                  )}
                </div>
                <Select
                  placeholder={t('settings.routeAddPlaceholder')}
                  value={undefined as unknown as string}
                  options={providers
                    .filter((p) => !(routes[route] || []).includes(p.id))
                    .map((p) => ({ value: p.id, label: p.id }))}
                  onChange={(v) => v && toggleInRoute(route, v)}
                  className="!w-[280px]"
                />
              </div>
            ))}
          </div>

          {/* Output language */}
          <div className="mb-6 flex items-center gap-3">
            <Text className="text-[14px] font-medium">{t('settings.outputLanguage')}</Text>
            <Select
              value={outputLanguage}
              options={LANGUAGE_OPTIONS}
              onChange={setOutputLanguage}
              className="!w-[160px]"
            />
            <Text type="secondary" className="text-[12px]">
              {t('settings.outputLanguageDesc')}
            </Text>
          </div>

          <Spin loading={saving}>
            <Button type="primary" onClick={save} disabled={saving}>
              {init ? t('settings.getStarted') : t('settings.saveAi')}
            </Button>
          </Spin>
          </Tabs.TabPane>

          {/* Screen recording (P1-B) */}
          <Tabs.TabPane key="capture" title={t('settings.captureTab')}>
          <div>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.excludeLabel')}</Text>
                <Switch
                  size="small"
                  checked={!!capture.paused}
                  onChange={(v) => setCapture((prev) => ({ ...prev, paused: v }))}
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.excludeHint')}
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.liveFps')}</Text>
                <InputNumber
                  size="small"
                  min={0.2}
                  max={1}
                  step={0.1}
                  value={capture.live_fps ?? 1}
                  onChange={(v) => setCapture((prev) => ({ ...prev, live_fps: v }))}
                  className="!w-[120px]"
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.liveFpsDesc')}
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.liveSummaryInterval')}</Text>
                <InputNumber
                  size="small"
                  min={5}
                  max={600}
                  step={5}
                  value={capture.live_summary_interval ?? 30}
                  onChange={(v) => setCapture((prev) => ({ ...prev, live_summary_interval: v }))}
                  className="!w-[120px]"
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.liveSummaryIntervalDesc')}
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.minInterval')}</Text>
                <InputNumber
                  size="small"
                  min={5}
                  max={600}
                  step={5}
                  value={capture.capture_min_interval ?? 20}
                  onChange={(v) => setCapture((prev) => ({ ...prev, capture_min_interval: v }))}
                  className="!w-[120px]"
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.minIntervalDesc')}
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.threshold')}</Text>
                <InputNumber
                  size="small"
                  min={1}
                  max={64}
                  step={1}
                  value={capture.change_hash_threshold ?? 10}
                  onChange={(v) => setCapture((prev) => ({ ...prev, change_hash_threshold: v }))}
                  className="!w-[120px]"
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.thresholdDesc')}
                </Text>
              </div>
              <div className="flex flex-col gap-1">
                <Text className="text-[14px]">{t('settings.excludeApps')}</Text>
                <Input.TextArea
                  value={excludeAppsText}
                  onChange={(v) => setExcludeAppsText(v)}
                  placeholder={'1Password\nBitwarden\nKeePassXC'}
                  autoSize={{ minRows: 3, maxRows: 6 }}
                  className="!w-[400px]"
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.excludeAppsDesc')}
                </Text>
              </div>
              <Button className="!w-[120px]" loading={captureSaving} onClick={saveCapture}>
                {t('common.save')}
              </Button>
            </div>
          </div>

          </Tabs.TabPane>

          {/* Sync server (P1-B) */}
          <Tabs.TabPane key="sync" title={t('settings.syncTab')}>
          <div>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.syncSendLabel')}</Text>
                <Switch
                  size="small"
                  checked={!!sync.enabled}
                  onChange={(v) => setSync((prev) => ({ ...prev, enabled: v }))}
                />
                <Text type="secondary" className="text-[12px]">
                  {t('settings.syncSendDesc')}
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.syncUrlLabel')}</Text>
                <Input
                  value={sync.server_url || ''}
                  onChange={(v) => setSync((prev) => ({ ...prev, server_url: v }))}
                  placeholder="http://host:18740"
                  className="!w-[340px]"
                  size="small"
                />
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">{t('settings.syncDeviceLabel')}</Text>
                <Input
                  value={sync.device_name || ''}
                  onChange={(v) => setSync((prev) => ({ ...prev, device_name: v }))}
                  placeholder={t('settings.syncDevicePlaceholder')}
                  className="!w-[340px]"
                  size="small"
                />
              </div>
              {syncStatus && (
                <div className="flex flex-col gap-1 text-[12px] text-[#6E718C]">
                  <span>
                    {t('settings.syncDeviceId')}: {syncStatus.device_id || t('settings.syncDeviceIdNone')}
                  </span>
                  <span>
                    {t('settings.syncLastSent')}: {syncStatus.last_sync_at || t('settings.syncLastSentNone')}
                  </span>
                  <span>{t('settings.syncPendingLine', { n: syncStatus.pending_count ?? '—' })}</span>
                  {syncStatus.last_error && (
                    <Text type="error">
                      {t('settings.syncLastError')}: {syncStatus.last_error}
                    </Text>
                  )}
                </div>
              )}
              <Button className="!w-[120px]" loading={syncSaving} onClick={saveSync}>
                {t('common.save')}
              </Button>
            </div>
          </div>

          </Tabs.TabPane>

          <Tabs.TabPane key="app" title={t('settings.appTab')}>
          <div>
            <div className="mb-[4px] flex items-center justify-between">
              <span className="text-[14px] text-[#0B0B0F]">{t('settings.updateAutoCheck')}</span>
              <Switch
                size="small"
                checked={autoCheckUpdate}
                onChange={(v) => setGeneralSettings({ autoCheckUpdate: v })}
              />
            </div>
            <Text type="secondary" className="text-[12px]">
              {t('settings.updateAutoCheckDesc')}
            </Text>
            <div className="mt-4 mb-[4px] flex items-center justify-between">
              <span className="text-[14px] text-[#0B0B0F]">{t('settings.language')}</span>
              <Select
                value={langPref}
                options={[
                  { value: 'auto', label: t('settings.langAuto') },
                  { value: 'ja', label: '日本語' },
                  { value: 'en', label: 'English' },
                  { value: 'zh', label: '中文' }
                ]}
                onChange={changeLangPref}
                className="!w-[160px]"
                size="small"
              />
            </div>
            <Text type="secondary" className="text-[12px]">
              {t('settings.languageDesc')}
            </Text>
          </div>
          </Tabs.TabPane>
          </Tabs>
        </div>
      </div>
    </Spin>
  )
}

export default Settings
