import { useState, useRef, useEffect } from 'react'
import { Send, Paperclip, AtSign, X, WifiOff, Users, Zap, MessageSquare } from 'lucide-react'
import { useAppStore } from '../stores/appStore'
import { useMessageStore } from '../stores/messageStore'
import MessageBubble from '../components/MessageBubble'

export default function CongressPage() {
  const { username, rightPanelOpen, isConnected } = useAppStore()
  const { messages, agents, addMessage } = useMessageStore()
  const [input, setInput] = useState('')
  const [showMentions, setShowMentions] = useState(false)
  const [mentionSearch, setMentionSearch] = useState('')
  const [selectedMentions, setSelectedMentions] = useState<string[]>([])
  const [sendError, setSendError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = () => {
    if (!input.trim()) return
    if (!isConnected) {
      setSendError('未连接到服务器')
      setTimeout(() => setSendError(null), 3000)
      return
    }

    const message = {
      type: 'message',
      content: input.trim(),
      mentions: selectedMentions,
    }

    // Send via custom event
    const event = new CustomEvent('tlon:sendMessage', { detail: message })
    window.dispatchEvent(event)

    // Optimistically add to local state
    addMessage({
      id: `temp-${Date.now()}`,
      senderId: 'human',
      senderName: username,
      senderType: 'human',
      content: input.trim(),
      timestamp: Date.now(),
      mentions: selectedMentions,
    })

    setInput('')
    setSelectedMentions([])
    setSendError(null)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setInput(value)

    // Check for @ mention
    const lastAtIndex = value.lastIndexOf('@')
    if (lastAtIndex !== -1 && lastAtIndex === value.length - 1) {
      setShowMentions(true)
      setMentionSearch('')
    } else if (lastAtIndex !== -1) {
      const afterAt = value.slice(lastAtIndex + 1)
      if (!afterAt.includes(' ')) {
        setMentionSearch(afterAt.toLowerCase())
      } else {
        setShowMentions(false)
      }
    } else {
      setShowMentions(false)
    }
  }

  const handleMentionSelect = (agentId: string) => {
    const lastAtIndex = input.lastIndexOf('@')
    const newInput = input.slice(0, lastAtIndex) + `@${agentId} `
    setInput(newInput)
    setSelectedMentions([...selectedMentions, agentId])
    setShowMentions(false)
    inputRef.current?.focus()
  }

  const filteredAgents = agents.filter(
    (agent) =>
      agent.name.toLowerCase().includes(mentionSearch) ||
      agent.id.toLowerCase().includes(mentionSearch)
  )

  return (
    <div className="h-full flex">
      {/* ============================================
          CHAT AREA
          ============================================ */}
      <div className="flex-1 flex flex-col min-w-0">
        
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
          <div className="max-w-3xl mx-auto">
            {!isConnected ? (
              <div className="flex flex-col items-center justify-center py-20 text-base-content/50">
                <div className="loading loading-spinner loading-lg text-primary mb-4" />
                <p className="text-lg font-semibold mb-2">连接中...</p>
                <p className="text-body-sm">正在建立 WebSocket 连接</p>
              </div>
            ) : messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-base-content/50">
                <div className="
                  w-20 h-20 rounded-2xl 
                  bg-primary/10 
                  flex items-center justify-center 
                  mb-6
                ">
                  <MessageSquare className="w-10 h-10 text-primary/60" />
                </div>
                <p className="text-xl font-bold mb-3">欢迎来到 Congress</p>
                <p className="text-body-sm text-center max-w-sm mb-6">
                  开始与 Agent 对话，使用 <kbd className="kbd kbd-sm">@</kbd> 提及特定 Agent
                </p>
                <div className="flex flex-wrap justify-center gap-2">
                  {agents.slice(0, 3).map(agent => (
                    <button
                      key={agent.id}
                      onClick={() => handleMentionSelect(agent.id)}
                      className="btn btn-sm btn-ghost gap-1"
                    >
                      <span className="text-primary">@</span>
                      {agent.name}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-1">
                {messages.map((message, index) => (
                  <div 
                    key={message.id} 
                    className="animate-fade-in"
                    style={{ animationDelay: `${Math.min(index * 0.03, 0.3)}s` }}
                  >
                    <MessageBubble message={message} />
                  </div>
                ))}
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input Area */}
        <div className="p-4 border-t border-base-300 bg-base-200/50">
          <div className="max-w-3xl mx-auto">
            
            {/* Error message */}
            {sendError && (
              <div className="alert alert-error alert-sm mb-3">
                <WifiOff className="w-4 h-4 shrink-0" />
                <span className="text-sm">{sendError}</span>
              </div>
            )}

            {/* Selected mentions */}
            {selectedMentions.length > 0 && (
              <div className="flex gap-2 mb-3 flex-wrap">
                {selectedMentions.map((mention) => (
                  <span
                    key={mention}
                    className="badge badge-primary gap-1.5 pl-3"
                  >
                    @{mention}
                    <button
                      onClick={() =>
                        setSelectedMentions(selectedMentions.filter((m) => m !== mention))
                      }
                      className="hover:text-primary-content/70 p-0.5"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {/* Mention dropdown */}
            {showMentions && (
              <div className="
                mb-3 
                bg-base-200 border border-base-300 
                rounded-box shadow-xl 
                overflow-hidden
              ">
                <div className="
                  px-4 py-2 text-xs font-semibold 
                  text-base-content/50 
                  border-b border-base-300
                ">
                  选择 Agent
                </div>
                {filteredAgents.length === 0 ? (
                  <div className="px-4 py-3 text-sm text-base-content/50">
                    未找到 Agent
                  </div>
                ) : (
                  filteredAgents.map((agent) => (
                    <button
                      key={agent.id}
                      onClick={() => handleMentionSelect(agent.id)}
                      className="
                        w-full flex items-center gap-3 
                        px-4 py-2.5 
                        hover:bg-base-300 
                        transition-colors 
                        text-left
                      "
                    >
                      <div className={`
                        w-2 h-2 rounded-full 
                        ${agent.status === 'active' ? 'bg-success' : 'bg-base-content/30'}
                      `} />
                      <span className="text-base-content font-medium">
                        {agent.name}
                      </span>
                      <span className="text-base-content/50 text-sm">
                        ({agent.id})
                      </span>
                      {agent.role && (
                        <span className="badge badge-sm badge-ghost ml-auto">
                          {agent.role}
                        </span>
                      )}
                    </button>
                  ))
                )}
              </div>
            )}

            {/* Input */}
            <div className="flex gap-2">
              <button className="btn btn-ghost btn-square shrink-0">
                <Paperclip className="w-5 h-5" />
              </button>
              
              <div className="flex-1 relative">
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder={isConnected ? "输入消息... 使用 @ 提及 Agent" : "等待连接..."}
                  disabled={!isConnected}
                  className="
                    input input-bordered w-full 
                    pr-12 
                    focus:input-primary 
                    transition-all 
                    disabled:opacity-50
                  "
                />
                <button className="
                  absolute right-3 top-1/2 -translate-y-1/2 
                  btn btn-ghost btn-xs btn-square
                ">
                  <AtSign className="w-4 h-4" />
                </button>
              </div>
              
              <button
                onClick={handleSend}
                disabled={!input.trim() || !isConnected}
                className="btn btn-primary gap-2 shrink-0"
              >
                <Send className="w-4 h-4" />
                <span className="hidden sm:inline">发送</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ============================================
          RIGHT PANEL
          ============================================ */}
      {rightPanelOpen && (
        <aside className="
          w-72 
          bg-base-200 border-l border-base-300 
          p-4 
          hidden xl:block 
          overflow-y-auto
          animate-slide-in-right
        ">
          {/* Session Info */}
          <div className="card bg-base-100 shadow-sm mb-4">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <Zap className="w-4 h-4 text-primary" />
                会话信息
              </h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-base-content/60">模式</span>
                  <span className="badge badge-sm badge-ghost">Chat</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-base-content/60">状态</span>
                  <div className={`
                    flex items-center gap-1.5 
                    ${isConnected ? 'text-success' : 'text-warning'}
                  `}>
                    <span className={`
                      w-2 h-2 rounded-full 
                      ${isConnected ? 'bg-success animate-pulse' : 'bg-warning'}
                    `} />
                    <span className="font-medium">
                      {isConnected ? '已连接' : '连接中...'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Participants */}
          <div className="card bg-base-100 shadow-sm mb-4">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <Users className="w-4 h-4 text-primary" />
                参与者
                <span className="badge badge-sm badge-primary badge-outline ml-auto">
                  {agents.length + 1}
                </span>
              </h3>
              <div className="space-y-1">
                {/* User */}
                <div className="
                  flex items-center gap-2 
                  p-2 rounded-lg 
                  bg-primary/10
                ">
                  <div className="w-2 h-2 rounded-full bg-primary" />
                  <span className="text-sm font-medium flex-1">{username}</span>
                  <span className="badge badge-xs badge-primary">你</span>
                </div>
                
                {/* Agents */}
                {agents.map((agent) => (
                  <button
                    key={agent.id}
                    onClick={() => handleMentionSelect(agent.id)}
                    className="
                      w-full flex items-center gap-2 
                      p-2 rounded-lg 
                      hover:bg-base-200 
                      transition-colors 
                      text-left 
                      group
                    "
                  >
                    <div className={`
                      w-2 h-2 rounded-full 
                      ${agent.status === 'active' ? 'bg-success' : 
                        agent.status === 'warning' ? 'bg-warning' :
                        agent.status === 'error' ? 'bg-error' : 
                        'bg-base-content/30'}
                    `} />
                    <span className="text-sm flex-1">{agent.name}</span>
                    {agent.role && (
                      <span className="
                        badge badge-xs badge-ghost 
                        opacity-0 group-hover:opacity-100 
                        transition-opacity
                      ">
                        {agent.role}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Quick Actions */}
          {agents.some(a => a.status !== 'active') && (
            <div className="card bg-base-100 shadow-sm">
              <div className="card-body p-4">
                <h3 className="card-title text-sm mb-3">快捷操作</h3>
                <div className="flex flex-col gap-2">
                  <button className="btn btn-sm btn-ghost justify-start gap-2">
                    <span>☀️</span>
                    唤醒所有 Agent
                  </button>
                  <button className="btn btn-sm btn-ghost justify-start gap-2 text-error">
                    <span>🗑️</span>
                    清空历史
                  </button>
                </div>
              </div>
            </div>
          )}
        </aside>
      )}
    </div>
  )
}
