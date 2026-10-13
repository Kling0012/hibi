// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import { createSlice, PayloadAction } from '@reduxjs/toolkit'

export type ApplyToDays = 'weekday' | 'everyday'

export const defaultScreenSettings = {
  recordInterval: 15,
  enableRecordingHours: false,
  recordingHours: ['08:00:00', '20:00:00'] as [string, string],
  applyToDays: 'weekday' as ApplyToDays
}

export type ScreenSettings = typeof defaultScreenSettings

export const defaultGeneralSettings = {
  autoCheckUpdate: true
}

export type GeneralSettings = typeof defaultGeneralSettings

const initialState = {
  screenSettings: defaultScreenSettings,
  generalSettings: defaultGeneralSettings
  // other settings...
}

const settingSlice = createSlice({
  name: 'settings',
  initialState,
  reducers: {
    setScreenSettings(state, action: PayloadAction<Partial<ScreenSettings>>) {
      state.screenSettings = { ...state.screenSettings, ...action.payload }
    },
    setGeneralSettings(state, action: PayloadAction<Partial<GeneralSettings>>) {
      state.generalSettings = { ...defaultGeneralSettings, ...state.generalSettings, ...action.payload }
    }
  }
})

export const { setScreenSettings, setGeneralSettings } = settingSlice.actions

export default settingSlice.reducer
