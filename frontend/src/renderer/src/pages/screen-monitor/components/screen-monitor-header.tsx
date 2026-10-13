import React from 'react'
import { Button, Space, Typography, Popover } from '@arco-design/web-react'
import { IconPlayArrow, IconSettings, IconRecordStop } from '@arco-design/web-react/icon'
import { useTranslation } from 'react-i18next'

const { Title, Text } = Typography

interface ScreenMonitorHeaderProps {
  hasPermission: boolean
  isMonitoring: boolean
  isToday: boolean
  screenAllSources: any[]
  appAllSources: any[]
  onOpenSettings: () => void
  onStartMonitoring: () => void
  onStopMonitoring: () => void
  onRequestPermission: () => void
}

const ScreenMonitorHeader: React.FC<ScreenMonitorHeaderProps> = ({
  hasPermission,
  isMonitoring,
  isToday,
  screenAllSources,
  appAllSources,
  onOpenSettings,
  onStartMonitoring,
  onStopMonitoring
}) => {
  const { t } = useTranslation()
  return (
    <div className="flex justify-between items-start mb-3 flex-col md:flex-row">
      <div className="w-full md:w-4/5">
        <Title
          heading={3}
          className="[&_.arco-typography]: !mt-1 [&_.arco-typography]: !font-bold [&_.arco-typography]: !text-[24px] [&_.arco-typography]: !text-black">
          {t('sm.title')}
        </Title>
        <Text type="secondary" className="[&_.arco-typography]: !text-[13px]">
          {t('sm.desc')}
        </Text>
      </div>
      <div className="flex items-center ml-0 md:ml-6 mt-4 md:mt-0 justify-end">
        {hasPermission ? (
          <Space>
            <Popover content={t('sm.targetsDisabledHint')} disabled={!isMonitoring}>
              <Button
                type="outline"
                icon={<IconSettings />}
                size="large"
                disabled={isMonitoring}
                onClick={onOpenSettings}
                className="[&_.arco-btn]: !bg-white [&_.arco-btn]: !border-gray-300 [&_.arco-btn]: !text-black [&_.arco-btn:hover]: !bg-gray-50">
                {t('sm.targets')}
              </Button>
            </Popover>
            {!isMonitoring ? (
              <Popover
                content={t('sm.startDisabledHint')}
                disabled={!(screenAllSources.length === 0 && appAllSources.length === 0)}>
                <Button
                  type="primary"
                  icon={<IconPlayArrow />}
                  size="large"
                  onClick={onStartMonitoring}
                  disabled={isMonitoring || !isToday}
                  style={{
                    background: '#000'
                  }}>
                  {t('sm.start')}
                </Button>
              </Popover>
            ) : (
              <Button
                type="primary"
                status="danger"
                icon={<IconRecordStop />}
                size="large"
                onClick={onStopMonitoring}
                className="[&_.arco-btn-primary]: !bg-red-500 [&_.arco-btn-primary:hover]: !bg-red-600">
                {t('sm.stop')}
              </Button>
            )}
          </Space>
        ) : null}
      </div>
    </div>
  )
}

export default ScreenMonitorHeader
