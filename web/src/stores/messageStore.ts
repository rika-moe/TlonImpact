import { create } from 'zustand'
import type { Message, Agent, TlonEvent } from '../types'

interface MessageState {
  // Messages
  messages: Message[]
  addMessage: (message: Message) => void
  setMessages: (messages: Message[]) => void
  clearMessages: () => void
  
  // Agents
  agents: Agent[]
  setAgents: (agents: Agent[]) => void
  updateAgentStatus: (id: string, status: Agent['status']) => void
  
  // Events
  events: TlonEvent[]
  addEvent: (event: TlonEvent) => void
  setEvents: (events: TlonEvent[]) => void
  clearEvents: () => void
  isPaused: boolean
  setPaused: (paused: boolean) => void
  pendingEvents: TlonEvent[]
  flushPendingEvents: () => void
}

export const useMessageStore = create<MessageState>((set) => ({
  messages: [],
  addMessage: (message) => set((state) => {
    // Check for duplicate messages (same id or same sender+content within 5 seconds)
    const isDuplicate = state.messages.some((m) => {
      // Same ID
      if (m.id === message.id) return true
      // Same sender and content within 5 seconds (optimistic update deduplication)
      if (m.senderId === message.senderId && 
          m.content === message.content &&
          Math.abs(m.timestamp - message.timestamp) < 5000) {
        return true
      }
      return false
    })
    
    if (isDuplicate) {
      console.log('[MessageStore] Duplicate message ignored:', message.id || message.content.slice(0, 20))
      return state
    }
    
    return { messages: [...state.messages, message] }
  }),
  setMessages: (messages) => set({ messages }),
  clearMessages: () => set({ messages: [] }),
  
  agents: [],
  setAgents: (agents) => set({ agents }),
  updateAgentStatus: (id, status) => set((state) => ({
    agents: state.agents.map((a) => 
      a.id === id ? { ...a, status } : a
    ),
  })),
  
  events: [],
  addEvent: (event) => set((state) => {
    if (state.isPaused) {
      return { pendingEvents: [...state.pendingEvents, event] }
    }
    return { events: [...state.events, event] }
  }),
  setEvents: (events) => set({ events }),
  clearEvents: () => set({ events: [] }),
  isPaused: false,
  setPaused: (paused) => set({ isPaused: paused }),
  pendingEvents: [],
  flushPendingEvents: () => set((state) => ({
    events: [...state.events, ...state.pendingEvents],
    pendingEvents: [],
  })),
}))
