import { Play, Square, RotateCcw, Settings, BarChart3, Cpu, HardDrive } from 'lucide-react'
import type { Agent } from '../types'

interface AgentCardProps {
  agent: Agent
  onWake: (id: string) => void
  onSleep: (id: string) => void
  onRestart: (id: string) => void
  onViewDetails: (agent: Agent) => void
}

const statusConfig: Record<string, { 
  badge: string
  label: string
  ringColor: string
  progressColor: string
}> = {
  active: { 
    badge: 'badge-success', 
    label: '运行中',
    ringColor: 'ring-success',
    progressColor: 'progress-success',
  },
  idle: { 
    badge: 'badge-ghost', 
    label: '空闲',
    ringColor: 'ring-base-content/20',
    progressColor: 'progress-primary',
  },
  warning: { 
    badge: 'badge-warning', 
    label: '警告',
    ringColor: 'ring-warning',
    progressColor: 'progress-warning',
  },
  error: { 
    badge: 'badge-error', 
    label: '错误',
    ringColor: 'ring-error',
    progressColor: 'progress-error',
  },
}

const roleIcons: Record<string, string> = {
  Developer: '💻',
  PM: '📊',
  Ops: '🔧',
  Product: '📝',
  'Data Analyst': '📈',
}

export default function AgentCard({ 
  agent, 
  onWake, 
  onSleep, 
  onRestart, 
  onViewDetails 
}: AgentCardProps) {
  const status = statusConfig[agent.status] || statusConfig.idle
  const roleIcon = (agent.role && roleIcons[agent.role]) || '🤖'
  
  // Calculate resource usage for progress bar
  const cpuValue = agent.cpu ? parseInt(agent.cpu) : 0

  return (
    <div className="
      card bg-base-200 border border-base-300 
      hover:shadow-xl hover:border-primary/30 
      hover:-translate-y-0.5
      transition-all duration-300 
      group
    ">
      <div className="card-body p-5">
        
        {/* ============================================
            HEADER: Avatar + Name + Status
            ============================================ */}
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            {/* Avatar with status pulse */}
            <div className={`avatar ${agent.status === 'active' ? 'status-pulse' : ''}`}>
              <div className={`
                w-12 h-12 rounded-xl 
                bg-gradient-to-br from-base-300 to-base-100 
                flex items-center justify-center text-2xl
                ring-2 ring-offset-2 ring-offset-base-200
                ${status.ringColor}
              `}>
                {roleIcon}
              </div>
            </div>
            
            {/* Name & Status */}
            <div>
              <h3 className="font-bold text-lg leading-tight">{agent.name}</h3>
              <div className="flex items-center gap-2 mt-0.5">
                <span className={`badge badge-sm ${status.badge}`}>
                  {status.label}
                </span>
                {agent.role && (
                  <span className="text-sm text-base-content/50">{agent.role}</span>
                )}
              </div>
            </div>
          </div>
          
          {/* ID Badge */}
          <span className="
            text-xs font-mono 
            text-base-content/30 
            bg-base-300/50 
            px-2 py-1 rounded
          ">
            {agent.id}
          </span>
        </div>

        {/* ============================================
            STATS GRID
            ============================================ */}
        <div className="grid grid-cols-2 gap-3 mb-4">
          {/* CPU */}
          <div className="bg-base-100 rounded-lg p-3 flex items-center gap-3">
            <div className="
              w-9 h-9 rounded-lg 
              bg-primary/10 
              flex items-center justify-center
            ">
              <Cpu className="w-4 h-4 text-primary" />
            </div>
            <div>
              <p className="text-xs text-base-content/50 mb-0.5">CPU</p>
              <p className={`
                font-mono font-bold text-sm
                ${agent.status === 'warning' ? 'text-warning' : 'text-base-content'}
              `}>
                {agent.cpu || '-'}
              </p>
            </div>
          </div>
          
          {/* Memory */}
          <div className="bg-base-100 rounded-lg p-3 flex items-center gap-3">
            <div className="
              w-9 h-9 rounded-lg 
              bg-secondary/10 
              flex items-center justify-center
            ">
              <HardDrive className="w-4 h-4 text-secondary" />
            </div>
            <div>
              <p className="text-xs text-base-content/50 mb-0.5">内存</p>
              <p className="font-mono font-bold text-sm text-base-content">
                {agent.mem || '-'}
              </p>
            </div>
          </div>
        </div>

        {/* ============================================
            RESOURCE PROGRESS
            ============================================ */}
        <div className="mb-4">
          <div className="flex justify-between text-xs text-base-content/50 mb-1.5">
            <span>资源使用</span>
            <span>{agent.status === 'active' ? '正常' : status.label}</span>
          </div>
          <progress 
            className={`progress progress-sm w-full ${status.progressColor}`}
            value={cpuValue || (agent.status === 'active' ? 65 : 20)}
            max="100"
          />
        </div>

        {/* ============================================
            ACTION BUTTONS
            ============================================ */}
        <div className="flex gap-2">
          {agent.status === 'active' ? (
            <button 
              onClick={() => onSleep(agent.id)}
              className="btn btn-sm btn-warning flex-1 gap-1.5"
              title="休眠"
            >
              <Square className="w-4 h-4" />
              <span className="hidden sm:inline">休眠</span>
            </button>
          ) : (
            <button 
              onClick={() => onWake(agent.id)}
              className="btn btn-sm btn-success flex-1 gap-1.5"
              title="唤醒"
            >
              <Play className="w-4 h-4" />
              <span className="hidden sm:inline">唤醒</span>
            </button>
          )}
          
          <button 
            onClick={() => onRestart(agent.id)}
            className="btn btn-sm btn-ghost"
            title="重启"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
          
          <button 
            onClick={() => onViewDetails(agent)}
            className="btn btn-sm btn-ghost"
            title="设置"
          >
            <Settings className="w-4 h-4" />
          </button>
          
          <button 
            className="btn btn-sm btn-ghost opacity-50 cursor-not-allowed"
            title="指标（开发中）"
          >
            <BarChart3 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
