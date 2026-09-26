import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { initTheme } from './theme'
import { initWorkspaceTheme } from './workspace/workspaceTheme'

initTheme()
initWorkspaceTheme()
createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>)
