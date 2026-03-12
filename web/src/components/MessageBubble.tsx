import { useState } from 'react'
import { Copy, Check, Bot, User as UserIcon } from 'lucide-react'
import type { Message } from '../types'

interface MessageBubbleProps {
  message: Message
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false)
  const isUser = message.senderType === 'human'
  const isSystem = message.senderType === 'system'
  
  const formatTime = (timestamp: number) => {
    return new Date(timestamp).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Ignore copy errors
    }
  }

  // System message
  if (isSystem) {
    return (
      <div className="flex justify-center my-4">
        <span className="
          badge badge-ghost badge-sm 
          px-3 py-1
          text-caption text-base-content/50
        ">
          {message.content}
        </span>
      </div>
    )
  }

  return (
    <div className={`
      flex gap-3 mb-5 
      ${isUser ? 'flex-row-reverse' : ''}
    `}>
      {/* ============================================
          AVATAR
          ============================================ */}
      <div className="tooltip" data-tip={message.senderName}>
        <div className={`
          w-10 h-10 rounded-full 
          flex items-center justify-center 
          flex-shrink-0
          ring-2 ring-offset-2 ring-offset-base-100
          transition-all duration-200
          ${isUser 
            ? 'bg-primary ring-primary' 
            : 'bg-base-200 ring-base-300'
          }
        `}>
          {isUser ? (
            <UserIcon className="w-5 h-5 text-primary-content" />
          ) : (
            <Bot className="w-5 h-5 text-base-content/70" />
          )}
        </div>
      </div>

      {/* ============================================
          MESSAGE CONTENT
          ============================================ */}
      <div className={`
        flex-1 max-w-[85%] sm:max-w-[75%]
        ${isUser ? 'items-end' : 'items-start'} 
        flex flex-col
      `}>
        {/* Header: Name + Time */}
        <div className={`
          flex items-center gap-2 mb-1.5
          ${isUser ? 'flex-row-reverse' : ''}
        `}>
          <span className={`
            text-sm font-semibold
            ${isUser ? 'text-primary' : 'text-base-content'}
          `}>
            {message.senderName}
          </span>
          <span className="text-xs text-base-content/40">
            {formatTime(message.timestamp)}
          </span>
          {message.mentions && message.mentions.length > 0 && (
            <span className="badge badge-xs badge-primary">
              @{message.mentions.length}
            </span>
          )}
        </div>

        {/* Message Bubble */}
        <div className={`
          relative group
          px-4 py-3 
          rounded-2xl
          shadow-sm
          transition-all duration-200
          ${isUser 
            ? 'bg-primary text-primary-content rounded-br-md shadow-primary/20' 
            : 'bg-base-200 text-base-content rounded-bl-md border border-base-300'
          }
        `}>
          {/* Content */}
          <div className={`
            whitespace-pre-wrap break-words 
            leading-relaxed
            ${isUser ? '' : ''}
          `}>
            {message.content}
          </div>
          
          {/* Copy Button */}
          <button
            onClick={handleCopy}
            className={`
              absolute top-2 
              ${isUser ? 'left-2' : 'right-2'} 
              btn btn-ghost btn-xs btn-square
              opacity-0 group-hover:opacity-100
              transition-all duration-200
              ${isUser 
                ? 'text-primary-content/70 hover:text-primary-content hover:bg-primary-content/20' 
                : 'text-base-content/50 hover:text-base-content hover:bg-base-300'
              }
            `}
            title="复制消息"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
