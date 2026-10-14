/** React 唯一挂载入口；StrictMode 帮助发现副作用问题，不在挂载阶段提交业务写入。 */
import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import './index.css'
import './console.css'

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
