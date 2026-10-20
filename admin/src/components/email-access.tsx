/** 两端共用玩家注册/登录与管理员邮箱挑战；密码、验证码及挑战只驻留当前组件内存。 */
import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { RetryEnvelope, type Session } from '@/lib/api'

/** Web 直连与启动器受限桥均实现此接口；表单不依赖业务令牌或服务器地址。 */
export interface AccountApi { request<T = unknown>(path: string, data?: object): Promise<T> }
export interface EmailChallenge { challenge_id: string; retry_after: number; expires_in?: number; recipient_hint?: string; message?: string }
type Mode = 'login' | 'register' | 'reset' | 'verify'
const titles: Record<Mode, string> = { login: '登录', register: '创建账号', reset: '找回密码', verify: '验证已有邮箱' }

/** 按受限桥合同构造账号正文；补验邮箱不携带密码，注册也不恢复已取消的邀请码字段。 */
export function playerAccessPayload(mode: Exclude<Mode, 'login'>, values: { email: string; password: string; nickname: string; code: string; challenge_id: string }) {
  return { email: values.email.trim(), ...(mode === 'verify' ? {} : { password: values.password }),
    ...(mode === 'register' ? { nickname: values.nickname.trim() } : {}), code: values.code, challenge_id: values.challenge_id }
}

