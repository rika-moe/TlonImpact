import { useEffect, useRef, useState, useCallback } from 'react'

interface WebSocketState {
  isConnected: boolean
  isConnecting: boolean
  error: string | null
}

interface UseWebSocketOptions {
  url: string
  onOpen?: () => void
  onClose?: () => void
  onError?: (error: Event) => void
  onMessage?: (data: unknown) => void
  reconnect?: boolean
  reconnectInterval?: number
  maxReconnectAttempts?: number
}

export function useWebSocket(options: UseWebSocketOptions) {
  const {
    url,
    onOpen,
    onClose,
    onError,
    onMessage,
    reconnect = true,
    reconnectInterval = 3000,
    maxReconnectAttempts = 5,
  } = options

  const [state, setState] = useState<WebSocketState>({
    isConnected: false,
    isConnecting: false,
    error: null,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isManualCloseRef = useRef(false)
  const isConnectingRef = useRef(false)
  const mountedRef = useRef(false)
  
  // Store callbacks in refs to avoid dependency issues
  const callbacksRef = useRef({
    onOpen,
    onClose,
    onError,
    onMessage,
  })
  
  // Update refs when callbacks change
  useEffect(() => {
    callbacksRef.current = { onOpen, onClose, onError, onMessage }
  }, [onOpen, onClose, onError, onMessage])

  // Stable send function
  const send = useCallback((data: unknown) => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(typeof data === 'string' ? data : JSON.stringify(data))
        return true
      } catch (err) {
        console.error('[WebSocket] Send error:', err)
        return false
      }
    }
    console.warn('[WebSocket] Cannot send - not connected, readyState:', ws?.readyState)
    return false
  }, [])
  
  // Connect function
  const connect = useCallback(() => {
    if (isConnectingRef.current) {
      console.log('[useWebSocket] Already connecting, skipping...')
      return
    }
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      console.log('[useWebSocket] Already connected, skipping...')
      return
    }
    
    if (!url) {
      console.warn('[useWebSocket] No URL provided')
      return
    }
    
    // Don't connect if component is not mounted
    if (!mountedRef.current) {
      console.log('[useWebSocket] Not mounted yet, delaying connect...')
      return
    }
    
    isConnectingRef.current = true
    isManualCloseRef.current = false

    setState((prev) => ({ ...prev, isConnecting: true, error: null }))

    console.log('[useWebSocket] Connecting to:', url)

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        console.log('[useWebSocket] Connected')
        isConnectingRef.current = false
        reconnectAttemptsRef.current = 0
        setState({
          isConnected: true,
          isConnecting: false,
          error: null,
        })
        callbacksRef.current.onOpen?.()
      }

      ws.onclose = (event) => {
        console.log('[useWebSocket] Connection closed:', event.code, event.reason)
        isConnectingRef.current = false
        wsRef.current = null
        
        setState((prev) => ({
          ...prev,
          isConnected: false,
          isConnecting: false,
        }))
        callbacksRef.current.onClose?.()

        // Auto reconnect (only if not manually closed and still mounted)
        if (reconnect && !isManualCloseRef.current && mountedRef.current) {
          if (reconnectAttemptsRef.current < maxReconnectAttempts) {
            reconnectAttemptsRef.current++
            console.log(`[useWebSocket] Reconnecting in ${reconnectInterval}ms... (attempt ${reconnectAttemptsRef.current})`)
            reconnectTimerRef.current = setTimeout(() => {
              if (mountedRef.current) {
                connect()
              }
            }, reconnectInterval)
          } else {
            setState((prev) => ({
              ...prev,
              error: 'Max reconnection attempts reached',
            }))
          }
        }
      }

      ws.onerror = (error) => {
        console.error('[useWebSocket] Connection error:', error)
        isConnectingRef.current = false
        setState((prev) => ({
          ...prev,
          error: `Connection failed to ${url}`,
        }))
        callbacksRef.current.onError?.(error)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          callbacksRef.current.onMessage?.(data)
        } catch {
          callbacksRef.current.onMessage?.(event.data)
        }
      }
    } catch (error) {
      console.error('[useWebSocket] Failed to create connection:', error)
      isConnectingRef.current = false
      setState({
        isConnected: false,
        isConnecting: false,
        error: 'Failed to create connection',
      })
    }
  }, [url, reconnect, reconnectInterval, maxReconnectAttempts])

  // Disconnect function
  const disconnect = useCallback(() => {
    console.log('[useWebSocket] Disconnecting...')
    isManualCloseRef.current = true
    
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }, [])

  // Handle mount/unmount
  useEffect(() => {
    mountedRef.current = true
    console.log('[useWebSocket] Mounted')
    
    return () => {
      console.log('[useWebSocket] Unmounting...')
      mountedRef.current = false
      disconnect()
    }
  }, [disconnect])

  // Handle URL changes
  useEffect(() => {
    if (!url || !mountedRef.current) return
    
    console.log('[useWebSocket] URL changed to:', url)
    
    // Small delay to handle React StrictMode double-mount
    const timer = setTimeout(() => {
      if (mountedRef.current) {
        connect()
      }
    }, 100)
    
    return () => {
      clearTimeout(timer)
    }
  }, [url, connect])

  return {
    ...state,
    connect,
    disconnect,
    send,
  }
}
