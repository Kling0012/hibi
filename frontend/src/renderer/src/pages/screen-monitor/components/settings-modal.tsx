import { Button, Checkbox, Form, Modal, Radio, Slider, Spin, Switch, TimePicker } from '@arco-design/web-react'
import screenIcon from '@renderer/assets/icons/screen.svg'
import { ApplyToDays } from '@renderer/store/setting'
import clsx from 'clsx'
import dayjs from 'dayjs'
import customParseFormat from 'dayjs/plugin/customParseFormat'
import React from 'react'
import { useTranslation } from 'react-i18next'

import { Application } from './application'

dayjs.extend(customParseFormat)

interface SettingsModalProps {
  visible: boolean
  form: any
  sources: any
  screenAllSources: any[]
  appAllSources: any[]
  applicationVisible: boolean
  tempRecordInterval: number
  tempEnableRecordingHours: boolean
  tempRecordingHours: [string, string]
  tempApplyToDays: string
  onCancel: () => void
  onSave: () => void
  onSetApplicationVisible: (visible: boolean) => void
  onSetTempRecordInterval: (value: number) => void
  onSetTempEnableRecordingHours: (value: boolean) => void
  onSetTempRecordingHours: (value: [string, string]) => void
  onSetTempApplyToDays: (value: ApplyToDays) => void
}

