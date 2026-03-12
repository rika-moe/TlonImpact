import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAppStore } from './stores/appStore'
import ConnectPage from './pages/Connect'
import Layout from './components/Layout'
import CongressPage from './pages/Congress'
import MonitorPage from './pages/Monitor'
import AgentsPage from './pages/Agents'
import SettingsPage from './pages/Settings'

function App() {
  const { isConnected } = useAppStore()

  return (
    <BrowserRouter basename="/ui">
      <Routes>
        <Route 
          path="/" 
          element={isConnected ? <Navigate to="/congress" /> : <ConnectPage />} 
        />
        <Route element={<Layout />}>
          <Route path="/congress" element={<CongressPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:id" element={<AgentsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
