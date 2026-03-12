import { useState, useEffect } from 'react'
import { useAppStore } from '../stores/appStore'
import { Server, User, Loader2, AlertCircle, Octagon } from 'lucide-react'

export default function ConnectPage() {
  const { 
    serverUrl, 
    setWsUrl, 
    setRestUrl, 
    username, 
    setUsername, 
    setConnected,
    theme,
    applyTheme,
  } = useAppStore()
  
  const [inputUrl, setInputUrl] = useState(serverUrl)
  const [inputName, setInputName] = useState(username)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  // Initialize theme on mount
  useEffect(() => {
    applyTheme(theme)
  }, [])

  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError('')
    
    try {
      // Parse URL
      const url = inputUrl.trim()
      let wsFullUrl: string
      let restFullUrl: string
      
      if (url.startsWith('ws://') || url.startsWith('wss://')) {
        wsFullUrl = url.includes('/ws') ? url : `${url}/ws`
        const httpUrl = url.replace(/^ws/, 'http').replace(/\/ws$/, '')
        restFullUrl = httpUrl
      } else if (url.startsWith('http://') || url.startsWith('https://')) {
        wsFullUrl = url.replace(/^http/, 'ws') + '/ws'
        restFullUrl = url
      } else {
        // host:port format
        const [host, port] = url.split(':')
        const portNum = port || '8888'
        wsFullUrl = `ws://${host || 'localhost'}:${portNum}/ws`
        restFullUrl = `http://${host || 'localhost'}:${parseInt(portNum) - 80 || 8080}`
      }
      
      // Try to connect to verify server is reachable
      const ws = new WebSocket(wsFullUrl)
      
      const connectionTimeout = setTimeout(() => {
        ws.close()
        setError('连接超时，请检查服务器地址')
        setIsLoading(false)
      }, 10000)
      
      ws.onopen = () => {
        clearTimeout(connectionTimeout)
        
        // Save settings
        setWsUrl(wsFullUrl)
        setRestUrl(restFullUrl)
        if (inputName.trim()) {
          setUsername(inputName.trim())
        }
        
        // Close this verification connection
        ws.close()
        
        // Mark as connected - this triggers navigation to Layout
        setConnected(true)
        setIsLoading(false)
      }
      
      ws.onerror = () => {
        clearTimeout(connectionTimeout)
        setError('无法连接到服务器，请检查地址是否正确')
        setIsLoading(false)
      }
      
      ws.onclose = (event) => {
        clearTimeout(connectionTimeout)
        if (!event.wasClean && isLoading) {
          setError('连接已关闭')
          setIsLoading(false)
        }
      }
    } catch (err) {
      setError('连接失败: ' + (err instanceof Error ? err.message : 'Unknown error'))
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-base-100 relative overflow-hidden">
      {/* ============================================
          BACKGROUND DECORATION
          ============================================ */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -left-1/2 w-full h-full bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute -bottom-1/2 -right-1/2 w-full h-full bg-accent/5 rounded-full blur-3xl" />
      </div>

      {/* ============================================
          MAIN CONTENT
          ============================================ */}
      <div className="w-full max-w-md px-6 relative z-10">
        
        {/* Logo Card */}
        <div className="
          card bg-base-200 border border-base-300 
          shadow-2xl 
          p-8 mb-6 
          animate-fade-in
        ">
          <div className="text-center">
            {/* Logo */}
            <div className="avatar mb-6 inline-block">
              <div className="
                w-24 h-24 rounded-2xl 
                bg-gradient-to-br from-primary to-primary/70 
                shadow-lg shadow-primary/30 
                ring-4 ring-primary/20 
                flex items-center justify-center
              ">
                <Octagon className="w-12 h-12 text-primary-content" strokeWidth={1.5} />
              </div>
            </div>
            
            {/* Brand */}
            <h1 className="text-display mb-2">
              <span className="gradient-text">Tlon</span>
            </h1>
            <p className="text-body text-base-content/60">
              多智能体协作系统
            </p>
          </div>
        </div>

        {/* Form Card */}
        <div className="
          card bg-base-200 border border-base-300 
          shadow-xl 
          p-6 
          animate-fade-in
        " style={{ animationDelay: '0.1s' }}>
          <form onSubmit={handleConnect} className="space-y-5">
            
            {/* Server URL Input */}
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text flex items-center gap-2 text-sm font-medium">
                  <Server className="w-4 h-4 text-base-content/50" />
                  服务器地址
                </span>
              </label>
              <input
                type="text"
                value={inputUrl}
                onChange={(e) => setInputUrl(e.target.value)}
                placeholder="localhost:8888"
                className="
                  input input-bordered w-full 
                  focus:input-primary 
                  transition-all duration-200
                  font-mono text-sm
                "
                disabled={isLoading}
              />
            </div>

            {/* Username Input */}
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text flex items-center gap-2 text-sm font-medium">
                  <User className="w-4 h-4 text-base-content/50" />
                  您的名称
                </span>
              </label>
              <input
                type="text"
                value={inputName}
                onChange={(e) => setInputName(e.target.value)}
                placeholder="Operator"
                className="
                  input input-bordered w-full 
                  focus:input-primary 
                  transition-all duration-200
                "
                disabled={isLoading}
              />
            </div>

            {/* Error Alert */}
            {error && (
              <div className="alert alert-error alert-sm">
                <AlertCircle className="w-5 h-5 shrink-0" />
                <span className="text-sm">{error}</span>
              </div>
            )}

            {/* Connect Button */}
            <button
              type="submit"
              disabled={isLoading}
              className="
                btn btn-primary w-full h-12 
                text-lg font-semibold
                shadow-lg shadow-primary/30
                hover:shadow-xl hover:shadow-primary/40
                transition-all duration-200
                disabled:shadow-none
              "
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  <span>正在连接...</span>
                </>
              ) : (
                <>
                  <span className="mr-2">🚀</span>
                  <span>连接</span>
                </>
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center space-y-1">
          <p className="text-caption text-base-content/40">
            v0.2.0
          </p>
          <p className="text-caption text-base-content/30 italic">
            由观念构建的、正在入侵现实的宇宙
          </p>
        </div>
      </div>
    </div>
  )
}
