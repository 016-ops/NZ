/** 独立游戏启动器：前端只持有本机操作能力，账号与游戏凭据由本机服务保存在内存。 */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { AlertCircle, ArrowRight, CheckCircle2, ChevronRight, Gamepad2, Gift, Home, LoaderCircle, LogOut, Package, RotateCcw, Settings, ShieldCheck, ShoppingBag, Square, Trophy, UserRound } from 'lucide-react'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Alert, AlertDescription, AlertTitle } from './ui/alert'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { CampaignsPage, Empty, InventoryPage, LeaderboardsPage, PlayerShopPage } from './platform-pages'
import { useResource, type PortalInfo, type RuntimeApi } from '@/lib/platform'

type LauncherState = {
  version: string; phase: string; busy: boolean; error: string | null; authenticated: boolean
  nickname: string | null; cleanup_pending: boolean; run: string | null
  events: { phase: string; at: string }[]; game_active: boolean
  native_start?: { available: boolean; blocker?: string; mode?: string; native_combat_verified?: boolean }
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
  PACKAGE_NATIVE_CLIENT_UNAVAILABLE: '当前为独立平台测试包。游戏客户端与原生通道需要单独部署和验收。',
  LAUNCHER_PLATFORM_PROXY_UNAVAILABLE: '此启动器版本尚未连接该平台页面，请更新启动器后重试。',
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
  // 桌面保持单一受限 URL；内存导航不会把能力、业务状态或参数写入地址栏。
  const [page, setPage] = useState<'home' | 'inventory' | 'shop' | 'activities' | 'leaderboards' | 'settings'>('home')

  /** 单次请求不重放写操作；错误只显示服务给出的受限代码，避免将原始正文渲染到页面。 */
  const request = useCallback(async (action: string, body?: Record<string, unknown>) => {
    const response = await fetch(`/launcher/api/${action}`, { method: body ? 'POST' : 'GET',
      headers: { 'X-NZ-Launcher': access, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal: AbortSignal.timeout(20000) })
    const value = await response.json()
    if (!response.ok) throw new Error(value.error || 'REQUEST_FAILED')
    return value
  }, [access])

  /** 共享页面仅调用本机固定代理；业务 Token 始终留在 Python，界面不能选择外网地址。 */
  const platformApi = useMemo<RuntimeApi>(() => ({
    request: <T,>(path: string, body?: object) => {
      if (body) return Promise.reject(new Error('LAUNCHER_PLATFORM_PROXY_UNAVAILABLE'))
      const target = new URL(path, 'http://nz.invalid')
      const view = target.pathname.split('/').at(-1) || ''
      if (!['portal', 'me', 'campaigns', 'leaderboards', 'player-shop', 'player-inventory'].includes(view)) return Promise.reject(new Error('LAUNCHER_PLATFORM_PROXY_UNAVAILABLE'))
      return request('view', { view, query: Object.fromEntries(target.searchParams) }) as Promise<T>
    },
    command: (action, payload, metadata) => request('command', { action, payload, metadata }),
  }), [request])
  const portal = useResource<PortalInfo>(platformApi, state?.authenticated ? '/api/public/portal' : null)

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
  }, [access, request])

  /** Select 等浮层挂载于 body；统一文档主题，让原生窗口中的弹出层保持相同对比度。 */
  useEffect(() => { const previous = document.documentElement.classList.contains('dark'); document.documentElement.classList.add('dark'); return () => { if (!previous) document.documentElement.classList.remove('dark') } }, [])

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
  const unavailable = state?.native_start?.available === false
  const navigation = [{ page: 'home', label: '游戏首页', icon: Home }, { page: 'inventory', label: '我的仓库', icon: Package }, { page: 'shop', label: '战备商城', icon: ShoppingBag }, { page: 'activities', label: '开服活动', icon: Gift }, { page: 'leaderboards', label: '先锋榜', icon: Trophy }] as const
  return <main className="nz-launcher dark">
    <aside className="nz-desktop-rail"><button className="nz-desktop-brand" aria-label="游戏首页" onClick={() => setPage('home')}>NZ</button><nav aria-label="启动器导航">{navigation.map(item => <button key={item.page} className={page === item.page ? 'is-active' : ''} title={item.label} aria-label={item.label} onClick={() => setPage(item.page)}><item.icon size={22} strokeWidth={1.5} /><span>{item.label}</span></button>)}</nav><button className={page === 'settings' ? 'is-active nz-desktop-settings' : 'nz-desktop-settings'} title="设置与诊断" aria-label="设置与诊断" onClick={() => setPage('settings')}><Settings size={22} strokeWidth={1.5} /><span>设置</span></button></aside>
    <section className="nz-desktop-main"><header className="nz-desktop-header"><div><span>逆战新生</span><i /><strong>{page === 'settings' ? '设置与诊断' : navigation.find(item => item.page === page)?.label}</strong></div><div><span className="nz-tag">DEV 测试版</span><span className="nz-desktop-account"><UserRound size={15} />{state?.authenticated ? state.nickname || '战友' : '尚未登录'}</span>{state?.authenticated && <Button variant="ghost" size="icon" title="退出账号" aria-label="退出账号" disabled={busy || closed} onClick={() => void perform('logout')}><LogOut size={16} /></Button>}</div></header>
      <div className="nz-desktop-scroll">
        {error && <Alert variant="destructive" className="nz-desktop-alert"><AlertCircle /><AlertTitle>操作尚未完成</AlertTitle><AlertDescription>{errors[error] ?? '请检查连接后重试，或保留错误码以便排查。'}<span className="nz-error-code">{error}</span></AlertDescription></Alert>}
        {closed ? <Empty title="清理完成，期待再次并肩。" icon={<ShieldCheck size={38} />}>本次启动器已关闭，可以安全关闭窗口。</Empty> : page === 'settings' ? <section className="nz-page nz-desktop-diagnostics"><div className="nz-page-heading"><div><h1>设置与诊断</h1><p>检查显示方式与本轮启动记录。退出清理未完成时，请先检查并恢复。</p></div></div><Card className="nz-panel"><CardHeader><CardTitle>游戏显示模式</CardTitle><CardDescription>仅影响本次受控启动，退出后按恢复计划还原。</CardDescription></CardHeader><CardContent><Label htmlFor="launcher-graphics">显示方式</Label><Select value={graphicsMode} onValueChange={setGraphicsMode} disabled={busy || closed}><SelectTrigger id="launcher-graphics" className="nz-graphics-select"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="frame-copy">兼容显示（当前电脑推荐）</SelectItem><SelectItem value="system">标准显示</SelectItem></SelectContent></Select><p className="nz-form-hint">兼容显示曾在当前验证机器完成大厅、仓库及商城交互；战斗、长期性能和其他机器仍待实测。</p><div className="nz-inline-actions"><Button variant="outline" disabled={busy || !access} onClick={() => void perform('recover')}><RotateCcw size={16} />检查并恢复</Button><Button variant="ghost" disabled={busy || !access} onClick={() => void perform('close')}>关闭 Launcher</Button></div></CardContent></Card><Card className="nz-panel"><CardHeader><CardTitle>本轮启动记录</CardTitle><CardDescription>{state?.run ? `诊断编号 ${state.run}` : '首次启动后生成诊断编号。'}</CardDescription></CardHeader><CardContent>{state?.events.length ? <ol className="nz-launch-events">{state.events.map((event, index) => <li key={index}><time>{event.at}</time><span>{phases[event.phase] ?? event.phase}</span></li>)}</ol> : <p className="nz-form-hint">暂无启动记录。</p>}</CardContent></Card></section> : page === 'home' ? <><section className="nz-desktop-hero"><div><span className="nz-tag">战友集结</span><h1>这一战，<br />我们再并肩。</h1><p>熟悉的战场，全新的起点。<br />从这里开始下一场合作挑战。</p><button onClick={() => setPage('activities')}>查看开服活动 <ArrowRight size={16} /></button></div></section><div className="nz-desktop-home-grid"><Card className="nz-panel nz-desktop-login"><CardHeader><CardTitle>{state?.authenticated ? `欢迎归队，${state.nickname || '战友'}。` : '登录你的账号'}</CardTitle><CardDescription>{state?.authenticated ? unavailable ? '账号已登录。平台功能可用，原生游戏仍待配置。' : '账号已验证，启动前仍会执行环境与客户端检查。' : '使用已注册、验证并创建角色的邮箱账号。'}</CardDescription></CardHeader><CardContent>{state?.authenticated ? <div className="nz-desktop-account-actions"><span className="nz-auth-check"><ShieldCheck size={24} />本次会话已验证</span><Button variant="outline" onClick={() => setPage('inventory')}>查看我的仓库 <ChevronRight size={15} /></Button>{unavailable && <p className="nz-form-hint">独立平台测试包已就绪；完整游戏客户端、原生通道和实玩需另行验收。</p>}</div> : <form onSubmit={login} autoComplete="off" className="nz-desktop-login-form"><div><Label htmlFor="launcher-email">邮箱</Label><Input id="launcher-email" type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="输入注册邮箱" required maxLength={254} disabled={busy} /></div><div><Label htmlFor="launcher-password">密码</Label><Input id="launcher-password" type="password" autoComplete="off" value={password} onChange={event => setPassword(event.target.value)} placeholder="输入账号密码" required maxLength={256} disabled={busy} /></div><Button type="submit" disabled={busy || !access}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : null}登录账号 <ArrowRight size={16} /></Button></form>}</CardContent></Card><section className="nz-desktop-discover"><button onClick={() => setPage('activities')}><Gift size={27} strokeWidth={1.25} /><span><strong>开服补给，等你领取</strong><small>登录、签到与挑战奖励</small></span><ArrowRight size={16} /></button><button onClick={() => setPage('leaderboards')}><Trophy size={27} strokeWidth={1.25} /><span><strong>写下小队的新纪录</strong><small>分地图、人数与难度的先锋榜</small></span><ArrowRight size={16} /></button><div className="nz-desktop-assurance"><ShieldCheck size={16} /><p>密码不保存到本机。游戏退出后完成会话与临时配置清理。</p></div></section></div></> : !state?.authenticated ? <div className="nz-desktop-login-required"><Empty title="先登录，再查看你的战备" icon={<UserRound size={35} />}>仓库、商城、活动进度和榜单通过当前会话读取。</Empty><Button onClick={() => setPage('home')}>返回登录 <ArrowRight size={16} /></Button></div> : page === 'inventory' ? <InventoryPage api={platformApi} /> : page === 'shop' ? <PlayerShopPage api={platformApi} /> : page === 'activities' ? <CampaignsPage api={platformApi} authenticated /> : <LeaderboardsPage api={platformApi} authenticated maps={portal.data?.maps || []} />}
      </div>
    </section>
    <footer className="nz-desktop-launchbar"><div className="nz-launchbar-version"><span className="nz-brand-symbol">NZ</span><span>逆战新生<small>{state?.version || 'cn2020-launcher-v1'}</small></span></div><div className="nz-launchbar-status" role="status">{busy ? <LoaderCircle size={18} className="animate-spin" /> : <CheckCircle2 size={18} />}<div><strong>{unavailable && state?.authenticated && !busy ? '平台会话已就绪' : statusLabel}</strong><small>{state?.cleanup_pending ? '请完成退出清理后再启动' : state?.run ? `诊断编号 ${state.run}` : '大厅与玩法状态以实机画面和结算为准'}</small></div></div><Button variant="ghost" size="icon" aria-label="检查并恢复" title="检查并恢复" disabled={busy || closed || !access} onClick={() => void perform('recover')}><RotateCcw size={18} /></Button>{state?.busy ? <Button className="nz-desktop-start" variant="outline" disabled={sending || state.phase === 'CLEANING'} onClick={() => void perform('stop')}><Square size={17} />停止本次启动 / 游戏</Button> : <Button className="nz-desktop-start" disabled={closed || busy || !!state?.cleanup_pending || !state?.authenticated || unavailable} onClick={() => void perform('start', { graphics_mode: graphicsMode })}><Gamepad2 size={20} />{unavailable ? '实玩待配置' : '启动游戏'}<ArrowRight size={17} /></Button>}</footer>
  </main>
}
