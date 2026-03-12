export function testWebSocket(url: string): Promise<{ success: boolean; error?: string }> {
  return new Promise((resolve) => {
    console.log('[TestWS] Testing connection to:', url)
    
    try {
      const ws = new WebSocket(url)
      const timeout = setTimeout(() => {
        ws.close()
        resolve({ success: false, error: 'Timeout' })
      }, 5000)
      
      ws.onopen = () => {
        clearTimeout(timeout)
        console.log('[TestWS] Connection successful!')
        ws.close()
        resolve({ success: true })
      }
      
      ws.onerror = (e) => {
        clearTimeout(timeout)
        console.error('[TestWS] Connection failed:', e)
        resolve({ success: false, error: 'Connection error' })
      }
      
      ws.onclose = (e) => {
        clearTimeout(timeout)
        if (!e.wasClean) {
          console.log('[TestWS] Connection closed unexpectedly:', e.code, e.reason)
        }
      }
    } catch (err) {
      resolve({ success: false, error: String(err) })
    }
  })
}
