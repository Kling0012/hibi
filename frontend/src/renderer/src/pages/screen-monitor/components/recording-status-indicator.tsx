import React from 'react'
import { Typography } from '@arco-design/web-react'
import { SCREEN_INTERVAL_TIME } from '../constant'

const { Text } = Typography

interface RecordingStatusIndicatorProps {
  isMonitoring: boolean
  canRecord: boolean
  isToday: boolean
}

const RecordingStatusIndicator: React.FC<RecordingStatusIndicatorProps> = ({ isMonitoring, canRecord, isToday }) => {
  if (!isToday) return null

  return (
    <>
      {isMonitoring ? (
        canRecord ? (
          <div className="w-full text-sm">
            <Text className="[&_.arco-typography]: !font-bold [&_.arco-typography]: !text-[#5252FF] [&_.arco-typography]: !text-xs">
              画面を記録しています…
            </Text>
            <div className="text-[#C9C9D4]">
              Every {SCREEN_INTERVAL_TIME} minutes, Hibi generates an Activity based on screen analysis.
            </div>
          </div>
        ) : (
          <div className="w-full text-sm">
            <Text className="[&_.arco-typography]: !font-bold [&_.arco-typography]: !text-[#FF4D4F] [&_.arco-typography]: !text-xs">
              記録は止まっています
            </Text>
            <div className="text-[#C9C9D4]">
              今は記録する時間帯の外です。次の時間帯になると自動で始まります。
            </div>
          </div>
        )
      ) : (
        <div style={{ width: '100%', fontSize: 14 }}>
          <Text style={{ fontWeight: 'bold', color: '#FF4D4F', fontSize: 12 }}>記録は止まっています</Text>
          <div style={{ color: '#C9C9D4' }}>いつでも記録を再開できます</div>
        </div>
      )}
    </>
  )
}

export default RecordingStatusIndicator
