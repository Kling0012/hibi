import { Button, DatePicker, Typography } from '@arco-design/web-react'
import { IconDown, IconLeft, IconRight } from '@arco-design/web-react/icon'
import { getResolvedLang } from '@renderer/i18n'
import React from 'react'
import { useTranslation } from 'react-i18next'

const { Text } = Typography

interface DateNavigationProps {
  hasPermission: boolean
  currentDate: Date
  isToday: boolean
  onPreviousDay: () => void
  onNextDay: () => void
  onDateChange: (dateString: any, date: any) => void
  onSetCurrentDate: (date: Date) => void
  disabledDate: (current: any) => boolean
}

const DateNavigation: React.FC<DateNavigationProps> = ({
  hasPermission,
  currentDate,
  isToday,
  onPreviousDay,
  onNextDay,
  onDateChange,
  onSetCurrentDate,
  disabledDate
}) => {
  const { t } = useTranslation()

  const dayLabel = (date: Date): string =>
    new Intl.DateTimeFormat({ ja: 'ja-JP', en: 'en-US', zh: 'zh-CN' }[getResolvedLang()], {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      weekday: 'short'
    }).format(date)

  return (
    <div className="flex justify-between items-center mb-4">
      <div className="flex items-center">
        {hasPermission && (
          <>
            <Button
              className="[&_.arco-btn-primary]: !bg-white [&_.arco-btn-primary]: !border-gray-200 [&_.arco-btn-primary]: !h-6 [&_.arco-btn-primary]: !text-black [&_.arco-btn-primary]:  !text-xs [&_.arco-btn-primary]: !mr-2 [&_.arco-btn:hover]: !bg-gray-50"
              onClick={() => onSetCurrentDate(new Date())}>
              {t('home.today')}
            </Button>
            <Button
              icon={<IconLeft />}
              onClick={onPreviousDay}
              className="[&_.arco-btn-primary]: !mr-3 [&_.arco-btn-primary]: !bg-transparent [&_.arco-btn-primary]: !border-none [&_.arco-btn:hover]: !bg-gray-100"
            />
            <DatePicker
              value={currentDate}
              onChange={onDateChange}
              disabledDate={disabledDate}
              triggerElement={
                <Button className="[&_.arco-btn-primary]: !h-[22px] [&_.arco-btn-primary]: !bg-transparent [&_.arco-btn-primary]: !border-none [&_.arco-btn-primary]: !p-0 [&_.arco-btn:hover]: !bg-gray-50">
                  <Text className="[&_.arco-typography]: !font-medium [&_.arco-typography]: !text-sm">
                    {dayLabel(currentDate)}
                  </Text>
                  <IconDown className="ml-1 w-3 h-3" />
                </Button>
              }
            />
            <Button
              icon={<IconRight />}
              onClick={onNextDay}
              className="[&_.arco-btn-primary]: !ml-3 [&_.arco-btn-primary]: !bg-transparent [&_.arco-btn-primary]: !border-none [&_.arco-btn:hover]: !bg-gray-100"
              disabled={isToday}
            />
          </>
        )}
      </div>
    </div>
  )
}

export default DateNavigation
