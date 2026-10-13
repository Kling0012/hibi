import { FC, useState, MouseEvent } from 'react'

import { Typography, Trigger, Modal, Form, Input } from '@arco-design/web-react'
import { IconDelete, IconMore } from '@arco-design/web-react/icon'
import { useMemoizedFn, useRequest } from 'ahooks'
import { conversationService } from '@renderer/services/conversation-service'
import chatHistoryIcon from '@renderer/assets/icons/ai-assistant/chat-history.svg'
import renameIcon from '@renderer/assets/icons/rename.svg'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
export interface ChatHistoryListItemProps {
  conversation: any
  refreshConversationList?: () => void
  handleGetMessages?: (e: MouseEvent, conversationId: number) => void
}

const ChatHistoryListItem: FC<ChatHistoryListItemProps> = (props) => {
  const { conversation, refreshConversationList, handleGetMessages } = props
  const { t } = useTranslation()
  const [renameVisible, setRenameVisible] = useState(false)
  const [form] = Form.useForm()
  const { run: updateConversationTitle } = useRequest(conversationService.updateConversationTitle, { manual: true })
  const { run: deleteConversation } = useRequest(conversationService.deleteConversation, { manual: true })
  const handleDelete = useMemoizedFn(async () => {
    // Add your delete logic here
    Modal.confirm({
      title: t('chat.deleteTitle'),
      content: t('chat.deleteContent'),
      okText: t('chat.delete'),
      onOk: () => {
        deleteConversation(conversation.id)
        refreshConversationList?.()
      }
    })
  })

  const handleRenameSubmit = useMemoizedFn(async () => {
    const values = await form.validate()
    await updateConversationTitle(conversation.id, { title: values.title })
    setRenameVisible(false)
    refreshConversationList?.()
  })
  const [editMenuVisible, setEditMenuVisible] = useState(false)

  return (
    <>
      <Trigger
        popup={() => (
          <div className="text-black border border-[#EAEDF1] p-[6px] shadow-[0_5px_15px_rgba(0,0,0,0.052)] bg-white w-[158px] p-[6px] rounded-[8px]">
            <div
              className="flex items-center mb-[4px] px-[12px] py-[6px] gap-[4px] hover:bg-[#f6f7fa] cursor-pointer"
              onClick={(e) => {
                e.stopPropagation()
                setRenameVisible(true)
              }}>
              <img src={renameIcon} className="block w-[14px] h-[14px]" />
              <div className="text-[14px] leading-[22px]">{t('chat.rename')}</div>
            </div>
            <div
              className="flex items-center px-[12px] py-[6px] gap-[4px] hover:bg-[#f6f7fa] cursor-pointer"
              onClick={(e) => {
                e.stopPropagation()
                handleDelete()
              }}>
              <IconDelete fontSize={14} />
              <div className="text-[14px] leading-[22px]">{t('chat.delete')}</div>
            </div>
          </div>
        )}
        alignPoint
        position="bl"
        popupAlign={{
          bottom: 8,
          left: 8
        }}
        trigger="contextMenu"
        onVisibleChange={setEditMenuVisible}>
        <div
          className={clsx(
            'cursor-pointer flex items-center gap-[6px] px-[12px] py-[9px] hover:bg-[#F6F7FA] rounded-[6px] group justify-between',
            { 'bg-[#F6F7FA]': editMenuVisible }
          )}
          onClick={(e) => handleGetMessages?.(e, conversation.id)}>
          <div className="flex items-center gap-[6px] flex-1">
            <img src={chatHistoryIcon} className="block" />
            <Typography.Text className="!my-0 !flex-1 !text-[13px] !leading-[22px]" ellipsis={{ rows: 1 }}>
              {conversation.title || t('chat.untitled')}
            </Typography.Text>
          </div>
          <Trigger
            updateOnScroll
            popup={() => (
              <div className="text-black border border-[#EAEDF1] rounded-[8px] p-[6px] shadow-[0_5px_15px_rgba(0,0,0,0.052)] bg-white w-[158px] p-[6px]">
                <div
                  className="flex items-center mb-[4px] px-[12px] py-[6px] gap-[4px] hover:bg-[#f6f7fa] cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation()
                    setRenameVisible(true)
                  }}>
                  <img src={renameIcon} className="block w-[14px] h-[14px]" />
                  <div className="text-[14px] leading-[22px]">{t('chat.rename')}</div>
                </div>
                <div
                  className="flex items-center px-[12px] py-[6px] gap-[4px] hover:bg-[#f6f7fa] cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleDelete()
                  }}>
                  <IconDelete fontSize={14} />
                  <div className="text-[14px] leading-[22px]">{t('chat.delete')}</div>
                </div>
              </div>
            )}
            alignPoint
            trigger="click"
            onVisibleChange={setEditMenuVisible}
            popupAlign={{
              bottom: 8
            }}>
            <div
              className={clsx(
                'w-[24px] h-[24px] flex items-center justify-center invisible group-hover:visible group-hover:bg-[#F0F2FA] rounded-[6px] overflow-hidden',
                { 'bg-[#F0F2FA]': editMenuVisible, visible: editMenuVisible }
              )}
              onClick={(e) => e.stopPropagation()}>
              <IconMore />
            </div>
          </Trigger>
        </div>
      </Trigger>
      <Modal
        okText={t('common.save')}
        style={{ width: 480 }}
        title={t('chat.renameTitle')}
        visible={renameVisible}
        onOk={handleRenameSubmit}
        onCancel={() => setRenameVisible(false)}>
        <Form form={form} initialValues={{ title: conversation.title || t('chat.untitled') }}>
          <Form.Item field="title" className={'!mb-0'}>
            <Input className={'!w-[432px]'} clearIcon />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
export { ChatHistoryListItem }
