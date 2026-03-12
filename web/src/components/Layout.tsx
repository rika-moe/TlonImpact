import { Outlet, NavLink, useLocation } from 'react-router-dom'
import { 
  MessageSquare, 
  Activity, 
  Bot, 
  Settings, 
  Bell,
  Sun,
  Moon,
  User,
  Menu,
  PanelRight,
  LogOut,
  Octagon
} from 'lucide-react'
import { useAppStore } from '../stores/appStore'
import WebSocketProvider from './WebSocketProvider'

const navItems = [
  { path: '/congress', icon: MessageSquare, label: 'Congress' },
  { path: '/monitor', icon: Activity, label: 'Monitor' },
  { path: '/agents', icon: Bot, label: 'Agents' },
  { path: '/settings', icon: Settings, label: 'Settings' },
]

function LayoutContent() {
  const location = useLocation()
  const { 
    username, 
    theme, 
    setTheme, 
    setConnected,
    sidebarCollapsed, 
    setSidebarCollapsed,
    rightPanelOpen,
    setRightPanelOpen,
  } = useAppStore()

  const toggleTheme = () => {
    const newTheme = theme === 'dark' ? 'light' : 'dark'
    setTheme(newTheme)
  }

  const handleLogout = () => {
    setConnected(false)
  }

  const currentNavItem = navItems.find(item => location.pathname.startsWith(item.path))

  return (
    <div className="h-screen flex bg-base-100">
      {/* ============================================
          SIDEBAR (80px)
          ============================================ */}
      <aside 
        className={`
          ${sidebarCollapsed ? 'w-16' : 'w-20'} 
          bg-base-200 border-r border-base-300 
          flex flex-col 
          transition-all duration-300 ease-out
        `}
      >
        {/* Logo */}
        <div className="h-16 flex items-center justify-center border-b border-base-300">
          <div className="tooltip tooltip-right" data-tip="Tlon">
            <div className="
              w-10 h-10 rounded-xl 
              bg-gradient-to-br from-primary to-primary/70 
              flex items-center justify-center 
              shadow-lg shadow-primary/30
              ring-2 ring-primary/20
            ">
              <Octagon className="w-6 h-6 text-primary-content" strokeWidth={1.5} />
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3 px-2 space-y-1">
          {navItems.map((item) => {
            const isActive = location.pathname.startsWith(item.path)
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={`
                  flex flex-col items-center justify-center 
                  py-3 px-2 rounded-xl
                  transition-all duration-200 ease-out
                  group
                  ${isActive 
                    ? 'bg-primary text-primary-content shadow-lg shadow-primary/30' 
                    : 'text-base-content/60 hover:text-base-content hover:bg-base-300'
                  }
                `}
                title={item.label}
              >
                <item.icon className={`
                  w-5 h-5 mb-1 
                  transition-transform duration-200
                  group-hover:scale-110
                  ${isActive ? '' : ''}
                `} />
                {!sidebarCollapsed && (
                  <span className="text-xs font-medium leading-none">{item.label}</span>
                )}
              </NavLink>
            )
          })}
        </nav>

        {/* Bottom Actions */}
        <div className="p-3 border-t border-base-300 space-y-2">
          <button 
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="btn btn-ghost btn-sm w-full"
            title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}
          >
            <Menu className={`
              w-5 h-5 transition-transform duration-300
              ${sidebarCollapsed ? 'rotate-180' : ''}
            `} />
          </button>
          <div className="flex items-center justify-center">
            <div className="tooltip tooltip-right" data-tip="已连接">
              <div className="w-3 h-3 rounded-full bg-success status-pulse" />
            </div>
          </div>
        </div>
      </aside>

      {/* ============================================
          MAIN CONTENT
          ============================================ */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header (64px) */}
        <header className="
          h-16 
          glass border-b border-base-300 
          flex items-center justify-between 
          px-6
        ">
          {/* Left: Page Title */}
          <div className="flex items-center gap-4">
            <h1 className="text-title-sm">{currentNavItem?.label || 'Tlon'}</h1>
          </div>
          
          {/* Right: Actions */}
          <div className="flex items-center gap-1">
            {/* Right Panel Toggle */}
            <button 
              onClick={() => setRightPanelOpen(!rightPanelOpen)}
              className={`
                btn btn-ghost btn-sm btn-square
                ${rightPanelOpen ? 'text-primary bg-primary/10' : ''}
              `}
              title="切换右侧面板"
            >
              <PanelRight className="w-5 h-5" />
            </button>
            
            {/* Notifications */}
            <button className="btn btn-ghost btn-sm btn-square relative">
              <Bell className="w-5 h-5" />
              <span className="
                badge badge-xs badge-primary 
                absolute -top-1 -right-1
              ">3</span>
            </button>
            
            {/* Theme Toggle */}
            <button 
              onClick={toggleTheme}
              className="btn btn-ghost btn-sm btn-square"
              title={theme === 'dark' ? '切换到浅色' : '切换到深色'}
            >
              {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>

            {/* User Menu */}
            <div className="flex items-center gap-2 pl-4 border-l border-base-300">
              <div className="dropdown dropdown-end">
                <div 
                  tabIndex={0} 
                  role="button" 
                  className="
                    flex items-center gap-2 
                    p-2 rounded-lg 
                    hover:bg-base-300
                    transition-colors duration-150
                    cursor-pointer
                  "
                >
                  <div className="avatar">
                    <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
                      <User className="w-4 h-4 text-primary-content" />
                    </div>
                  </div>
                  <span className="text-sm font-medium hidden md:block">{username}</span>
                </div>
                <ul tabIndex={0} className="
                  dropdown-content menu menu-sm 
                  bg-base-200 border border-base-300 
                  rounded-box z-[1] mt-3 w-52 p-2 
                  shadow-xl
                ">
                  <li className="menu-title px-3 py-2">
                    <span className="text-base-content/70">{username}</span>
                  </li>
                  <li>
                    <button onClick={handleLogout} className="text-error hover:bg-error/10">
                      <LogOut className="w-4 h-4" />
                      断开连接
                    </button>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </header>

        {/* Content Area */}
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default function Layout() {
  return (
    <WebSocketProvider>
      <LayoutContent />
    </WebSocketProvider>
  )
}
