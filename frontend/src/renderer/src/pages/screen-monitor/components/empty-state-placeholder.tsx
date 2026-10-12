import React from 'react'
import { Typography, Button } from '@arco-design/web-react'
import Stopped from '@renderer/assets/images/screen-monitor/stopped.png'
import NeedPermission from '@renderer/assets/images/screen-monitor/need-permission.svg'
import screenMonitorEmpty from '@renderer/assets/images/screen-monitor/screen-monitor-empty.svg'

const { Text } = Typography

interface EmptyStatePlaceholderProps {
  hasPermission: boolean
  isToday: boolean
  onGrantPermission: () => void
}

const EmptyStatePlaceholder: React.FC<EmptyStatePlaceholderProps> = ({ hasPermission, isToday, onGrantPermission }) => {
  return (
    <div className="flex items-center justify-center flex-1 min-h-[300px]">
      <div className="text-center flex flex-col items-center justify-center">
        {hasPermission ? (
          isToday ? (
            <>
              <img src={Stopped} alt="Screen recording" style={{ width: 66, height: 78 }} />
              <Text style={{ marginTop: 16, width: 270, color: '#6C7191', fontSize: 12 }}>
                記録を開始すると、画面の変化を AI が読み取り、作業の記録として残します。
              </Text>
            </>
          ) : (
            <>
              <img src={screenMonitorEmpty} alt="Screen recording" style={{ width: 66, height: 78 }} />
              <Text style={{ marginTop: 16, width: 270, color: '#6C7191', fontSize: 12 }}>この日の記録はありません</Text>
            </>
          )
        ) : (
          <>
            <img src={NeedPermission} alt="Need permission" style={{ width: 286, height: 168, marginLeft: 67 }} />
            <Text style={{ marginTop: 16, width: 440, color: '#6C7191', fontSize: 12 }}>
              画面収録の許可をオンにすると、AI が画面から作業を記録できるようになります。
            </Text>
            <Button
              type="primary"
              size="large"
              onClick={onGrantPermission}
              className="[&_.arco-btn-primary]: !mt-6 [&_.arco-btn-primary]: !font-medium [&_.arco-btn-primary]: !bg-black">
              許可する
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

export default EmptyStatePlaceholder
