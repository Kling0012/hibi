// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import { FC, useCallback, useEffect, useState } from 'react'
import { Button, Input, InputNumber, Message, Select, Spin, Switch, Typography } from '@arco-design/web-react'
import { get } from 'lodash'

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
const ROUTE_LABELS: Record<string, string> = {
  vision: 'Vision (image understanding)',
  chat: 'Chat / agent',
  structured: 'Structured (JSON output)',
  embedding: 'Embedding',
  exact: 'Exact (verbatim screen text)'
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
      Message.error(get(e, 'message', 'Failed to load AI settings'))
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
        [provider.id]: result || { ok: false, message: 'no response' }
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
      Message.error('Provider ids must be unique and non-empty')
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
      Message.success('AI settings saved')
      await load()
      if (init) closeSetting?.()
    } catch (e) {
      Message.error(get(e, 'response.data.message') || get(e, 'message') || 'Failed to save')
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
      Message.success('Capture settings saved')
    } catch (e) {
      Message.error(get(e, 'response.data.message') || get(e, 'message') || 'Failed to save')
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
      Message.success('Sync settings saved')
    } catch (e) {
      Message.error(get(e, 'response.data.message') || get(e, 'message') || 'Failed to save')
    } finally {
      setSyncSaving(false)
    }
  }

  return (
    <Spin loading={loading} block className="[&_.arco-spin-children]:!h-full !h-full">
      <div className="top-0 left-0 flex flex-col h-full overflow-y-hidden py-2 pr-2 relative">
        <div className="bg-white rounded-[16px] pl-6 flex flex-col h-full overflow-y-auto overflow-x-hidden scrollbar-hide pb-2">
          <div className="mb-[12px]">
            <div className="mt-[26px] mb-[10px] text-[24px] font-bold text-[#000]">AI providers</div>
            <Text type="secondary" className="text-[13px]">
              Providers are tried in route order; on failure the next one is used automatically.
              Keys are read from a file path or an inline value.
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
                    placeholder="id (unique)"
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
                    placeholder="model name"
                    className="flex-1"
                  />
                  <Button size="small" onClick={() => test(provider)} loading={testing[provider.id]}>
                    Test
                  </Button>
                  <Button size="small" status="danger" onClick={() => removeProvider(provider.id)}>
                    Remove
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  {provider.kind !== 'gemini_live' && (
                    <Input
                      value={provider.base_url || ''}
                      onChange={(v) => updateProvider(index, { base_url: v })}
                      placeholder="base URL (https://...)"
                      className="flex-1"
                    />
                  )}
                  <Input
                    value={provider.api_key_file || ''}
                    onChange={(v) => updateProvider(index, { api_key_file: v })}
                    placeholder="API key file path (~ allowed)"
                    className="flex-1"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <Input.Password
                    value={provider.api_key || ''}
                    onChange={(v) => updateProvider(index, { api_key: v })}
                    placeholder={provider.has_api_key ? 'API key (saved — leave blank to keep)' : 'API key value (optional if file path set)'}
                    className="flex-1"
                    defaultVisibility={false}
                  />
                </div>
                {testResults[provider.id] && (
                  <Text type={testResults[provider.id].ok ? 'success' : 'error'} className="text-[12px]">
                    {testResults[provider.id].ok ? 'OK' : 'Failed'}: {testResults[provider.id].message}
                  </Text>
                )}
              </div>
            ))}
            <Button
              className="!w-[160px]"
              onClick={() => setProviders((prev) => [...prev, emptyProvider(prev.length + 1)])}
            >
              + Add provider
            </Button>
          </div>

          {/* Routes */}
          <div className="mb-[12px] text-[16px] font-medium text-[#0B0B0F]">Route priorities</div>
          <div className="flex flex-col gap-4 mb-6">
            {ROUTE_KEYS.map((route) => (
              <div key={route} className="flex flex-col gap-1">
                <Text className="text-[14px] font-medium">{ROUTE_LABELS[route]}</Text>
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
                      empty (features using this route are disabled)
                    </Text>
                  )}
                </div>
                <Select
                  placeholder="+ add provider to this route"
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
            <Text className="text-[14px] font-medium">Output language</Text>
            <Select
              value={outputLanguage}
              options={LANGUAGE_OPTIONS}
              onChange={setOutputLanguage}
              className="!w-[160px]"
            />
            <Text type="secondary" className="text-[12px]">
              Appended to prompts so generated text (summaries, reports) uses this language.
            </Text>
          </div>

          {/* Screen recording (P1-B) */}
          <div className="mb-[24px] pt-[16px] border-t border-[#E5E6EB]">
            <div className="mb-[12px] text-[16px] font-medium text-[#0B0B0F]">Screen recording</div>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Recording paused</Text>
                <Switch
                  size="small"
                  checked={!!capture.paused}
                  onChange={(v) => setCapture((prev) => ({ ...prev, paused: v }))}
                />
                <Text type="secondary" className="text-[12px]">
                  Temporarily stop live screen recording (frames are not captured at all).
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Live frame rate (fps)</Text>
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
                  0.2 - 1. Restart recording to apply.
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Live summary interval (s)</Text>
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
                  How often the ongoing screen stream is summarized into an activity.
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Min capture interval (s)</Text>
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
                  Minimum seconds between verbatim screen-text captures.
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Change threshold (dhash distance)</Text>
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
                  Larger = only bigger screen changes trigger a capture.
                </Text>
              </div>
              <div className="flex flex-col gap-1">
                <Text className="text-[14px]">Excluded apps (one per line)</Text>
                <Input.TextArea
                  value={excludeAppsText}
                  onChange={(v) => setExcludeAppsText(v)}
                  placeholder={'1Password\nBitwarden\nKeePassXC'}
                  autoSize={{ minRows: 3, maxRows: 6 }}
                  className="!w-[400px]"
                />
                <Text type="secondary" className="text-[12px]">
                  Frontmost apps never recorded (matched by app name substring).
                </Text>
              </div>
              <Button className="!w-[120px]" loading={captureSaving} onClick={saveCapture}>
                Save capture
              </Button>
            </div>
          </div>

          {/* Sync server (P1-B) */}
          <div className="mb-[24px] pt-[16px] border-t border-[#E5E6EB]">
            <div className="mb-[12px] text-[16px] font-medium text-[#0B0B0F]">Sync server</div>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Upload to sync server</Text>
                <Switch
                  size="small"
                  checked={!!sync.enabled}
                  onChange={(v) => setSync((prev) => ({ ...prev, enabled: v }))}
                />
                <Text type="secondary" className="text-[12px]">
                  Recorded events are queued locally and uploaded in batches. Local recording
                  continues even when the server is unreachable.
                </Text>
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Server URL</Text>
                <Input
                  value={sync.server_url || ''}
                  onChange={(v) => setSync((prev) => ({ ...prev, server_url: v }))}
                  placeholder="http://host:18740"
                  className="!w-[340px]"
                  size="small"
                />
              </div>
              <div className="flex items-center gap-3">
                <Text className="text-[14px] w-[240px]">Device name</Text>
                <Input
                  value={sync.device_name || ''}
                  onChange={(v) => setSync((prev) => ({ ...prev, device_name: v }))}
                  placeholder="(hostname)"
                  className="!w-[340px]"
                  size="small"
                />
              </div>
              {syncStatus && (
                <div className="flex flex-col gap-1 text-[12px] text-[#6E718C]">
                  <span>Device ID: {syncStatus.device_id || '(not generated yet)'}</span>
                  <span>Last upload: {syncStatus.last_sync_at || 'never'}</span>
                  <span>Pending events: {syncStatus.pending_count ?? '—'}</span>
                  {syncStatus.last_error && (
                    <Text type="error">Last error: {syncStatus.last_error}</Text>
                  )}
                </div>
              )}
              <Button className="!w-[120px]" loading={syncSaving} onClick={saveSync}>
                Save sync
              </Button>
            </div>
          </div>

          <Spin loading={saving}>
            <Button type="primary" onClick={save} disabled={saving} className="!bg-[#000]">
              {init ? 'Get started' : 'Save'}
            </Button>
          </Spin>

          <div className="mt-[32px] pt-[16px] border-t border-[#E5E6EB]">
            <div className="mb-[12px] text-[16px] font-medium text-[#0B0B0F]">Application</div>
            <div className="mb-[4px] flex items-center justify-between">
              <span className="text-[14px] text-[#0B0B0F]">Automatically check for updates</span>
              <Switch
                size="small"
                checked={autoCheckUpdate}
                onChange={(v) => setGeneralSettings({ autoCheckUpdate: v })}
              />
            </div>
            <Text type="secondary" className="text-[12px]">
              Off by default. When enabled, MineContext checks for updates from GitHub releases on
              startup and downloads them in the background.
            </Text>
          </div>
        </div>
      </div>
    </Spin>
  )
}

export default Settings
