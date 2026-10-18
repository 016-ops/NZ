/** 独立游戏启动器：前端只持有本机操作能力，账号与游戏凭据由本机服务保存在内存。 */
import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, ArrowRight, CheckCircle2, Gamepad2, LoaderCircle, LogOut, RotateCcw, ShieldCheck, Square } from 'lucide-react'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Alert, AlertDescription, AlertTitle } from './ui/alert'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

type LauncherState = {
  version: string; phase: string; busy: boolean; error: string | null; authenticated: boolean
  nickname: string | null; cleanup_pending: boolean; run: string | null
  events: { phase: string; at: string }[]; game_active: boolean
}

const phases: Record<string, string> = {
  READY: '等待登录', CONNECTING: '连接游戏服务', LOGGING_IN: '验证账号', LOGGED_IN: '可以启动游戏',
  RECOVERING: '检查上次退出状态', CLIENT_CHECK: '核对客户端文件', AUTHORIZING: '获取本次游戏凭据',
  LOADING_INVENTORY: '读取角色与仓库', GRAPHICS_CHECKED: '已检测到图形接口，画面待确认', PROCESS_STARTED: '游戏进程已启动',
  GRAPHICS_COMPAT_APPLIED: '已启用本次兼容显示模式',
  ZONE_CONNECTED: '已连接区服', INVENTORY_SENT: '仓库已同步，等待游戏界面', CONNECTION_ERROR: '游戏连接出现异常',
  CLEANING: '正在退出并清理', EXITED: '游戏已退出，清理完成', CLOSED: '启动器已关闭', ERROR: '需要处理后重试',
}
const errors: Record<string, string> = {
  AUTH: '邮箱或密码不正确，或本次登录已过期。', EMAIL_NOT_VERIFIED: '请先完成账号邮箱验证。',
  LOGIN_REQUIRED: '请先登录账号。', LAUNCHER_BUSY: '上一项操作仍在进行，请稍候。',
  SESSION_CLEANUP_PENDING: '退出尚未得到服务端确认。连接恢复后，请点击“检查并恢复”。',
  CONFIGURATION_CONFLICT: '客户端配置在运行期间发生变化，已保留当前文件。请保留诊断编号以便处理。',
  PREVIOUS_GAME_STILL_RUNNING: '上一轮游戏进程仍在运行，请先从游戏内退出。',
  START_CANCELLED: '启动已取消，本次凭据已进入清理流程。',
  SESSION_LEASE_EXPIRED: '连接中断过久，本次会话已到期。请重新登录。',
  SESSION_REVOKED: '服务端已结束本次会话，请重新登录。',
  INVALID_SESSION_LEASE: '服务端会话版本不匹配，请检查 DEV 发布状态。',
  CLIENT_DID_NOT_START: '游戏未能启动，请查看诊断编号。',
  D3D9_UNAVAILABLE_IN_LAUNCH_CONTEXT: '当前启动环境无法访问 D3D9 图形接口。请从本机桌面 Launcher 启动，并保留诊断编号。',
  CLIENT_STARTUP_TIMEOUT: '游戏启动超过三分钟，尚未完成区服与仓库初始化。本轮已停止并清理，请保留诊断编号。',
  GRAPHICS_COMPAT_PACKAGE_MISSING: '兼容显示组件未安装，请先完成 Launcher 构建。',
  GRAPHICS_COMPAT_PACKAGE_MISMATCH: '兼容显示组件校验失败，请保留诊断编号并重新构建。',
  GRAPHICS_COMPAT_FILE_CONFLICT: '游戏工作副本中已有显示组件，已保留原文件。请检查并恢复后重试。',
  FRAME_BRIDGE_COMPONENT_MISSING: '兼容显示组件缺失，请重新构建 Launcher。',
  FRAME_BRIDGE_COMPONENT_MISMATCH: '兼容显示组件校验失败，已停止启动。请重新构建 Launcher。',
  FRAME_BRIDGE_FILE_CONFLICT: '游戏工作副本中已有其他显示组件，已保留该文件。请检查并恢复后重试。',
  LAUNCHER_ACCESS_REQUIRED: '请从“启动Launcher.cmd”重新打开此窗口。',
  VERIFIED_ACCOUNT_AND_CHARACTER_REQUIRED: '此账号需要先完成邮箱验证并建立角色。',
}

