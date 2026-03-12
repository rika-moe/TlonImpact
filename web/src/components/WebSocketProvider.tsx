import { useEffect, useCallback, createContext, useContext, useRef, useState } from 'react'
import { useAppStore } from '../stores/appStore'
import { useMessageStore } from '../stores/messageStore'
import { useWebSocket } from '../hooks/useWebSocket'
import { testWebSocket } from '../utils/testWebSocket'
import { transformMessage, transformAgent, snakeToCamel } from '../utils/transform'
import type { Message, Agent, TlonEvent } from '../types'

interface WebSocketContextType {
  send: (data: unknown) => boolean
  isConnected: boolean
}

const WebSocketContext = createContext<WebSocketContextType | null>(null)

export function useWebSocketContext() {
  const context = useContext(WebSocketContext)
  if (!context) {
    throw new Error('useWebSocketContext must be used within WebSocketProvider')
  }
  return context
}

interface WebSocketProviderProps {
  children: React.ReactNode
}

export default function WebSocketProvider({ children }: WebSocketProviderProps) {
  const { wsUrl, setConnected, setSessionId } = useAppStore()
  const messageStore = useMessageStore()
  
  const [isReady, setIsReady] = useState(false)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  
  // Refs to track state without causing re-renders
  const isReadyRef = useRef(false)
  const hasSentAuthRef = useRef(false)
  const pendingAuthRef = useRef(false)
  const sendQueueRef = useRef<unknown[]>([])
  
  // Store store actions in refs to avoid dependency issues
  const storeRef = useRef({
    addMessage: messageStore.addMessage,
    addEvent: messageStore.addEvent,
    updateAgentStatus: messageStore.updateAgentStatus,
    setAgents: messageStore.setAgents,
    setMessages: messageStore.setMessages,
    setSessionId,
    setConnected,
  })
  
  // Update refs when stores change
  useEffect(() => {
    storeRef.current = {
      addMessage: messageStore.addMessage,
      addEvent: messageStore.addEvent,
      updateAgentStatus: messageStore.updateAgentStatus,
      setAgents: messageStore.setAgents,
      setMessages: messageStore.setMessages,
      setSessionId,
      setConnected,
    }
  }, [messageStore, setSessionId, setConnected])

  // Run diagnostic on mount
  useEffect(() => {
    testWebSocket(wsUrl).then(result => {
      if (!result.success) {
        setConnectionError(`Cannot connect to ${wsUrl}: ${result.error}`)
        console.error('[WebSocketProvider] Diagnostic failed:', result)
      }
    })
  }, [wsUrl])

  // Stable message handler
  const handleMessage = useCallback((data: unknown) => {
    const msg = data as Record<string, unknown>
    const msgType = msg.type as string
    
    console.log('[WebSocketProvider] Received:', msgType, msg)
    
    switch (msgType) {
      case 'auth_success': {
        const sessionId = msg.session_id as string
        storeRef.current.setSessionId(sessionId)
        isReadyRef.current = true
        hasSentAuthRef.current = true
        pendingAuthRef.current = false
        setIsReady(true)
        
        // Process any queued messages
        while (sendQueueRef.current.length > 0) {
          const queuedMsg = sendQueueRef.current.shift()
          if (queuedMsg) {
            sendRef.current?.(queuedMsg)
          }
        }
        
        // Request initial data
        setTimeout(() => {
          sendRef.current?.({ type: 'mode', mode: 'chat' })
        }, 100)
        break
      }
        
      case 'history': {
        if (msg.messages) {
          const messages = (msg.messages as Record<string, unknown>[]).map((m) => {
            const transformed = transformMessage(m)
            return {
              ...transformed,
              senderType: (transformed.senderType as string) || 'agent',
              senderName: (transformed.senderName as string) || 'Unknown',
              senderId: (transformed.senderId as string) || 'unknown',
            } as Message
          })
          storeRef.current.setMessages(messages)
        }
        break
      }
        
      case 'participants': {
        console.log('[WebSocketProvider] Participants:', msg.participants)
        break
      }
        
      case 'agents': {
        if (msg.agents) {
          const agents = (msg.agents as Record<string, unknown>[]).map((a) => {
            const transformed = transformAgent(a)
            return {
              ...transformed,
              status: (transformed.status as Agent['status']) || 
                      (transformed.state as Agent['status']) || 
                      'idle',
            } as Agent
          })
          storeRef.current.setAgents(agents)
        }
        break
      }
        
      case 'event': {
        const eventType = msg.event_type as string
        const payload = msg.payload as Record<string, unknown>
        
        // Handle agent state changes
        if (eventType === 'agent.started' && payload.agent_id) {
          storeRef.current.updateAgentStatus(payload.agent_id as string, 'active')
        } else if (eventType === 'agent.stopped' && payload.agent_id) {
          storeRef.current.updateAgentStatus(payload.agent_id as string, 'idle')
        } else if (eventType === 'agent.error' && payload.agent_id) {
          storeRef.current.updateAgentStatus(payload.agent_id as string, 'error')
        }
        
        // Add to events
        const event: TlonEvent = {
          id: `${Date.now()}-${Math.random()}`,
          type: eventType,
          level: getEventLevel(eventType),
          agentId: payload.agent_id as string,
          message: getEventMessage(eventType, payload),
          timestamp: (msg.timestamp as number) || Date.now(),
          payload: snakeToCamel(payload),
        }
        storeRef.current.addEvent(event)
        
        // Handle message events
        if (eventType === 'message.new' || eventType === 'message.mention') {
          const transformedPayload = transformMessage(payload)
          const message: Message = {
            id: (transformedPayload.id as string) || `${Date.now()}`,
            senderId: (transformedPayload.senderId as string) || 'unknown',
            senderName: (transformedPayload.senderName as string) || 'Unknown',
            senderType: (transformedPayload.senderType as 'human' | 'agent' | 'system') || 'agent',
            content: (transformedPayload.content as string) || '',
            timestamp: typeof transformedPayload.timestamp === 'number' 
              ? transformedPayload.timestamp 
              : Date.now(),
            mentions: transformedPayload.mentions as string[],
            contentType: transformedPayload.contentType as string,
          }
          storeRef.current.addMessage(message)
        }
        break
      }
        
      case 'message': {
        const payload = msg.payload as Record<string, unknown>
        const transformedPayload = transformMessage(payload)
        const message: Message = {
          id: (transformedPayload.id as string) || `${Date.now()}`,
          senderId: (transformedPayload.senderId as string) || 'unknown',
          senderName: (transformedPayload.senderName as string) || 'Unknown',
          senderType: (transformedPayload.senderType as 'human' | 'agent' | 'system') || 'agent',
          content: (transformedPayload.content as string) || '',
          timestamp: typeof transformedPayload.timestamp === 'number' 
            ? transformedPayload.timestamp 
            : Date.now(),
          mentions: transformedPayload.mentions as string[],
          contentType: transformedPayload.contentType as string,
        }
        storeRef.current.addMessage(message)
        break
      }
        
      case 'mode_changed': {
        console.log('[WebSocketProvider] Mode changed:', msg.mode)
        break
      }
        
      case 'error': {
        console.error('[WebSocketProvider] Server error:', msg.message)
        break
      }
    }
  }, [])

  const { send, isConnected: wsConnected } = useWebSocket({
    url: wsUrl,
    onOpen: () => {
      console.log('[WebSocketProvider] onOpen - sending auth')
      hasSentAuthRef.current = false
      pendingAuthRef.current = true
      
      // Send auth immediately when connected
      setTimeout(() => {
        sendRef.current?.({
          type: 'auth',
          client_type: 'web',
          metadata: { version: '0.1.0' },
        })
      }, 50)
    },
    onClose: () => {
      console.log('[WebSocketProvider] onClose')
      isReadyRef.current = false
      hasSentAuthRef.current = false
      pendingAuthRef.current = false
      setIsReady(false)
      storeRef.current.setConnected(false)
    },
    onError: (error) => {
      console.error('[WebSocketProvider] onError:', error)
    },
    onMessage: handleMessage,
    reconnect: true,
    reconnectInterval: 3000,
  })

  // Store send function in ref
  const sendRef = useRef<((data: unknown) => boolean) | null>(null)
  useEffect(() => {
    sendRef.current = send
  }, [send])

  // Update connected state when WebSocket is ready
  useEffect(() => {
    const shouldBeConnected = wsConnected && isReadyRef.current
    console.log('[WebSocketProvider] Connection state:', { wsConnected, isReady: isReadyRef.current, shouldBeConnected })
    storeRef.current.setConnected(shouldBeConnected)
  }, [wsConnected, isReady])

  // Handle reconnection - need to re-authenticate
  useEffect(() => {
    if (wsConnected && !isReadyRef.current && !pendingAuthRef.current) {
      console.log('[WebSocketProvider] Reconnected, sending auth...')
      pendingAuthRef.current = true
      setTimeout(() => {
        sendRef.current?.({
          type: 'auth',
          client_type: 'web',
          metadata: { version: '0.1.0' },
        })
      }, 100)
    }
  }, [wsConnected])

  // Listen for custom send events from other components
  useEffect(() => {
    const handleSendEvent = (e: CustomEvent) => {
      const detail = e.detail
      if (detail) {
        console.log('[WebSocketProvider] Received send event:', detail)
        if (isReadyRef.current) {
          sendRef.current?.(detail)
        } else {
          // Queue message for later
          sendQueueRef.current.push(detail)
          console.log('[WebSocketProvider] Queued message, queue length:', sendQueueRef.current.length)
        }
      }
    }
    
    window.addEventListener('tlon:sendMessage', handleSendEvent as EventListener)
    return () => {
      window.removeEventListener('tlon:sendMessage', handleSendEvent as EventListener)
    }
  }, [])

  // Stable send wrapper
  const sendWrapper = useCallback((data: unknown) => {
    console.log('[WebSocketProvider] Sending:', data, 'wsConnected:', wsConnected, 'isReady:', isReadyRef.current)
    if (isReadyRef.current) {
      return sendRef.current?.(data) ?? false
    } else {
      sendQueueRef.current.push(data)
      console.log('[WebSocketProvider] Queued message, queue length:', sendQueueRef.current.length)
      return false
    }
  }, [wsConnected])

  if (connectionError) {
    return (
      <div className="fixed inset-0 bg-dark-bg flex items-center justify-center p-6">
        <div className="bg-dark-surface border border-red-500/30 rounded-xl p-8 max-w-md text-center">
          <div className="text-4xl mb-4">⚠️</div>
          <h2 className="text-xl font-semibold text-white mb-2">Connection Error</h2>
          <p className="text-red-400 mb-4">{connectionError}</p>
          <p className="text-slate-400 text-sm mb-6">
            Please make sure the Tlon server is running on the correct port.
          </p>
          <button 
            onClick={() => window.location.reload()}
            className="btn-primary"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <WebSocketContext.Provider value={{ send: sendWrapper, isConnected: wsConnected && isReady }}>
      {children}
    </WebSocketContext.Provider>
  )
}

function getEventLevel(eventType: string): TlonEvent['level'] {
  if (eventType.includes('error')) return 'error'
  if (eventType.includes('warning')) return 'warn'
  if (eventType.includes('success')) return 'success'
  if (eventType.includes('debug')) return 'debug'
  return 'info'
}

function getEventMessage(eventType: string, payload: Record<string, unknown>): string {
  switch (eventType) {
    case 'message.new':
      return (payload.content as string) || 'New message'
    case 'agent.started':
      return `Agent ${payload.agent_id} started`
    case 'agent.stopped':
      return `Agent ${payload.agent_id} stopped`
    case 'agent.error':
      return `Agent ${payload.agent_id} error: ${payload.error || 'Unknown'}`
    case 'tool.execute':
      return `Executing ${payload.tool}`
    case 'tool.result':
      return `Tool ${payload.tool} completed`
    default:
      return eventType
  }
}
