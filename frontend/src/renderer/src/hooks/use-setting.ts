// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useCallback } from 'react'
import { useSelector } from 'react-redux'
import { RootState, useAppDispatch } from '@renderer/store'
import { ApplyToDays, GeneralSettings, setScreenSettings as setScreenSettingsAction, setGeneralSettings as setGeneralSettingsAction } from '@renderer/store/setting'

export const useSetting = () => {
  const dispatch = useAppDispatch()
  const screenSettings = useSelector((state: RootState) => state.setting.screenSettings)

  const { recordInterval, recordingHours, enableRecordingHours, applyToDays } = screenSettings

  const autoCheckUpdate = useSelector(
    (state: RootState) => state.setting?.generalSettings?.autoCheckUpdate ?? true
  )

  const setRecordInterval = useCallback(
    (interval: number) => {
      dispatch(setScreenSettingsAction({ recordInterval: interval }))
    },
    [dispatch]
  )

  const setEnableRecordingHours = useCallback(
    (enable: boolean) => {
      dispatch(setScreenSettingsAction({ enableRecordingHours: enable }))
    },
    [dispatch]
  )

  const setRecordingHours = useCallback(
    (hours: [string, string]) => {
      dispatch(setScreenSettingsAction({ recordingHours: hours }))
    },
    [dispatch]
  )

  const setApplyToDays = useCallback(
    (days: ApplyToDays) => {
      dispatch(setScreenSettingsAction({ applyToDays: days }))
    },
    [dispatch]
  )

  const setGeneralSettings = useCallback(
    (settings: Partial<GeneralSettings>) => {
      dispatch(setGeneralSettingsAction(settings))
    },
    [dispatch]
  )

  return {
    recordInterval,
    recordingHours,
    enableRecordingHours,
    applyToDays,
    autoCheckUpdate,
    setRecordInterval,
    setEnableRecordingHours,
    setRecordingHours,
    setApplyToDays,
    setGeneralSettings
  }
}