/** 从首次本机链接取能力后立即移除 fragment；只在当前标签页保留，刷新无需重新发放。 */
function readAccess() {
  const incoming = new URLSearchParams(window.location.hash.slice(1)).get('access')
  if (incoming && /^[A-Za-z0-9_-]{43}$/.test(incoming)) {
    sessionStorage.setItem('nz-launcher-access', incoming)
    history.replaceState(null, '', window.location.pathname)
  }
  return sessionStorage.getItem('nz-launcher-access') ?? ''
}

/** 呈现登录、启动、取消及清理状态；任何阶段都不把协议握手成功显示为已进入大厅。 */
export function NativeLauncher() {
  const [access] = useState(readAccess)
  const [state, setState] = useState<LauncherState | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [sending, setSending] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [closed, setClosed] = useState(false)
  const [graphicsMode, setGraphicsMode] = useState('frame-copy')

  /** 单次请求不重放写操作；错误只显示服务给出的受限代码，避免将原始正文渲染到页面。 */
  async function request(action: string, body?: Record<string, string>) {
    const response = await fetch(`/launcher/api/${action}`, { method: body ? 'POST' : 'GET',
      headers: { 'X-NZ-Launcher': access, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal: AbortSignal.timeout(20000) })
    const value = await response.json()
    if (!response.ok) throw new Error(value.error || 'REQUEST_FAILED')
    return value
  }

  useEffect(() => {
    document.title = '逆战 · Launcher'
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    /** 串行轮询避免慢服务时堆叠请求；卸载后不更新页面，关闭状态不再报告连接丢失。 */
    async function poll() {
      try {
        const next: LauncherState = await request('status')
        if (!disposed) {
          setState(next)
          setRequestError(null)
          if (next.phase === 'CLOSED') { setClosed(true); return }
        }
      } catch (error) {
        if (!disposed) setRequestError(error instanceof Error ? error.message : 'CONNECTION_FAILED')
      }
      if (!disposed) timer = setTimeout(poll, 1200)
    }
    if (access) void poll()
    else setRequestError('LAUNCHER_ACCESS_REQUIRED')
    return () => { disposed = true; clearTimeout(timer) }
  }, [access])

  /** 发送一次明确操作，登录提交后马上清空输入框；密码不进入任何浏览器持久存储。 */
  async function perform(action: string, body: Record<string, string> = {}) {
    setSending(true); setRequestError(null)
    try {
      const pending = request(action, body)
      if (action === 'login') setPassword('')
      await pending
      setState(await request('status'))
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'REQUEST_FAILED')
    } finally { setSending(false) }
  }

  /** 表单仅提交至固定回环服务，远程地址由后端决定，页面不能改服务器或执行路径。 */
  function login(event: FormEvent) {
    event.preventDefault()
    void perform('login', { email: email.trim(), password })
  }

  const busy = sending || !!state?.busy
  const error = requestError || state?.error
  const statusLabel = closed ? '启动器已关闭' : phases[state?.phase ?? 'READY'] ?? '正在处理'
  return <main className="dark min-h-screen bg-slate-950 text-slate-100">
    <div className="mx-auto max-w-6xl px-6 py-10 md:py-16">
      <header className="mb-12 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3"><Gamepad2 className="size-8 text-emerald-400" /><span className="text-lg font-semibold tracking-wide">逆战 <span className="ml-2 font-normal text-slate-400">Launcher</span></span></div>
        <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">DEV · 当前客户端</span>
      </header>
      <div className="grid gap-8 md:grid-cols-[1.1fr_1fr]">
        <section className="flex flex-col justify-between gap-10 rounded-2xl border border-slate-800 bg-gradient-to-br from-slate-900 to-slate-950 p-8 md:p-10">
          <div><p className="mb-5 text-xs tracking-[0.3em] text-emerald-400">NZ REVIVAL</p><h1 className="text-4xl font-semibold leading-tight">从这里，<br />回到战场。</h1><p className="mt-6 max-w-sm leading-7 text-slate-400">登录你的账号，读取角色和仓库。每次启动检查环境，退出后完成本轮清理。</p></div>
          <div className="space-y-5 border-t border-slate-800 pt-7 text-sm text-slate-300">
            <p className="flex items-center gap-3"><ShieldCheck className="size-5 text-emerald-400" />密码不保存到本机</p>
            <p className="flex items-center gap-3"><RotateCcw className="size-5 text-emerald-400" />退出后恢复临时启动配置</p>
            <p className="text-xs leading-6 text-slate-500">当前为开发验证版本。大厅画面与玩法就绪以游戏内实际状态为准。</p>
          </div>
        </section>
        <div className="space-y-5">
          <Card className="border border-slate-800 bg-slate-900/70 text-slate-100 ring-0">
            <CardHeader><CardTitle className="text-xl">{state?.authenticated ? `你好，${state.nickname ?? '玩家'}` : '登录账号'}</CardTitle><CardDescription className="text-slate-400">{state?.authenticated ? '账号已验证，可以启动本次游戏。' : '使用已注册并验证的邮箱账号。'}</CardDescription></CardHeader>
            <CardContent>
              {state?.authenticated ? <div className="space-y-4"><Button size="lg" className="h-12 w-full bg-emerald-400 font-semibold text-slate-950 hover:bg-emerald-300" disabled={busy || state.cleanup_pending || closed} onClick={() => void perform('start', { graphics_mode: graphicsMode })}>启动游戏 <ArrowRight /></Button><Button variant="ghost" className="w-full text-slate-400" disabled={busy || closed} onClick={() => void perform('logout')}><LogOut />退出账号</Button></div> : <form onSubmit={login} className="space-y-5" autoComplete="off">
                <div className="space-y-2"><Label htmlFor="launcher-email">邮箱</Label><Input id="launcher-email" type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="你的账号邮箱" required maxLength={254} disabled={busy || closed} className="h-11" /></div>
                <div className="space-y-2"><Label htmlFor="launcher-password">密码</Label><Input id="launcher-password" type="password" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)} placeholder="输入密码" required maxLength={256} disabled={busy || closed} className="h-11" /></div>
                <Button type="submit" size="lg" disabled={busy || !access || closed} className="h-12 w-full bg-emerald-400 font-semibold text-slate-950 hover:bg-emerald-300">{busy ? <LoaderCircle className="animate-spin" /> : null} 登录</Button>
              </form>}
            </CardContent>
          </Card>
          <Card className="border border-slate-800 bg-slate-900/70 text-slate-100 ring-0">
            <CardContent className="space-y-4">
              <div className="space-y-2"><Label htmlFor="launcher-graphics">游戏显示模式</Label><Select value={graphicsMode} onValueChange={setGraphicsMode} disabled={busy || closed}><SelectTrigger id="launcher-graphics" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="frame-copy">兼容显示（当前电脑推荐）</SelectItem><SelectItem value="system">标准显示</SelectItem></SelectContent></Select><p className="text-xs leading-5 text-slate-400">兼容显示已验证大厅、仓库及商城交互，战斗和长期性能仍待验证。退出后自动还原临时配置。</p></div>
              <div className="flex items-center gap-3" role="status">{busy ? <LoaderCircle className="size-5 animate-spin text-emerald-400" /> : <CheckCircle2 className="size-5 text-slate-400" />}<span>{statusLabel}</span></div>
              {error && <Alert variant="destructive"><AlertCircle /><AlertTitle>操作尚未完成</AlertTitle><AlertDescription>{errors[error] ?? '请检查连接后重试，或保留以下错误码以便排查。'}<span className="mt-2 block font-mono text-xs">{error}</span></AlertDescription></Alert>}
              {state?.busy && <Button variant="outline" disabled={sending || state.phase === 'CLEANING'} onClick={() => void perform('stop')}><Square />停止本次启动 / 游戏</Button>}
              <div className="flex flex-wrap gap-2"><Button variant="ghost" size="sm" disabled={busy || closed || !access} onClick={() => void perform('recover')}><RotateCcw />检查并恢复</Button><Button variant="ghost" size="sm" disabled={busy || closed || !access} onClick={() => void perform('close')}>关闭 Launcher</Button></div>
              {state?.events.length ? <details className="border-t border-slate-800 pt-4 text-xs text-slate-400"><summary className="cursor-pointer">启动记录{state.run ? ` · ${state.run}` : ''}</summary><ol className="mt-3 max-h-52 space-y-2 overflow-auto">{state.events.map((event, index) => <li key={index}><time className="mr-3 font-mono">{event.at}</time>{phases[event.phase] ?? event.phase}</li>)}</ol></details> : null}
              {closed && <p className="text-sm text-slate-400">清理完成，可以关闭此窗口。</p>}
            </CardContent>
          </Card>
        </div>
      </div>
      <footer className="mt-8 text-xs text-slate-600">{state?.version ?? 'cn2020-launcher-v1'} · 请从 Launcher 启动游戏并保留此窗口</footer>
    </div>
  </main>
}
