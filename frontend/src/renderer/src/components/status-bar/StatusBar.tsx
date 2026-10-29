// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import { Input, Tag } from '@arco-design/web-react'
import { getResolvedLang } from '@renderer/i18n'
import { Vault } from '@renderer/types'
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'

const i18nLocale = (): string => {
  const lang = getResolvedLang()
  return lang === 'en' ? 'en-US' : lang === 'zh' ? 'zh-CN' : 'ja-JP'
}

interface StatusBarProps {
  vaultData: Vault | null
  onSummaryChange: (summary: string) => void
  onTagsChange: (tags: string) => void
}

const StatusBar: React.FC<StatusBarProps> = ({ vaultData, onSummaryChange, onTagsChange }) => {
  const { t } = useTranslation()
  const [isEditingSummary, setIsEditingSummary] = useState(false)
  const [isEditingTags, setIsEditingTags] = useState(false)

  if (!vaultData) {
    return null
  }

  const renderRow = (label: string, value: React.ReactNode, className?: string): React.ReactElement => (
    <div className={`flex items-center !mb-[12px] !text-[14px] ${className || ''}`}>
      <span className="text-[#666] w-[140px]">{label} :</span>
      <div className="text-[#333] w-full">{value}</div>
    </div>
  )

  const tags =
    typeof vaultData.tags === 'string' && vaultData.tags.trim() ? vaultData.tags.split(',').map((t) => t.trim()) : []

  return (
    <div className="status-bar-container">
      {renderRow(
        t('statusBar.created'),
        vaultData.created_at
          ? new Intl.DateTimeFormat(i18nLocale(), {
              year: 'numeric',
              month: 'long',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit'
            }).format(new Date(vaultData.created_at))
          : 'N/A'
      )}
      {renderRow(t('statusBar.captureMethod'), 'Creation')}
      {renderRow(t('statusBar.processMethod'), 'Smart Summary')}
      {renderRow(t('statusBar.contextType'), 'Knowledge Context')}
      {renderRow(
        t('statusBar.summary'),
        isEditingSummary ? (
          <Input.TextArea
            defaultValue={vaultData.summary || ''}
            onChange={onSummaryChange}
            onBlur={() => setIsEditingSummary(false)}
            placeholder={t('statusBar.placeholderSummary')}
            autoSize
            autoFocus
            className="w-full"
          />
        ) : (
          <div
            onClick={() => {
              setIsEditingSummary(true)
            }}
            className="min-h-[22px] cursor-text w-full">
            {vaultData.summary || <span className="text-[#aaa]">{t('statusBar.noSummary')}</span>}
          </div>
        )
      )}
      {renderRow(
        t('statusBar.tags'),
        <div className="flex flex-wrap items-center min-h-[32px] gap-x-[4px] gap-y-[4px]">
          {tags.length > 0 ? (
            tags.map((tag, index) => (
              <Tag key={index} className="mr-[8px] mb-[4px]">
                {tag}
              </Tag>
            ))
          ) : (
            <div className="text-[#aaa] mr-[8px]">{t('statusBar.noTags')}</div>
          )}
          {isEditingTags ? (
            <Input
              defaultValue=""
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const value = e.currentTarget.value.trim()
                  if (value.includes(',')) {
                    return
                  }
                  if (value) {
                    const newTags = [...tags, value]
                    onTagsChange(newTags.join(', '))
                    setIsEditingTags(false)
                  }
                }
              }}
              onBlur={() => setIsEditingTags(false)}
              placeholder={t('statusBar.placeholderTag')}
              autoFocus
              className="!w-[120px] mr-[8px] mb-[4px] !inline-block"
            />
          ) : (
            <Tag
              className="mb-[4px] cursor-pointer bg-[#f0f0f0] !border-[#d9d9d9] !border-dashed border"
              onClick={(e) => {
                e.stopPropagation()
                setIsEditingTags(true)
              }}>
              {t('statusBar.newTag')}
            </Tag>
          )}
        </div>,
        'items-start'
      )}
    </div>
  )
}

export default StatusBar
