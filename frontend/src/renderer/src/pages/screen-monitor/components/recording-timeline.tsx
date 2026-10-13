import React from 'react'
import { Typography, Timeline } from '@arco-design/web-react'
import { useTranslation } from 'react-i18next'
import { Activity } from '../screen-monitor'
import { ActivityTimelineItem } from './activitie-timeline-item'
import { formatTime } from '@renderer/utils/time'
import RecordingStatsCard, { RecordingStats } from './recording-stats-card'
import dayjs from 'dayjs'

const { Text } = Typography
const TimelineItem = Timeline.Item

interface RecordingTimelineProps {
  isMonitoring: boolean
  isToday: boolean
  canRecord: boolean
  activities: Activity[]
  recordingStats: RecordingStats | null
  awaySince: string | null
}

const RecordingTimeline: React.FC<RecordingTimelineProps> = ({
  isMonitoring,
  isToday,
  canRecord,
  activities,
  recordingStats,
  awaySince
}) => {
  const { t } = useTranslation()
  console.log('[RecordingTimeline] Props:', {
    isMonitoring,
    isToday,
    canRecord,
    hasRecordingStats: !!recordingStats,
    recordingStats
  })

  return (
    <div className="mt-5">
      <Timeline labelPosition="relative">
        {isToday && (
          <TimelineItem label={t('sm.now')} className="!pb-[24px]">
            {awaySince ? (
              <Text style={{ fontWeight: 'bold', fontSize: 12 }}>
                {t('sm.awaySince', { time: dayjs(awaySince).format('HH:mm') })}
              </Text>
            ) : isMonitoring ? (
              canRecord ? (
                <>
                  <div className="w-full text-sm">
                    <Text className="[&_.arco-typography]: !font-bold [&_.arco-typography]: !text-[#5252FF] [&_.arco-typography]: !text-xs">
                      {t('sm.recordingNow')}
                    </Text>
                    <div className="text-[#C9C9D4]">{t('sm.recordingDesc')}</div>
                  </div>
                  <RecordingStatsCard stats={recordingStats} />
                </>
              ) : (
                <div className="w-full text-sm">
                  <Text className="[&_.arco-typography]: !font-bold [&_.arco-typography]: !text-[#FF4D4F] [&_.arco-typography]: !text-xs">
                    {t('sm.recordingStopped')}
                  </Text>
                  <div className="text-[#C9C9D4]">{t('sm.outsideHours')}</div>
                </div>
              )
            ) : (
              <div style={{ width: '100%', fontSize: 14 }}>
                <Text style={{ fontWeight: 'bold', color: '#FF4D4F', fontSize: 12 }}>{t('sm.recordingStopped')}</Text>
                <div style={{ color: '#C9C9D4' }}>{t('sm.canResume')}</div>
              </div>
            )}
          </TimelineItem>
        )}

        {/* Display activities */}
        {activities
          .sort((a, b) => dayjs(b.start_time).valueOf() - dayjs(a.start_time).valueOf()) // Sort by time in descending order
          .map((activity) => (
            <TimelineItem label={formatTime(activity?.end_time)} key={activity.id}>
              <ActivityTimelineItem key={activity.id} activity={activity} />
            </TimelineItem>
          ))}
      </Timeline>
    </div>
  )
}

export default RecordingTimeline
