// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import axiosInstance from '@renderer/services/axiosConfig'
import { get } from 'lodash'

// Model configuration interface
export interface ModelConfigProps {
  modelPlatform: string // Model platform, e.g., openai, custom
  modelId: string // VLM model ID
  baseUrl: string // API base URL
  embeddingModelId: string // Embedding model ID
  apiKey: string // API key
  embeddingBaseUrl?: string // Optional separate embedding base URL
  embeddingApiKey?: string // Optional separate embedding API key
  embeddingModelPlatform?: string // Optional separate embedding platform
}

// API response data structure
export interface ModelInfoResponseData {
  config: ModelConfigProps
}

// Complete API response structure
export interface ApiResponse<T> {
  code: number
  status: number
  message: string
  data: T
}

// Get model settings information
export const getModelInfo = async (): Promise<ModelInfoResponseData | undefined> => {
  const res = await axiosInstance.get<ModelInfoResponseData>('/api/model_settings/get')
  return get(res, 'data.data')
}

// Update model settings information
// Update model settings information response data structure
export interface UpdateModelSettingsResponseData {
  success: boolean
  message: string
}

export const updateModelSettingsAPI = async (
  params: ModelConfigProps
): Promise<UpdateModelSettingsResponseData | undefined> => {
  const res = await axiosInstance.post<UpdateModelSettingsResponseData>('/api/model_settings/update', {
    config: {
      ...params
    }
  })
  return get(res, 'data.data')
}

// ==================== AI provider settings (P1) ====================

export type ProviderKind = 'openai_compat' | 'gemini_live'

export interface AIProviderConfig {
  id: string
  kind: ProviderKind | string
  model: string
  base_url?: string
  api_key_file?: string
  api_key?: string
  has_api_key?: boolean
  options?: Record<string, unknown>
}

export interface AIRoutes {
  vision?: string[]
  chat?: string[]
  structured?: string[]
  embedding?: string[]
}

export interface AISettingsPayload {
  providers: AIProviderConfig[]
  routes: AIRoutes
  output_language: string
}

export interface AIProviderStatus extends AIProviderConfig {
  usable: boolean
  in_cooldown: boolean
  cooldown_remaining_s: number
  consecutive_failures: number
  last_error: string
}

export interface AISettingsResponseData {
  ai: AISettingsPayload
  status: { providers: AIProviderStatus[]; routes: AIRoutes; output_language: string }
}

export const getAISettings = async (): Promise<AISettingsResponseData | undefined> => {
  const res = await axiosInstance.get<AISettingsResponseData>('/api/ai_settings')
  return get(res, 'data.data')
}

export const updateAISettings = async (params: AISettingsPayload): Promise<void> => {
  await axiosInstance.post('/api/ai_settings', { ai: params })
}

export const testAIProvider = async (
  provider: AIProviderConfig
): Promise<{ ok: boolean; message: string } | undefined> => {
  const res = await axiosInstance.post<{ ok: boolean; message: string }>('/api/ai_settings/test', {
    provider
  })
  return get(res, 'data.data')
}
