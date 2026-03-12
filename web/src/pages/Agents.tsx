import { useState, useEffect } from 'react'
import { Plus, Bot, Zap, AlertTriangle, AlertCircle } from 'lucide-react'
import { useMessageStore } from '../stores/messageStore'
import { useAppStore } from '../stores/appStore'
import AgentCard from '../components/AgentCard'
import AgentDetailDrawer from '../components/AgentDetailDrawer'
import type { Agent } from '../types'

export default function AgentsPage() {
  const { agents, updateAgentStatus } = useMessageStore()
  const { isConnected } = useAppStore()
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [requestSent, setRequestSent] = useState(false)

  // Request agents list when connected
  useEffect(() => {
    if (isConnected && !requestSent) {
      console.log('[Agents] Connected, sending mode request...')
      
      const event = new CustomEvent('tlon:sendMessage', { 
        detail: { type: 'mode', mode: 'agent' } 
      })
      window.dispatchEvent(event)
      
      setRequestSent(true)
      setIsLoading(false)
    }
    
    if (!isConnected) {
      setRequestSent(false)
      setIsLoading(true)
    }
  }, [isConnected, requestSent])

  useEffect(() => {
    if (agents.length > 0) {
      setIsLoading(false)
    }
  }, [agents])

  const handleWake = (id: string) => {
    updateAgentStatus(id, 'active')
    const event = new CustomEvent('tlon:sendMessage', { 
      detail: { type: 'wake', agent_id: id } 
    })
    window.dispatchEvent(event)
  }

  const handleSleep = (id: string) => {
    updateAgentStatus(id, 'idle')
    const event = new CustomEvent('tlon:sendMessage', { 
      detail: { type: 'stop', agent_id: id } 
    })
    window.dispatchEvent(event)
  }

  const handleRestart = (id: string) => {
    updateAgentStatus(id, 'idle')
    setTimeout(() => {
      updateAgentStatus(id, 'active')
      const event = new CustomEvent('tlon:sendMessage', { 
        detail: { type: 'wake', agent_id: id } 
      })
      window.dispatchEvent(event)
    }, 1000)
  }

  const handleViewDetails = (agent: Agent) => {
    setSelectedAgent(agent)
  }

  // Stats
  const stats = {
    total: agents.length,
    active: agents.filter((a) => a.status === 'active').length,
    warning: agents.filter((a) => a.status === 'warning').length,
    error: agents.filter((a) => a.status === 'error').length,
  }

  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="max-w-6xl mx-auto">
        
        {/* ============================================
            HEADER
            ============================================ */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <h2 className="text-title flex items-center gap-2 mb-1">
              <Bot className="w-6 h-6 text-primary" />
              Agent 管理
            </h2>
            <p className="text-body-sm text-base-content/60">
              管理您的 AI 工作团队
            </p>
          </div>
          <button className="btn btn-primary gap-2 shrink-0">
            <Plus className="w-4 h-4" />
            添加 Agent
          </button>
        </div>

        {/* ============================================
            STATS OVERVIEW
            ============================================ */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-8">
          {/* Total */}
          <div className="card bg-base-200 border-base-300 shadow-sm">
            <div className="card-body p-4">
              <div className="flex items-center gap-3">
                <div className="
                  w-10 h-10 rounded-xl 
                  bg-primary/10 
                  flex items-center justify-center
                ">
                  <Bot className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <p className="text-caption text-base-content/50 uppercase">总计</p>
                  <p className="text-2xl font-bold leading-none mt-0.5">{stats.total}</p>
                </div>
              </div>
            </div>
          </div>

          {/* Active */}
          <div className="card bg-base-200 border-success/30 shadow-sm">
            <div className="card-body p-4">
              <div className="flex items-center gap-3">
                <div className="
                  w-10 h-10 rounded-xl 
                  bg-success/10 
                  flex items-center justify-center
                ">
                  <Zap className="w-5 h-5 text-success" />
                </div>
                <div>
                  <p className="text-caption text-base-content/50 uppercase">运行中</p>
                  <p className="text-2xl font-bold text-success leading-none mt-0.5">
                    {stats.active}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Warning */}
          <div className="card bg-base-200 border-warning/30 shadow-sm">
            <div className="card-body p-4">
              <div className="flex items-center gap-3">
                <div className="
                  w-10 h-10 rounded-xl 
                  bg-warning/10 
                  flex items-center justify-center
                ">
                  <AlertTriangle className="w-5 h-5 text-warning" />
                </div>
                <div>
                  <p className="text-caption text-base-content/50 uppercase">警告</p>
                  <p className="text-2xl font-bold text-warning leading-none mt-0.5">
                    {stats.warning}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Error */}
          <div className="card bg-base-200 border-error/30 shadow-sm">
            <div className="card-body p-4">
              <div className="flex items-center gap-3">
                <div className="
                  w-10 h-10 rounded-xl 
                  bg-error/10 
                  flex items-center justify-center
                ">
                  <AlertCircle className="w-5 h-5 text-error" />
                </div>
                <div>
                  <p className="text-caption text-base-content/50 uppercase">错误</p>
                  <p className="text-2xl font-bold text-error leading-none mt-0.5">
                    {stats.error}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ============================================
            AGENT GRID
            ============================================ */}
        <div className="card-grid">
          {agents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              onWake={handleWake}
              onSleep={handleSleep}
              onRestart={handleRestart}
              onViewDetails={handleViewDetails}
            />
          ))}

          {/* Add New Card */}
          <button className="
            card bg-base-200 border-base-300 border-dashed 
            hover:border-primary hover:bg-primary/5 
            transition-all duration-300 
            flex flex-col items-center justify-center 
            min-h-[260px]
            group
          ">
            <div className="
              w-16 h-16 rounded-2xl 
              bg-base-300 
              group-hover:bg-primary/20 
              flex items-center justify-center 
              mb-4 
              transition-all duration-300
            ">
              <Plus className="
                w-8 h-8 
                text-base-content/50 
                group-hover:text-primary 
                transition-colors duration-300
              " />
            </div>
            <span className="font-semibold text-base-content/70 group-hover:text-primary transition-colors">
              添加新 Agent
            </span>
            <span className="text-sm text-base-content/40 mt-1">
              配置一个新的 AI Agent
            </span>
          </button>
        </div>

        {/* ============================================
            EMPTY STATE
            ============================================ */}
        {agents.length === 0 && !isLoading && (
          <div className="text-center py-16">
            <div className="
              w-24 h-24 rounded-2xl 
              bg-base-200 
              flex items-center justify-center 
              mx-auto mb-6
            ">
              <Bot className="w-12 h-12 text-base-content/30" />
            </div>
            <h3 className="text-xl font-bold text-base-content mb-2">
              还没有 Agent
            </h3>
            <p className="text-body-sm text-base-content/50 mb-6">
              开始添加您的第一个 AI Agent
            </p>
            <button className="btn btn-primary gap-2">
              <Plus className="w-4 h-4" />
              添加 Agent
            </button>
          </div>
        )}
        
        {/* Loading State */}
        {agents.length === 0 && isLoading && (
          <div className="text-center py-16">
            <div className="loading loading-spinner loading-lg text-primary mb-4" />
            <p className="text-body text-base-content/60">
              正在加载 Agents...
            </p>
          </div>
        )}
      </div>

      {/* Detail Drawer */}
      {selectedAgent && (
        <AgentDetailDrawer
          agent={selectedAgent}
          onClose={() => setSelectedAgent(null)}
          onWake={handleWake}
          onSleep={handleSleep}
          onRestart={handleRestart}
        />
      )}
    </div>
  )
}
