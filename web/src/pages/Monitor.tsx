import { useState, useMemo } from 'react'
import { Pause, Play, Download, Trash2, Search, Filter, Activity } from 'lucide-react'
import { useMessageStore } from '../stores/messageStore'
import EventRow from '../components/EventRow'

const eventTypes = [
  'message.new',
  'message.mention',
  'agent.started',
  'agent.stopped',
  'agent.error',
  'agent.thinking',
  'tool.execute',
  'tool.result',
  'system.startup',
  'system.shutdown',
]

const levels = ['debug', 'info', 'warn', 'error', 'success']

const levelLabels: Record<string, string> = {
  debug: '调试',
  info: '信息',
  warn: '警告',
  error: '错误',
  success: '成功',
}

export default function MonitorPage() {
  const { events, isPaused, setPaused, clearEvents, pendingEvents, flushPendingEvents } = useMessageStore()
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [levelFilter, setLevelFilter] = useState<string>('all')
  const [agentFilter, setAgentFilter] = useState<string>('')
  const [searchQuery, setSearchQuery] = useState('')

  const filteredEvents = useMemo(() => {
    return events.filter((event) => {
      if (typeFilter !== 'all' && !event.type.includes(typeFilter)) return false
      if (levelFilter !== 'all' && event.level !== levelFilter) return false
      if (agentFilter && event.agentId !== agentFilter) return false
      if (searchQuery && !event.message.toLowerCase().includes(searchQuery.toLowerCase())) return false
      return true
    })
  }, [events, typeFilter, levelFilter, agentFilter, searchQuery])

  const uniqueAgents = useMemo(() => {
    const agents = new Set<string>()
    events.forEach((e) => {
      if (e.agentId) agents.add(e.agentId)
    })
    return Array.from(agents)
  }, [events])

  const handlePauseToggle = () => {
    if (isPaused) {
      setPaused(false)
      flushPendingEvents()
    } else {
      setPaused(true)
    }
  }

  const handleExport = () => {
    const data = JSON.stringify(filteredEvents, null, 2)
    const blob = new Blob([data], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `tlon-events-${new Date().toISOString()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const eventRate = useMemo(() => {
    const now = Date.now()
    const recentEvents = events.filter((e) => now - e.timestamp < 60000)
    return Math.round(recentEvents.length / 60)
  }, [events])

  // Stats
  const stats = useMemo(() => ({
    total: events.length,
    shown: filteredEvents.length,
    errors: events.filter(e => e.level === 'error').length,
    warnings: events.filter(e => e.level === 'warn').length,
  }), [events, filteredEvents])

  return (
    <div className="h-full flex flex-col">
      
      {/* ============================================
          STATS BAR
          ============================================ */}
      <div className="
        h-14 
        bg-base-200 border-b border-base-300 
        flex items-center px-4 sm:px-6 
        gap-4 sm:gap-6
      ">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-primary" />
          <span className="text-sm text-base-content/60">事件</span>
          <span className="text-lg font-bold">{stats.total}</span>
        </div>
        
        <div className="w-px h-6 bg-base-300 hidden sm:block" />
        
        <div className="hidden sm:flex items-center gap-2">
          <span className="badge badge-error badge-sm">{stats.errors}</span>
          <span className="text-sm text-base-content/60">错误</span>
        </div>
        
        <div className="hidden sm:flex items-center gap-2">
          <span className="badge badge-warning badge-sm">{stats.warnings}</span>
          <span className="text-sm text-base-content/60">警告</span>
        </div>
        
        <div className="flex-1" />
        
        <div className="text-sm text-base-content/50">
          {eventRate} 事件/秒
        </div>
      </div>

      {/* ============================================
          FILTER BAR
          ============================================ */}
      <div className="
        min-h-[56px] 
        bg-base-200/50 border-b border-base-300 
        flex flex-wrap items-center gap-2 
        px-4 sm:px-6 py-2
      ">
        <Filter className="w-4 h-4 text-base-content/50 shrink-0" />
        
        {/* Type Filter */}
        <select 
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="select select-bordered select-sm w-32 sm:w-40"
        >
          <option value="all">所有类型</option>
          {eventTypes.map((type) => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>

        {/* Level Filter */}
        <select 
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value)}
          className="select select-bordered select-sm w-28 sm:w-32"
        >
          <option value="all">所有级别</option>
          {levels.map((level) => (
            <option key={level} value={level}>
              {levelLabels[level]}
            </option>
          ))}
        </select>

        {/* Agent Filter */}
        <select 
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          className="select select-bordered select-sm w-32 sm:w-40"
        >
          <option value="">所有 Agents</option>
          {uniqueAgents.map((agent) => (
            <option key={agent} value={agent}>{agent}</option>
          ))}
        </select>

        <div className="flex-1" />

        {/* Search */}
        <div className="relative w-full sm:w-auto mt-2 sm:mt-0">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-base-content/40" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索事件..."
            className="input input-bordered input-sm pl-9 w-full sm:w-48"
          />
        </div>

        {/* Actions */}
        <button 
          onClick={handlePauseToggle}
          className={`btn btn-sm ${isPaused ? 'btn-warning' : 'btn-ghost'}`}
        >
          {isPaused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
          <span className="hidden sm:inline ml-1.5">
            {isPaused ? '继续' : '暂停'}
          </span>
          {isPaused && pendingEvents.length > 0 && (
            <span className="badge badge-sm badge-primary ml-1.5">
              {pendingEvents.length}
            </span>
          )}
        </button>
        
        <button 
          onClick={handleExport}
          className="btn btn-sm btn-ghost"
          title="导出"
        >
          <Download className="w-4 h-4 sm:mr-1.5" />
          <span className="hidden sm:inline">导出</span>
        </button>
        
        <button 
          onClick={clearEvents}
          className="btn btn-sm btn-ghost text-error hover:bg-error/10"
          title="清空"
        >
          <Trash2 className="w-4 h-4 sm:mr-1.5" />
          <span className="hidden sm:inline">清空</span>
        </button>
      </div>

      {/* ============================================
          EVENT TABLE
          ============================================ */}
      <div className="flex-1 overflow-auto">
        <table className="table table-zebra table-sm">
          <thead className="bg-base-200 sticky top-0 z-10">
            <tr className="text-left text-base-content/60 text-xs uppercase tracking-wider">
              <th className="w-32 font-medium py-3">时间</th>
              <th className="w-20 font-medium py-3">级别</th>
              <th className="w-36 font-medium py-3">类型</th>
              <th className="w-28 font-medium py-3">Agent</th>
              <th className="font-medium py-3">消息</th>
            </tr>
          </thead>
          <tbody>
            {filteredEvents.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center py-16">
                  {events.length === 0 ? (
                    <div className="flex flex-col items-center text-base-content/40">
                      <div className="
                        w-16 h-16 rounded-2xl 
                        bg-base-200 
                        flex items-center justify-center 
                        mb-4
                      ">
                        <Activity className="w-8 h-8" />
                      </div>
                      <p className="text-lg font-medium mb-1">暂无事件</p>
                      <p className="text-sm">Agent 开始工作时，事件将显示在这里</p>
                    </div>
                  ) : (
                    <p className="text-base-content/40">没有匹配的事件</p>
                  )}
                </td>
              </tr>
            ) : (
              filteredEvents.map((event) => (
                <EventRow key={event.id} event={event} />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* ============================================
          FOOTER
          ============================================ */}
      <div className="
        h-10 
        bg-base-200/50 border-t border-base-300 
        flex items-center px-4 sm:px-6 
        text-xs text-base-content/50
      ">
        <span className="mr-4">显示 {filteredEvents.length} 条</span>
        <span className="mr-4">总计 {events.length} 条</span>
        {isPaused && (
          <span className="ml-auto text-warning flex items-center gap-1.5">
            <Pause className="w-3 h-3" />
            已暂停 ({pendingEvents.length} 条待处理)
          </span>
        )}
      </div>
    </div>
  )
}
