import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { TlonEvent } from '../types'

interface EventRowProps {
  event: TlonEvent
}

const levelBadgeColors: Record<string, string> = {
  debug: 'badge-ghost',
  info: 'badge-info',
  warn: 'badge-warning',
  error: 'badge-error',
  success: 'badge-success',
}

const levelLabels: Record<string, string> = {
  debug: '调试',
  info: '信息',
  warn: '警告',
  error: '错误',
  success: '成功',
}

export default function EventRow({ event }: EventRowProps) {
  const [expanded, setExpanded] = useState(false)

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp)
    const timeStr = date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    const ms = date.getMilliseconds().toString().padStart(3, '0')
    return `${timeStr}.${ms}`
  }

  const truncateMessage = (msg: string, maxLen: number = 50) => {
    if (msg.length <= maxLen) return msg
    return msg.slice(0, maxLen) + '...'
  }

  const hasPayload = event.payload && Object.keys(event.payload).length > 0

  return (
    <>
      <tr 
        className={`
          hover:bg-base-200/50 
          cursor-pointer 
          transition-colors duration-150
          ${expanded ? 'bg-base-200/30' : ''}
        `}
        onClick={() => hasPayload && setExpanded(!expanded)}
      >
        {/* Time */}
        <td className="whitespace-nowrap py-3">
          <div className="flex items-center gap-1">
            {hasPayload && (
              <div className={`
                transition-transform duration-200 
                ${expanded ? 'rotate-180' : ''}
              `}>
                <ChevronDown className="w-4 h-4 text-base-content/40" />
              </div>
            )}
            <span className="font-mono text-xs text-base-content/60">
              {formatTime(event.timestamp)}
            </span>
          </div>
        </td>

        {/* Level */}
        <td className="py-3">
          <span className={`
            badge badge-sm 
            ${levelBadgeColors[event.level] || levelBadgeColors.info}
          `}>
            {levelLabels[event.level] || event.level.toUpperCase()}
          </span>
        </td>

        {/* Type */}
        <td className="py-3">
          <span className="
            badge badge-sm badge-ghost 
            font-mono text-xs
          ">
            {event.type}
          </span>
        </td>

        {/* Agent */}
        <td className="py-3 text-base-content/70">
          {event.agentId ? (
            <span className="font-medium text-sm">{event.agentId}</span>
          ) : (
            <span className="text-base-content/30">-</span>
          )}
        </td>

        {/* Message */}
        <td className="py-3 max-w-md">
          <span 
            className="text-sm text-base-content/80" 
            title={event.message}
          >
            {truncateMessage(event.message)}
          </span>
        </td>
      </tr>

      {/* Expanded Payload */}
      {expanded && hasPayload && (
        <tr className="bg-base-200/50">
          <td colSpan={5} className="p-0">
            <div className="p-4">
              <div className="card bg-base-100 shadow-sm">
                <div className="card-body p-4">
                  <h4 className="
                    text-xs font-semibold 
                    text-base-content/50 
                    mb-2 uppercase tracking-wider
                  ">
                    Payload
                  </h4>
                  <pre className="
                    text-xs font-mono 
                    text-base-content/70 
                    overflow-x-auto 
                    whitespace-pre-wrap break-all
                  ">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
