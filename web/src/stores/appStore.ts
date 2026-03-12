import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AppState {
  // Connection
  isConnected: boolean
  serverUrl: string
  wsUrl: string
  restUrl: string
  username: string
  sessionId: string | null
  
  // Actions
  setConnected: (connected: boolean) => void
  setServerUrl: (url: string) => void
  setWsUrl: (url: string) => void
  setRestUrl: (url: string) => void
  setUsername: (name: string) => void
  setSessionId: (id: string | null) => void
  
  // Settings
  theme: 'dark' | 'light' | 'system'
  language: 'zh' | 'en'
  setTheme: (theme: 'dark' | 'light' | 'system') => void
  setLanguage: (lang: 'zh' | 'en') => void
  applyTheme: (theme: 'dark' | 'light' | 'system') => void
  
  // UI
  sidebarCollapsed: boolean
  setSidebarCollapsed: (collapsed: boolean) => void
  rightPanelOpen: boolean
  setRightPanelOpen: (open: boolean) => void
}

// Theme application helper
const applyDaisyUITheme = (theme: 'dark' | 'light' | 'system') => {
  const root = document.documentElement
  
  if (theme === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    if (prefersDark) {
      root.classList.add('dark')
      root.setAttribute('data-theme', 'tlonDark')
    } else {
      root.classList.remove('dark')
      root.setAttribute('data-theme', 'tlonLight')
    }
  } else if (theme === 'dark') {
    root.classList.add('dark')
    root.setAttribute('data-theme', 'tlonDark')
  } else {
    root.classList.remove('dark')
    root.setAttribute('data-theme', 'tlonLight')
  }
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      isConnected: false,
      serverUrl: 'localhost:8888',
      wsUrl: 'ws://localhost:8888/ws',
      restUrl: 'http://localhost:8080',
      username: 'Operator',
      sessionId: null,
      
      setConnected: (connected) => set({ isConnected: connected }),
      setServerUrl: (url) => set({ serverUrl: url }),
      setWsUrl: (url) => set({ wsUrl: url }),
      setRestUrl: (url) => set({ restUrl: url }),
      setUsername: (name) => set({ username: name }),
      setSessionId: (id) => set({ sessionId: id }),
      
      theme: 'dark',
      language: 'zh',
      setTheme: (theme) => {
        set({ theme })
        applyDaisyUITheme(theme)
      },
      setLanguage: (lang) => set({ language: lang }),
      applyTheme: (theme) => {
        applyDaisyUITheme(theme)
      },
      
      sidebarCollapsed: false,
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      rightPanelOpen: true,
      setRightPanelOpen: (open) => set({ rightPanelOpen: open }),
    }),
    {
      name: 'tlon-app-storage',
      partialize: (state) => ({
        serverUrl: state.serverUrl,
        wsUrl: state.wsUrl,
        restUrl: state.restUrl,
        username: state.username,
        theme: state.theme,
        language: state.language,
        sidebarCollapsed: state.sidebarCollapsed,
        rightPanelOpen: state.rightPanelOpen,
      }),
    }
  )
)

// Initialize theme on load
if (typeof window !== 'undefined') {
  const saved = localStorage.getItem('tlon-app-storage')
  if (saved) {
    try {
      const state = JSON.parse(saved)
      applyDaisyUITheme(state.state?.theme || 'dark')
    } catch {
      applyDaisyUITheme('dark')
    }
  } else {
    applyDaisyUITheme('dark')
  }
}
