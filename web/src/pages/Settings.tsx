import { useState } from 'react'
import { 
  Palette, 
  Globe, 
  Bell, 
  Database, 
  Shield,
  Moon,
  Sun,
  Monitor,
  Check,
  AlertTriangle,
  Type,
  LayoutGrid,
  HardDrive,
  Download,
  Trash2
} from 'lucide-react'
import { useAppStore } from '../stores/appStore'

type Theme = 'dark' | 'light' | 'system'

const themes: { value: Theme; label: string; icon: typeof Moon }[] = [
  { value: 'dark', label: '深色', icon: Moon },
  { value: 'light', label: '浅色', icon: Sun },
  { value: 'system', label: '跟随系统', icon: Monitor },
]

const fontSizes = [
  { value: 'small', label: '小', size: '14px' },
  { value: 'medium', label: '中', size: '16px' },
  { value: 'large', label: '大', size: '18px' },
]

const densities = [
  { value: 'compact', label: '紧凑', desc: '更小的间距' },
  { value: 'normal', label: '标准', desc: '默认间距' },
  { value: 'comfortable', label: '舒适', desc: '更大的间距' },
]

export default function SettingsPage() {
  const { 
    theme, 
    setTheme, 
    serverUrl, 
    wsUrl,
    setServerUrl,
    setWsUrl,
    username,
    setUsername
  } = useAppStore()
  
  const [activeSection, setActiveSection] = useState('appearance')
  const [saved, setSaved] = useState(false)
  const [fontSize, setFontSize] = useState('medium')
  const [density, setDensity] = useState('normal')
  const [notifications, setNotifications] = useState({
    desktop: true,
    sound: false,
    mentionsOnly: false,
  })
  const [autoReconnect, setAutoReconnect] = useState(true)

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const handleReset = () => {
    setTheme('dark')
    setFontSize('medium')
    setDensity('normal')
    setNotifications({ desktop: true, sound: false, mentionsOnly: false })
    setAutoReconnect(true)
  }

  const sections = [
    { id: 'appearance', label: '外观', icon: Palette },
    { id: 'connection', label: '连接', icon: Globe },
    { id: 'notifications', label: '通知', icon: Bell },
    { id: 'storage', label: '存储', icon: Database },
    { id: 'advanced', label: '高级', icon: Shield },
  ]

  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <h2 className="text-title flex items-center gap-2 mb-6">
          <Shield className="w-6 h-6 text-primary" />
          设置
        </h2>

        <div className="grid grid-cols-1 lg:grid-cols-[180px_1fr] gap-6">
          
          {/* ============================================
              SIDEBAR NAVIGATION
              ============================================ */}
          <nav className="space-y-1">
            {sections.map((section) => (
              <button
                key={section.id}
                onClick={() => setActiveSection(section.id)}
                className={`
                  w-full flex items-center gap-3 
                  px-4 py-2.5 rounded-lg 
                  text-left 
                  transition-all duration-200
                  ${activeSection === section.id
                    ? 'bg-primary text-primary-content shadow-lg shadow-primary/30'
                    : 'text-base-content/60 hover:text-base-content hover:bg-base-200'
                  }
                `}
              >
                <section.icon className="w-5 h-5" />
                <span className="font-medium">{section.label}</span>
              </button>
            ))}
          </nav>

          {/* ============================================
              CONTENT
              ============================================ */}
          <div className="space-y-6">
            
            {/* APPEARANCE SECTION */}
            {activeSection === 'appearance' && (
              <div className="space-y-5">
                {/* Theme */}
                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="
                        w-10 h-10 rounded-xl 
                        bg-primary/10 
                        flex items-center justify-center
                      ">
                        <Palette className="w-5 h-5 text-primary" />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg">主题</h3>
                        <p className="text-body-sm text-base-content/60">
                          选择您喜欢的界面风格
                        </p>
                      </div>
                    </div>
                    
                    <div className="grid grid-cols-3 gap-3">
                      {themes.map(({ value, label, icon: Icon }) => (
                        <button
                          key={value}
                          onClick={() => setTheme(value)}
                          className={`
                            p-4 rounded-xl border-2 
                            transition-all duration-200
                            ${theme === value
                              ? 'border-primary bg-primary/10'
                              : 'border-base-300 hover:border-primary/50 hover:bg-base-300'
                            }
                          `}
                        >
                          <Icon className={`
                            w-6 h-6 mx-auto mb-2 
                            ${theme === value ? 'text-primary' : 'text-base-content/50'}
                          `} />
                          <span className={`
                            text-sm font-medium
                            ${theme === value ? 'text-base-content' : 'text-base-content/60'}
                          `}>
                            {label}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Font Size */}
                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="
                        w-10 h-10 rounded-xl 
                        bg-secondary/10 
                        flex items-center justify-center
                      ">
                        <Type className="w-5 h-5 text-secondary" />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg">字体大小</h3>
                        <p className="text-body-sm text-base-content/60">
                          调整界面文字大小
                        </p>
                      </div>
                    </div>
                    
                    <div className="space-y-2">
                      {fontSizes.map(({ value, label, size }) => (
                        <button
                          key={value}
                          onClick={() => setFontSize(value)}
                          className={`
                            w-full flex items-center justify-between 
                            p-3 rounded-lg border 
                            transition-all duration-200
                            ${fontSize === value
                              ? 'border-primary bg-primary/10'
                              : 'border-base-300 hover:border-primary/50 hover:bg-base-300'
                            }
                          `}
                        >
                          <span className={`
                            font-medium
                            ${fontSize === value ? 'text-base-content' : 'text-base-content/60'}
                          `}>
                            {label}
                          </span>
                          <span 
                            className="text-base-content/50"
                            style={{ fontSize: size }}
                          >
                            Aa
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Density */}
                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="
                        w-10 h-10 rounded-xl 
                        bg-accent/10 
                        flex items-center justify-center
                      ">
                        <LayoutGrid className="w-5 h-5 text-accent" />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg">布局密度</h3>
                        <p className="text-body-sm text-base-content/60">
                          调整界面元素间距
                        </p>
                      </div>
                    </div>
                    
                    <div className="space-y-2">
                      {densities.map(({ value, label, desc }) => (
                        <button
                          key={value}
                          onClick={() => setDensity(value)}
                          className={`
                            w-full flex items-center justify-between 
                            p-3 rounded-lg border 
                            transition-all duration-200
                            ${density === value
                              ? 'border-primary bg-primary/10'
                              : 'border-base-300 hover:border-primary/50 hover:bg-base-300'
                            }
                          `}
                        >
                          <div className="text-left">
                            <span className={`
                              font-medium
                              ${density === value ? 'text-base-content' : 'text-base-content/60'}
                            `}>
                              {label}
                            </span>
                            <p className="text-xs text-base-content/40">{desc}</p>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* CONNECTION SECTION */}
            {activeSection === 'connection' && (
              <div className="space-y-5">
                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="
                        w-10 h-10 rounded-xl 
                        bg-primary/10 
                        flex items-center justify-center
                      ">
                        <Globe className="w-5 h-5 text-primary" />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg">服务器连接</h3>
                        <p className="text-body-sm text-base-content/60">
                          配置服务端连接参数
                        </p>
                      </div>
                    </div>
                    
                    <div className="space-y-4">
                      <div className="form-control">
                        <label className="label py-1">
                          <span className="label-text font-medium">WebSocket URL</span>
                        </label>
                        <input
                          type="text"
                          value={wsUrl}
                          onChange={(e) => setWsUrl(e.target.value)}
                          className="input input-bordered font-mono text-sm"
                          placeholder="ws://localhost:8888"
                        />
                      </div>
                      
                      <div className="form-control">
                        <label className="label py-1">
                          <span className="label-text font-medium">REST API URL</span>
                        </label>
                        <input
                          type="text"
                          value={serverUrl}
                          onChange={(e) => setServerUrl(e.target.value)}
                          className="input input-bordered font-mono text-sm"
                          placeholder="http://localhost:8080"
                        />
                      </div>
                      
                      <div className="form-control">
                        <label className="label py-1">
                          <span className="label-text font-medium">用户名</span>
                        </label>
                        <input
                          type="text"
                          value={username}
                          onChange={(e) => setUsername(e.target.value)}
                          className="input input-bordered"
                          placeholder="您的名称"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-bold">自动重连</h3>
                        <p className="text-body-sm text-base-content/60">
                          连接断开时自动尝试重新连接
                        </p>
                      </div>
                      <input 
                        type="checkbox" 
                        className="toggle toggle-primary" 
                        checked={autoReconnect}
                        onChange={(e) => setAutoReconnect(e.target.checked)}
                      />
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* NOTIFICATIONS SECTION */}
            {activeSection === 'notifications' && (
              <div className="card bg-base-200 border-base-300 shadow-sm">
                <div className="card-body p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="
                      w-10 h-10 rounded-xl 
                      bg-primary/10 
                      flex items-center justify-center
                    ">
                      <Bell className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                      <h3 className="font-bold text-lg">通知设置</h3>
                      <p className="text-body-sm text-base-content/60">
                        配置消息提醒方式
                      </p>
                    </div>
                  </div>
                  
                  <div className="space-y-4">
                    {[
                      { key: 'desktop', label: '桌面通知', desc: '显示系统通知弹窗' },
                      { key: 'sound', label: '提示音', desc: '新消息时播放声音' },
                      { key: 'mentionsOnly', label: '仅提及', desc: '只在被@时通知' },
                    ].map(({ key, label, desc }) => (
                      <div 
                        key={key} 
                        className="flex items-center justify-between py-2"
                      >
                        <div>
                          <p className="font-medium">{label}</p>
                          <p className="text-body-sm text-base-content/60">{desc}</p>
                        </div>
                        <input 
                          type="checkbox" 
                          className="toggle toggle-primary" 
                          checked={notifications[key as keyof typeof notifications]}
                          onChange={(e) => setNotifications(prev => ({ 
                            ...prev, 
                            [key]: e.target.checked 
                          }))}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* STORAGE SECTION */}
            {activeSection === 'storage' && (
              <div className="space-y-5">
                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="
                        w-10 h-10 rounded-xl 
                        bg-primary/10 
                        flex items-center justify-center
                      ">
                        <HardDrive className="w-5 h-5 text-primary" />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg">本地存储</h3>
                        <p className="text-body-sm text-base-content/60">
                          管理浏览器本地数据
                        </p>
                      </div>
                    </div>
                    
                    <div className="space-y-4">
                      <div className="
                        flex items-center justify-between 
                        p-4 bg-base-300 rounded-lg
                      ">
                        <div className="flex items-center gap-3">
                          <Database className="w-5 h-5 text-base-content/50" />
                          <div>
                            <p className="font-medium">消息历史缓存</p>
                            <p className="text-body-sm text-base-content/50">
                              存储在浏览器中
                            </p>
                          </div>
                        </div>
                        <span className="text-base-content/60 font-mono text-sm">
                          ~2.4 MB
                        </span>
                      </div>
                      
                      <button className="btn btn-error btn-outline btn-sm gap-2">
                        <Trash2 className="w-4 h-4" />
                        清除缓存
                      </button>
                    </div>
                  </div>
                </div>

                <div className="card bg-base-200 border-base-300 shadow-sm">
                  <div className="card-body p-5">
                    <div className="flex items-center gap-3 mb-4">
                      <div className="
                        w-10 h-10 rounded-xl 
                        bg-secondary/10 
                        flex items-center justify-center
                      ">
                        <Download className="w-5 h-5 text-secondary" />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg">数据导出</h3>
                        <p className="text-body-sm text-base-content/60">
                          导出设置和聊天记录
                        </p>
                      </div>
                    </div>
                    
                    <button className="btn btn-secondary btn-sm gap-2">
                      <Download className="w-4 h-4" />
                      导出数据
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* ADVANCED SECTION */}
            {activeSection === 'advanced' && (
              <div className="card bg-base-200 border-warning/30 shadow-sm">
                <div className="card-body p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="
                      w-10 h-10 rounded-xl 
                      bg-warning/10 
                      flex items-center justify-center
                    ">
                      <AlertTriangle className="w-5 h-5 text-warning" />
                    </div>
                    <div>
                      <h3 className="font-bold text-lg">高级设置</h3>
                      <p className="text-body-sm text-base-content/60">
                        仅供开发者使用
                      </p>
                    </div>
                  </div>
                  
                  <div className="space-y-4">
                    <div className="flex items-center justify-between py-2">
                      <div>
                        <p className="font-medium">调试模式</p>
                        <p className="text-body-sm text-base-content/60">
                          显示额外的调试信息
                        </p>
                      </div>
                      <input type="checkbox" className="toggle" />
                    </div>
                    
                    <div className="flex items-center justify-between py-2">
                      <div>
                        <p className="font-medium">实验性功能</p>
                        <p className="text-body-sm text-base-content/60">
                          启用 Beta 功能
                        </p>
                      </div>
                      <input type="checkbox" className="toggle" />
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* ACTIONS */}
            <div className="
              flex items-center justify-between 
              pt-4 border-t border-base-300
            ">
              <button 
                onClick={handleReset}
                className="btn btn-ghost btn-sm"
              >
                恢复默认
              </button>
              
              <div className="flex items-center gap-3">
                {saved && (
                  <span className="text-success text-sm flex items-center gap-1 animate-fade-in">
                    <Check className="w-4 h-4" />
                    已保存
                  </span>
                )}
                <button 
                  onClick={handleSave}
                  className="btn btn-primary"
                >
                  保存更改
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