/** 可重发倒计时由组件管理；真实发送额度、有效期和验证码判断始终由服务端决定。 */
function useCooldown() {
  const [cooldown, setCooldown] = useState(0)
  useEffect(() => {
    if (!cooldown) return
    const timer = window.setTimeout(() => setCooldown(value => Math.max(0, value - 1)), 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])
  return [cooldown, setCooldown] as const
}

/** 玩家注册填昵称并验证邮箱，成功后用仍在内存中的密码登录；不再要求邀请或人工批准。 */
function PlayerEmailForm({ api, mode, busy, setBusy, report, signedIn, completed, onPlayerLogin, disabled }: {
  api: AccountApi; mode: Mode; busy: boolean; setBusy: (value: boolean) => void; report: (value: unknown) => void
  signedIn: (session: Session, identity: string) => Promise<void>; completed: () => void
  onPlayerLogin?: (email: string, password: string) => Promise<void>; disabled?: boolean
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState('')
  const [cooldown, setCooldown] = useCooldown()
  const retry = useRef(new RetryEnvelope())
  const id = useId()
  /** 统一两端登录回调；启动器的回调只返回完成状态，不把游戏/业务 Session 带入 React。 */
  async function login() {
    if (onPlayerLogin) await onPlayerLogin(email.trim(), password)
    else await signedIn(await api.request<Session>('/api/player/login', { email: email.trim(), password }), email.trim())
  }
  /** 每个用途单独申请挑战；发送中锁定表单，服务端控制实际冷却与投递额度。 */
  async function sendCode() {
    if (busy || cooldown || disabled) return
    setBusy(true)
    try {
      const value = await api.request<EmailChallenge>('/api/player/email-code', { email: email.trim(), purpose: mode })
      setChallenge(value.challenge_id); setCode(''); setCooldown(value.retry_after); report(value.message || '验证码已发送，请检查邮箱。')
    } catch (error) { report(error instanceof Error ? error.message : '验证码发送失败，请稍后重试。') }
    finally { setBusy(false) }
  }
  return <Card className="business-form"><CardHeader><CardTitle>{titles[mode]}</CardTitle></CardHeader><CardContent><form className="grid gap-4" onSubmit={async event => {
    event.preventDefault(); if (busy || disabled) return
    setBusy(true)
    let registered = false
    try {
      if (mode === 'login') await login()
      else {
        const values = playerAccessPayload(mode, { email, password, nickname, code, challenge_id: challenge })
        await api.request('/api/player/' + (mode === 'register' ? 'register' : mode === 'reset' ? 'password-reset' : 'email-verify'), { ...values, ...retry.current.prepare(values, titles[mode]) })
        retry.current.success(); registered = mode === 'register'; completed()
        if (mode === 'register') {
          report('账号与角色已创建，正在为你登录。')
          await login()
        } else report(mode === 'reset' ? '密码已更新，请使用新密码登录。' : '邮箱验证完成，请登录。')
      }
      setPassword(''); setCode('')
    } catch (error) { const message = error instanceof Error ? error.message : '操作未完成，请重试。'; report(registered ? `账号已创建，自动登录尚未完成。请在登录页重试。${message}` : message) }
    finally { setBusy(false) }
  }}>
    <div className="grid gap-2"><Label htmlFor={id + '-email'}>邮箱地址</Label><Input id={id + '-email'} type="email" autoComplete="email" required disabled={busy || disabled} value={email} maxLength={254} onChange={event => { setEmail(event.target.value); setChallenge(''); setCode('') }} /></div>
    {mode === 'register' && <div className="grid gap-2"><Label htmlFor={id + '-nickname'}>游戏昵称</Label><Input id={id + '-nickname'} required minLength={1} maxLength={24} disabled={busy || disabled} value={nickname} onChange={event => setNickname(event.target.value)} placeholder="给战友一个熟悉的称呼" /></div>}
    {mode !== 'verify' && <div className="grid gap-2"><Label htmlFor={id + '-password'}>{mode === 'login' ? '密码' : '新密码（12–128 字符）'}</Label><Input id={id + '-password'} type="password" required minLength={mode === 'login' ? undefined : 12} maxLength={128} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} disabled={busy || disabled} value={password} onChange={event => setPassword(event.target.value)} /></div>}
    {mode !== 'login' && <div className="grid gap-2"><Label htmlFor={id + '-code'}>邮箱验证码</Label><Input id={id + '-code'} required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" disabled={busy || disabled} value={code} onChange={event => setCode(event.target.value)} /><Button type="button" variant="outline" disabled={busy || disabled || cooldown > 0 || !email} onClick={() => void sendCode()}>{cooldown ? `${cooldown} 秒后可重发` : '发送验证码'}</Button></div>}
    <Button type="submit" disabled={busy || disabled || (mode !== 'login' && !challenge)}>{busy ? '处理中…' : mode === 'register' ? '注册并登录' : titles[mode]}</Button>
  </form></CardContent></Card>
}

/** 管理员先校验账号与密码，再由邮箱验证码完成登录；不再显示动态口令输入。 */
function AdminLogin({ api, report, signedIn }: { api: AccountApi; report: (value: unknown) => void; signedIn: (value: Session, identity: string) => Promise<void> }) {
  const [login, setLogin] = useState(''), [password, setPassword] = useState(''), [code, setCode] = useState('')
  const [challenge, setChallenge] = useState<EmailChallenge | null>(null), [busy, setBusy] = useState(false)
  const [cooldown, setCooldown] = useCooldown()
  const id = useId()
  /** 账号或密码变化会废弃已发挑战，防止把其他身份的验证码用于当前表单。 */
  function invalidate() { setChallenge(null); setCode('') }
  /** 只有账号密码校验成功才返回邮箱挑战，不向前端暴露管理员真实收件地址。 */
  async function send() {
    if (busy || cooldown) return
    setBusy(true)
    try { const value = await api.request<EmailChallenge>('/api/admin/login-code', { login, password }); setChallenge(value); setCode(''); setCooldown(value.retry_after); report(`验证码已发送至 ${value.recipient_hint || '管理员邮箱'}。`) }
    catch (error) { report(error instanceof Error ? error.message : '发送失败，请重试。') }
    finally { setBusy(false) }
  }
  return <Card className="business-form"><CardHeader><CardTitle>管理员登录</CardTitle></CardHeader><CardContent><form className="grid gap-4" onSubmit={async event => {
    event.preventDefault(); if (busy || !challenge) return
    setBusy(true)
    try { await signedIn(await api.request<Session>('/api/login', { kind: 'admin', login, password, challenge_id: challenge.challenge_id, code }), login); setPassword(''); setCode('') }
    catch (error) { report(error instanceof Error ? error.message : '登录失败，请重试。') }
    finally { setBusy(false) }
  }}>
    <div className="grid gap-2"><Label htmlFor={id + '-login'}>管理员账号</Label><Input id={id + '-login'} required value={login} disabled={busy} onChange={event => { setLogin(event.target.value); invalidate() }} autoComplete="username" /></div>
    <div className="grid gap-2"><Label htmlFor={id + '-password'}>密码</Label><Input id={id + '-password'} required type="password" value={password} disabled={busy} onChange={event => { setPassword(event.target.value); invalidate() }} autoComplete="current-password" /></div>
    <Button type="button" variant="outline" disabled={busy || cooldown > 0 || !login || !password} onClick={() => void send()}>{cooldown ? `${cooldown} 秒后可重发` : '发送登录验证码'}</Button>
    {challenge && <p className="nz-form-hint">验证码已发送至 {challenge.recipient_hint || '管理员邮箱'}，5 分钟内有效。</p>}
    <div className="grid gap-2"><Label htmlFor={id + '-code'}>邮箱验证码</Label><Input id={id + '-code'} required pattern="[0-9]{6}" inputMode="numeric" maxLength={6} value={code} disabled={busy || !challenge} autoComplete="one-time-code" onChange={event => setCode(event.target.value)} /></div>
    <Button disabled={busy || !challenge}>{busy ? '验证中…' : '登录管理中心'}</Button>
  </form></CardContent></Card>
}

/** 身份切换仅供旧工作台使用；专属入口固定身份，启动器仅使用玩家表单。 */
export function EmailAccess({ api, report, signedIn, only, onPlayerLogin, disabled }: {
  api: AccountApi; report: (value: unknown) => void; signedIn: (session: Session, identity: string) => Promise<void>
  only?: 'player' | 'admin'; onPlayerLogin?: (email: string, password: string) => Promise<void>; disabled?: boolean
}) {
  const [kind, setKind] = useState<'player' | 'admin'>(only || 'player'), [mode, setMode] = useState<Mode>('login')
  const [busy, setBusy] = useState(false)
  return <>
    {!only && <div className="auth-tabs" role="tablist" aria-label="登录身份"><Button role="tab" disabled={busy} aria-selected={kind === 'player'} variant={kind === 'player' ? 'default' : 'ghost'} onClick={() => setKind('player')}>玩家账号</Button><Button role="tab" disabled={busy} aria-selected={kind === 'admin'} variant={kind === 'admin' ? 'default' : 'ghost'} onClick={() => setKind('admin')}>管理员</Button></div>}
    {kind === 'admin' ? <AdminLogin api={api} report={report} signedIn={signedIn} /> : <><PlayerEmailForm key={mode} api={api} mode={mode} busy={busy} setBusy={setBusy} report={report} signedIn={signedIn} completed={() => setMode('login')} onPlayerLogin={onPlayerLogin} disabled={disabled} /><div className="auth-tabs">{(Object.keys(titles) as Mode[]).filter(value => value !== mode).map(value => <Button key={value} disabled={busy} variant="ghost" onClick={() => setMode(value)}>{titles[value]}</Button>)}</div><p className="muted-value">注册成功即创建角色并登录。找回密码不会解除账号封禁。</p></>}
  </>
}
