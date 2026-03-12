import { 
  X, Play, Square, RotateCcw, Terminal, Cpu, HardDrive, 
  Clock, Tag, CheckCircle2, Circle, Loader2 
} from 'lucide-react'
import type { Agent } from '../types'

interface AgentDetailDrawerProps {
  agent: Agent | null
  onClose: () => void
  onWake: (id: string) => void
  onSleep: (id: string) => void
  onRestart: (id: string) => void
}

const statusConfig: Record<string, { badge: string; label: string }> = {
  active: { badge: 'badge-success', label: '运行中' },
  idle: { badge: 'badge-ghost', label: '空闲' },
  warning: { badge: 'badge-warning', label: '警告' },
  error: { badge: 'badge-error', label: '错误' },
}

// Mock data
const mockSkills = ['git-operations', 'file-operations', 'code-review']
const mockTasks = [
  { id: 1, name: 'Review PR ', status: 'completed', time: '10分钟前' },
  { id: 2, name: '生成报告', status: 'in_progress', time: '1小时前' },
  { id: 3, name: '修复 bug ', status: 'pending', time: '2小时前' },
]

const taskStatusIcons: Record<string, { icon: React.ReactNode; color: string }> = {
  completed: { 
    icon: <CheckCircle2 className="w-4 h-4" />, 
    color: 'text-success' 
  },
  in_progress: { 
    icon: <Loader2 className="w-4 h-4 animate-spin" />, 
    color: 'text-primary' 
  },
  pending: { 
    icon: <Circle className="w-4 h-4" />, 
    color: 'text-base-content/30' 
  },
}

