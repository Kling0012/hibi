import { Typography } from '@arco-design/web-react'
import React from 'react'
import { useTranslation } from 'react-i18next'

const { Text } = Typography

interface RecordingStatusIndicatorProps {
  isMonitoring: boolean
  canRecord: boolean
  isToday: boolean
}

const RecordingStatusIndicator: React.FC<RecordingStatusIndicatorProps> = ({ isMonitoring, canRecord, isToday }) => {
  const { t } = useTranslation()
  if (!isToday) return null

  return (
    <>
      {isMonitoring ? (
        canRecord ? (
          <div className="w-full text-sm">
            <Text className="[&_.arco-typography]: !font-bold [&_.arco-typography]: !text-[#5252FF] [&_.arco-typography]: !text-xs">
              {t('sm.recordingNow')}
            </Text>
            <div className="text-[#C9C9D4]">{t('sm.recordingDesc')}</div>
          </div>
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
    </>
  )
}

export default RecordingStatusIndicator
