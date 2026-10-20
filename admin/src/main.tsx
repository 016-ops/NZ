/** React 唯一挂载入口；StrictMode 帮助发现副作用问题，不在挂载阶段提交业务写入。 */
import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import './console.css'
import './platform.css'

// Launcher 使用独立本机服务和能力令牌，不复用后台管理登录或权限。
const path = window.location.pathname.replace(/\/$/, '') || '/'
// 三种入口按需加载，官网访客无需下载完整旧工作台及原生启动逻辑。
const App = lazy(() => import('./App').then(module => ({ default: module.App })))
const NativeLauncher = lazy(() => import('./components/native-launcher').then(module => ({ default: module.NativeLauncher })))
const PortalApp = lazy(() => import('./portal-app').then(module => ({ default: module.PortalApp })))
// 旧工作台保留 /console 及子路径；官网、玩家和运营中心由新的共享前端承载。
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><Suspense fallback={<div className="nz-app"><p className="nz-loading">正在准备页面…</p></div>}>{path === '/launcher' ? <NativeLauncher /> : path === '/console' || path.startsWith('/console/') ? <App /> : <PortalApp />}</Suspense></React.StrictMode>)
