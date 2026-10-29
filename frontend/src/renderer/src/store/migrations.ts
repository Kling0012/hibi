import { getLogger } from '@shared/logger/renderer'
import { omit } from 'lodash'

import { defaultGeneralSettings } from './setting'

const logger = getLogger('migrations')

const migrations = {
  2: (state: any): any => {
    logger.info('Running redux-persist migration for version 2...')

    if (!state) {
      logger.warn('Migration v2: Received undefined state, returning state as-is.')
      return state
    }

    const newState = omit(state, ['chatHistory'])

    if (state.chatHistory) {
      logger.info('Migration v2: Removed "chatHistory" from persisted state.')
    } else {
      logger.info('Migration v2: "chatHistory" not found in state, nothing to remove.')
    }

    return newState
  },
  3: (state: any): any => {
    logger.info('Running redux-persist migration for version 3...')

    if (!state) {
      logger.warn('Migration v3: Received undefined state, returning state as-is.')
      return state
    }

    const newState = {
      ...state,
      setting: {
        ...state.setting,
        generalSettings: { ...defaultGeneralSettings, ...state.setting?.generalSettings }
      }
    }

    logger.info('Migration v3: Ensured "setting.generalSettings" defaults (auto update check off).')
    return newState
  },
  4: (state: any): any => {
    // Auto update became on by default; earlier installs stored the old default (off).
    if (!state?.setting?.generalSettings) return state
    return {
      ...state,
      setting: {
        ...state.setting,
        generalSettings: { ...state.setting.generalSettings, autoCheckUpdate: true }
      }
    }
  }
}
export { migrations }
