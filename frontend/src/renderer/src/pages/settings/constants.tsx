// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

import { ReactNode } from 'react'

import custom from '../../assets/images/settings/custom.svg'
import openAI from '../../assets/images/settings/OpenAI.png'

export enum ModelTypeList {
  OpenAI = 'openai',
  Custom = 'custom'
}

export enum embeddingModels {
  OpenAIEmbeddingModelId = 'text-embedding-3-large'
}
export enum BaseUrl {
  OpenAIUrl = 'https://api.openai.com/v1'
}
export interface OptionInfo {
  value: string
  label: string
}
export interface ModelInfo {
  icon: ReactNode
  key: string
  value: string
  option?: OptionInfo[]
}

export const ModelInfoList = [
  {
    icon: <img src={openAI} className="!max-w-none w-[24px] h-[24px]" />,
    key: 'OpenAI',
    value: 'openai',
    option: [
      {
        value: 'gpt-5',
        label: 'GPT-5'
      },
      {
        value: 'gpt-5-mini',
        label: 'GPT-5 Mini'
      },
      {
        value: 'gpt-5-nano',
        label: 'GPT-5 Nano'
      }
    ]
  },
  {
    icon: <img src={custom} className="!max-w-none w-[18px] h-[18px]" />,
    key: 'Custom',
    value: 'custom'
  }
]