export default function AgentDetailDrawer({ 
  agent, 
  onClose, 
  onWake, 
  onSleep, 
  onRestart 
}: AgentDetailDrawerProps) {
  if (!agent) return null

  const status = statusConfig[agent.status] || statusConfig.idle
  const cpuValue = agent.cpu ? parseInt(agent.cpu) : 0

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40"
        onClick={onClose}
      />
      
      {/* Drawer */}
      <div className="
        fixed right-0 top-0 bottom-0 
        w-80 sm:w-96 
        bg-base-200 border-l border-base-300 
        z-50 flex flex-col 
        animate-slide-in-right 
        shadow-2xl
      ">
        {/* Header */}
        <header className="
          h-16 
          bg-base-100 border-b border-base-300 
          flex items-center justify-between 
          px-5
        ">
          <div className="flex items-center gap-3">
            <div className="avatar">
              <div className="
                w-10 h-10 rounded-xl 
                bg-gradient-to-br from-primary to-primary/70 
                flex items-center justify-center text-xl 
                ring-2 ring-primary/20
              ">
                {agent.role === 'Developer' ? '💻' : 
                 agent.role === 'PM' ? '📊' : 
                 agent.role === 'Ops' ? '🔧' : '🤖'}
              </div>
            </div>
            <div>
              <h2 className="font-bold text-base-content">{agent.name}</h2>
              <p className="text-xs text-base-content/50 font-mono">{agent.id}</p>
            </div>
          </div>
          
          <button 
            onClick={onClose}
            className="btn btn-ghost btn-sm btn-square"
          >
            <X className="w-5 h-5" />
          </button>
        </header>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          
          {/* Status Card */}
          <div className="card bg-base-100 shadow-sm">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <div className={`
                  w-2 h-2 rounded-full 
                  ${agent.status === 'active' ? 'bg-success animate-pulse' : 'bg-base-content/30'}
                `} />
                状态
              </h3>
              
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-base-content/60">状态</span>
                  <span className={`badge ${status.badge}`}>
                    {status.label}
                  </span>
                </div>
                
                <div className="flex items-center justify-between">
                  <span className="text-base-content/60">岗位</span>
                  <span className="text-base-content font-medium">
                    {agent.role || '-'}
                  </span>
                </div>
                
                <div className="flex items-center justify-between">
                  <span className="text-base-content/60">容器</span>
                  <span className="font-mono text-xs text-base-content/50">
                    tlon-agent-{agent.id}
                  </span>
                </div>
                
                <div className="flex items-center justify-between">
                  <span className="text-base-content/60">启动时间</span>
                  <span className="text-base-content/70 text-sm">
                    2024-03-10 14:23
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Resources Card */}
          <div className="card bg-base-100 shadow-sm">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <Cpu className="w-4 h-4 text-primary" />
                资源使用
              </h3>
              
              <div className="space-y-4">
                {/* CPU */}
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-base-content/60 flex items-center gap-1.5">
                      <Cpu className="w-3.5 h-3.5" />
                      CPU
                    </span>
                    <span className="text-sm font-mono font-bold">
                      {agent.cpu || '0%'}
                    </span>
                  </div>
                  <progress 
                    className={`
                      progress progress-sm w-full 
                      ${cpuValue > 50 ? 'progress-warning' : 'progress-success'}
                    `}
                    value={cpuValue}
                    max="100"
                  />
                </div>
                
                {/* Memory */}
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-base-content/60 flex items-center gap-1.5">
                      <HardDrive className="w-3.5 h-3.5" />
                      内存
                    </span>
                    <span className="text-sm font-mono font-bold">
                      {agent.mem || '0MB'}
                    </span>
                  </div>
                  <progress 
                    className="progress progress-sm progress-primary w-full"
                    value={50}
                    max="100"
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Skills Card */}
          <div className="card bg-base-100 shadow-sm">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <Tag className="w-4 h-4 text-primary" />
                技能
              </h3>
              
              <div className="flex flex-wrap gap-2">
                {mockSkills.map((skill) => (
                  <span 
                    key={skill}
                    className="
                      badge badge-ghost 
                      cursor-default 
                      hover:badge-primary 
                      transition-colors
                    "
                  >
                    {skill}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Tasks Card */}
          <div className="card bg-base-100 shadow-sm">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <CheckCircle2 className="w-4 h-4 text-primary" />
                最近任务
              </h3>
              
              <div className="space-y-2">
                {mockTasks.map((task) => {
                  const statusConfig = taskStatusIcons[task.status]
                  return (
                    <div 
                      key={task.id}
                      className="
                        flex items-center justify-between 
                        p-3 bg-base-200 rounded-lg 
                        group hover:bg-base-300 
                        transition-colors
                      "
                    >
                      <div className="flex items-center gap-2">
                        <span className={statusConfig.color}>
                          {statusConfig.icon}
                        </span>
                        <span className="text-sm text-base-content">
                          {task.name}
                        </span>
                      </div>
                      <span className="
                        text-xs text-base-content/40 
                        flex items-center gap-1
                      ">
                        <Clock className="w-3 h-3" />
                        {task.time}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>

          {/* Logs Card */}
          <div className="card bg-base-100 shadow-sm">
            <div className="card-body p-4">
              <h3 className="card-title text-sm flex items-center gap-2 mb-3">
                <Terminal className="w-4 h-4 text-primary" />
                最近日志
              </h3>
              
              <div className="
                bg-base-300 rounded-lg p-3 
                font-mono text-xs 
                text-base-content/60 
                space-y-1 max-h-32 
                overflow-y-auto
              ">
                <p>
                  <span className="text-base-content/40">[14:32:04]</span> Agent 初始化完成
                </p>
                <p>
                  <span className="text-base-content/40">[14:32:05]</span> 正在加载技能...
                </p>
                <p>
                  <span className="text-base-content/40">[14:32:06]</span> 准备就绪
                </p>
                <p className="text-success">
                  <span className="text-base-content/40">[14:32:10]</span> 任务执行成功
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <footer className="
          p-5 border-t border-base-300 
          space-y-2 bg-base-100
        ">
          <div className="flex gap-2">
            {agent.status === 'active' ? (
              <button 
                onClick={() => onSleep(agent.id)}
                className="flex-1 btn btn-warning gap-2"
              >
                <Square className="w-4 h-4" />
                休眠
              </button>
            ) : (
              <button 
                onClick={() => onWake(agent.id)}
                className="flex-1 btn btn-success gap-2"
              >
                <Play className="w-4 h-4" />
                唤醒
              </button>
            )}
            
            <button 
              onClick={() => onRestart(agent.id)}
              className="flex-1 btn btn-ghost gap-2"
            >
              <RotateCcw className="w-4 h-4" />
              重启
            </button>
          </div>
          
          <button className="w-full btn btn-ghost btn-sm gap-2">
            <Terminal className="w-4 h-4" />
            查看完整日志
          </button>
        </footer>
      </div>
    </>
  )
}
