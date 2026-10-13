// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import i18n from '@renderer/i18n'
import dayjs from 'dayjs'
import isBetween from 'dayjs/plugin/isBetween'
import utc from 'dayjs/plugin/utc'
dayjs.extend(isBetween)
dayjs.extend(utc)

// Format time
export const formatTime = (date: Date | number | string) => {
  return dayjs(date).format('HH:mm:ss')
}

// Format date
export const formatDate = (date: Date) => {
  return dayjs(date).format('MMM D, HH:mm')
}

export const timestampToISODateString = (timestamp: number): string => {
  return dayjs(timestamp).format('YYYYMMDD')
}

export const timeToISOTimeString = (time: number | Date): string => {
  const dt = dayjs(time)
  // 保持原始逻辑：用本地时间的组件创建UTC时间
  return dayjs.utc(dt.format('YYYY-MM-DD HH:mm:ss')).toISOString()
}

// Calculate the elapsed time based on the date string and the current time, with precision to days/hours and minutes
export const formatRelativeTime = (dateString: string): string => {
  const now = dayjs()
  const time = dayjs(dateString)
  const diffInMinutes = now.diff(time, 'minute')

  if (diffInMinutes < 1) {
    return i18n.t('time.justNow')
  }

  // Calculate the difference in days
  const days = Math.floor(diffInMinutes / (24 * 60))

  // If it's more than 7 days, display the specific date (month/day hour:minute)
  if (days >= 7) {
    return time.format('MM/DD HH:mm')
  }

  // If it's more than 24 hours but less than 7 days, display as N days ago
  if (days > 0) {
    return i18n.t('time.daysAgo', { count: days })
  }

  const hours = Math.floor(diffInMinutes / 60)
  const minutes = diffInMinutes % 60

  if (hours > 0) {
    return i18n.t('time.hoursAgo', { count: hours })
  } else {
    return i18n.t('time.minutesAgo', { count: minutes })
  }
}

// Format '2025-05-02' to determine if the date is within the last seven days
export const isWithinSevenDays = (dateString: string): boolean => {
  const targetDate = dayjs(dateString) // Parse as a Day.js object

  // Calculate the start time of seven days ago (00:00:00)
  const sevenDaysAgo = dayjs().subtract(7, 'day').startOf('day')
  // Calculate the end time of the current time (23:59:59)
  const nowEnd = dayjs().endOf('day')

  // Determine if the target date is within the [seven days ago, now] interval (inclusive)
  const isWithinSevenDays = targetDate.isBetween(
    sevenDaysAgo,
    nowEnd,
    'day', // Compare by day (ignoring time differences)
    '[]' // Include the start and end of the interval
  )
  return isWithinSevenDays
}

// Export vault-related utility functions
export * from './vault'
