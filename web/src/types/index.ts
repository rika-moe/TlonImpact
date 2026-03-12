export interface Agent {
  id: string
  name: string
  role?: string
  state?: string
  status: 'active' | 'idle' | 'warning' | 'error'
  cpu?: string
  mem?: string
}

export interface Message {
  id: string
  senderId: string
  senderName: string
  senderType: 'human' | 'agent' | 'system'
  content: string
  timestamp: number
  mentions?: string[]
  contentType?: string
  replyTo?: string
  metadata?: Record<string, unknown>
}

export type EventLevel = 'debug' | 'info' | 'warn' | 'error' | 'success'

export interface TlonEvent {
  id: string
  type: string
  level: EventLevel
  agentId?: string
  message: string
  timestamp: number
  payload?: Record<string, unknown>
}