const SettingsModal: React.FC<SettingsModalProps> = ({
  visible,
  form,
  sources,
  screenAllSources,
  appAllSources,
  applicationVisible,
  tempRecordInterval,
  tempEnableRecordingHours,
  tempRecordingHours,
  tempApplyToDays,
  onCancel,
  onSave,
  onSetApplicationVisible,
  onSetTempRecordInterval,
  onSetTempEnableRecordingHours,
  onSetTempRecordingHours,
  onSetTempApplyToDays
}) => {
  const { t } = useTranslation()
  const validRecordingHours =
    Array.isArray(tempRecordingHours) &&
    tempRecordingHours.length === 2 &&
    tempRecordingHours.every(
      (value) => typeof value === 'string' && dayjs(value, ['HH:mm:ss', 'HH:mm'], true).isValid()
    )
  const invalidRecordingHours = tempEnableRecordingHours && !validRecordingHours
  return (
    <Modal
      title={t('sm.modalTitle')}
      visible={visible}
      autoFocus={false}
      focusLock
      onCancel={onCancel}
      className="text-[#AEAFC2]"
      unmountOnExit
      footer={
        <>
          <Button onClick={onCancel} className="[&_.arco-btn]: !text-xs">
            {t('common.cancel')}
          </Button>
          <Button
            type="primary"
            onClick={onSave}
            disabled={invalidRecordingHours}
            className="[&_.arco-btn-primary]: !bg-black">
            {t('common.save')}
          </Button>
        </>
      }
      style={{ width: 682 }}>
      <Form layout="vertical" form={form}>
        <div className="flex w-full flex-1 mt-5">
          <div className="flex flex-col flex-1 pr-[24px]">
            <Form.Item label={t('sm.intervalLabel')} className="[&_.arco-form-item-label]:!text-xs">
              <Slider
                value={tempRecordInterval}
                onChange={(value) => onSetTempRecordInterval(value as number)}
                min={5}
                max={300}
                marks={{
                  5: '5s',
                  300: '5min'
                }}
                className="!mt-4"
                formatTooltip={(value) => `${value}s`}
              />
            </Form.Item>
            <Form.Item label={t('sm.targets')} shouldUpdate>
              {(values) => {
                const { screenSources = [], windowSources = [] } = values || {}
                const screenList = screenAllSources?.filter((source) => screenSources.includes(source.id)) || []
                const windowList = appAllSources?.filter((source) => windowSources.includes(source.id)) || []
                return (
                  <Spin loading={sources.state === 'loading'} block>
                    <Application
                      value={[...screenList, ...windowList]}
                      onCancel={() => onSetApplicationVisible(false)}
                      visible={applicationVisible}
                      onOk={() => onSetApplicationVisible(true)}
                    />
                  </Spin>
                )
              }}
            </Form.Item>
            <Form.Item label={t('sm.hoursSwitch')} className="[&_.arco-form-item-label]:!text-xs !mb-0">
              <Switch
                checked={tempEnableRecordingHours}
                onChange={onSetTempEnableRecordingHours}
                className={
                  !tempEnableRecordingHours ? '[&_.arco-switch]: !bg-[#e2e3ef]' : '[&_.arco-switch]: !bg-black'
                }
              />
            </Form.Item>
            {tempEnableRecordingHours && (
              <div className="!mt-3">
                <Form.Item
                  label={t('sm.hoursLabel')}
                  validateStatus={invalidRecordingHours ? 'error' : undefined}
                  help={invalidRecordingHours ? t('sm.invalidRecordingHours') : undefined}
                  className="[&_.arco-form-item-label]:!text-xs">
                  <TimePicker.RangePicker
                    format="HH:mm"
                    value={tempRecordingHours}
                    onChange={(value) => onSetTempRecordingHours(value as [string, string])}
                  />
                </Form.Item>
                <Form.Item label={t('sm.weekdayLabel')} className="[&_.arco-form-item-label]: !text-xs">
                  <Radio.Group value={tempApplyToDays} onChange={onSetTempApplyToDays}>
                    <Radio value="weekday" className="[&_.arco-radio-mask]: !border-[#d7daea]">
                      {t('sm.weekdayOnly')}
                    </Radio>
                    <Radio value="everyday" className="[&_.arco-radio-mask]: !border-[#d7daea]">
                      {t('sm.everyday')}
                    </Radio>
                  </Radio.Group>
                </Form.Item>
              </div>
            )}
          </div>
          <div
            className={clsx(
              'flex flex-col flex-1 border-l border-[#efeff4] max-h-[360px] h-[360px] overflow-x-hidden overflow-y-auto px-[16px]  [&_.arco-checkbox-checked_.arco-checkbox-mask]:!bg-[#000000] [&_.arco-checkbox-checked_.arco-checkbox-mask]:!border-[#000000]',
              { hidden: !applicationVisible }
            )}>
            <div className="text-[15px] leading-[18px] text-[#42464e] mb-[12px] font-medium">{t('sm.targets')}</div>
            <div className="[&_.arco-checkbox]:!flex [&_.arco-checkbox]:!items-center">
              <div className="text-[14px] leading-[20px] text-[#42464e] mb-[4px]">{t('sm.screens')}</div>
              <Form.Item field="screenSources">
                <Checkbox.Group className="!grid grid-cols-3 gap-4 relative [&_label]:!mr-0 [&_.arco-checkbox-text]:!ml-0">
                  {screenAllSources.map((source) => (
                    <Checkbox key={source.id} value={source.id}>
                      {({ checked }) => {
                        return (
                          <div className="flex flex-col items-center gap-[4px]">
                            <div
                              className={clsx(
                                'w-[94px] h-[60px] min-w-[94px] min-h-[60px] rounded-[8px] overflow-hidden border relative',
                                checked ? 'border-black' : 'border-transparent'
                              )}>
                              <img
                                src={source.thumbnail || ''}
                                alt="thumbnail"
                                className="w-[94px] h-[60px] inline-block object-cover"
                              />
                              <Checkbox checked={checked} className="!absolute !top-[4px] !right-[4px]" />
                            </div>
                            <div className="flex items-center space-x-[4px]">
                              {source.appIcon ? (
                                <img
                                  src={source.appIcon || ''}
                                  alt=""
                                  className="w-[14px] h-[14px] inline-block object-cover"
                                />
                              ) : (
                                <img src={screenIcon} alt="" className="w-[14px] h-[14px] inline-block object-cover" />
                              )}
                              <div className="text-[13px] leading-[22px] text-[#0b0b0f] !ml-[4px] line-clamp-1">
                                {source.name}
                              </div>
                            </div>
                          </div>
                        )
                      }}
                    </Checkbox>
                  ))}
                </Checkbox.Group>
              </Form.Item>
            </div>
            <div className="[&_.arco-checkbox]:!flex [&_.arco-checkbox]:!items-center">
              <div className="text-[14px] leading-[20px] text-[#42464e] mb-[4px]">{t('sm.windows')}</div>
              <div className="text-[10px] leading-[12px] text-[#737a87] mb-[4px]">{t('sm.windowsHint')}</div>
              <Form.Item field="windowSources">
                <Checkbox.Group className="flex flex-col space-y-4">
                  {appAllSources.map((source) => (
                    <Checkbox key={source.id} value={source.id}>
                      <div className="flex items-center space-x-[4px]">
                        <img
                          src={source.appIcon || source.thumbnail || ''}
                          alt=""
                          className="w-[14px] h-[14px] inline-block object-cover"
                        />
                        <div className="text-[13px] leading-[22px] text-[#0b0b0f] !ml-[4px] line-clamp-1">
                          {source.name}
                        </div>
                      </div>
                    </Checkbox>
                  ))}
                </Checkbox.Group>
              </Form.Item>
            </div>
          </div>
        </div>
      </Form>
    </Modal>
  )
}

export default SettingsModal
