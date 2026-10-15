/** 玩家邮箱验证码注册、旧邮箱验证和密码恢复；敏感输入仅驻留内存，不与管理员 MFA 混用。 */
import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BusinessForm } from '@/components/business'
import { Api, RetryEnvelope, type Session } from '@/lib/api'

type Mode = 'login' | 'register' | 'reset' | 'verify'
const titles: Record<Mode, string> = { login:'登录', register:'验证码注册', reset:'找回密码', verify:'验证已有邮箱' }

/** 一个独立目的对应一个挑战；更换地址或邀请码即丢弃旧挑战，防止表单误用其他收件人的码。 */
function PlayerEmailForm({ api, mode, busy, setBusy, report, signedIn, completed }: {
  api: Api; mode: Mode; busy: boolean; setBusy: (value: boolean) => void; report: (value: unknown) => void;
  signedIn: (session: Session, identity: string) => Promise<void>; completed: () => void
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [invitation, setInvitation] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState('')
  const [cooldown, setCooldown] = useState(0)
  const retry = useRef(new RetryEnvelope())
  const id = useId()
  useEffect(() => {
    if (!cooldown) return
    const timer = window.setTimeout(() => setCooldown(value => Math.max(0, value-1)), 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])
  /** 服务端持久控制额度；等待期间锁定收件字段与入口，防止旧响应把挑战写入已切换的表单。 */
  async function sendCode() {
    if (busy || cooldown) return
    setBusy(true)
    try {
      const result = await api.request<{challenge_id: string; retry_after: number; message: string}>('/api/player/email-code', {email, purpose:mode, invitation})
      setChallenge(result.challenge_id); setCode(''); setCooldown(result.retry_after); report(result.message)
    } catch (error) { report(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <Card className="business-form"><CardHeader><CardTitle>{titles[mode]}</CardTitle></CardHeader><CardContent>
    <form className="grid gap-4" onSubmit={async event => {
      event.preventDefault(); if (busy) return
      setBusy(true)
      try {
        if (mode === 'login') await signedIn(await api.request<Session>('/api/player/login', {email, password}), email)
        else {
          const values = {email, password, invitation, code, challenge_id:challenge}
          const result = await api.request<{message?: string}>('/api/player/' + (mode === 'register' ? 'register' : mode === 'reset' ? 'password-reset' : 'email-verify'),
            {...values, ...retry.current.prepare(values, titles[mode])})
          retry.current.success(); report(result.message || '邮箱已验证，注册申请已提交，批准后可登录。'); completed()
        }
        setPassword(''); setCode('')
      } catch (error) { report(error instanceof Error ? error.message : String(error)) }
      finally { setBusy(false) }
    }}>
      <div className="grid gap-2"><Label htmlFor={id+'-email'}>邮箱地址</Label><Input id={id+'-email'} name="email" type="email" required disabled={busy} value={email} maxLength={254}
        autoComplete="email" onChange={event => {setEmail(event.target.value); setChallenge(''); setCode('')}} /></div>
      {mode === 'register' && <div className="grid gap-2"><Label htmlFor={id+'-invitation'}>邀请令牌</Label><Input id={id+'-invitation'} required disabled={busy} value={invitation} maxLength={128}
        onChange={event => {setInvitation(event.target.value); setChallenge(''); setCode('')}} /></div>}
      {mode !== 'verify' && <div className="grid gap-2"><Label htmlFor={id+'-password'}>{mode === 'login' ? '密码' : '新密码（12–128 字符）'}</Label><Input id={id+'-password'} name="password" type="password" required disabled={busy}
        minLength={mode === 'login' ? undefined : 12} maxLength={128} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} /></div>}
      {mode !== 'login' && <div className="grid gap-2"><Label htmlFor={id+'-code'}>邮箱验证码（10 分钟有效）</Label>
        <Input id={id+'-code'} name="code" required disabled={busy} pattern="[0-9]{6}" inputMode="numeric" maxLength={6} autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} />
        <Button type="button" variant="outline" disabled={busy || cooldown > 0 || !email || (mode === 'register' && !invitation)} onClick={() => void sendCode()}>{cooldown ? `${cooldown} 秒后可重发` : '发送验证码'}</Button>
      </div>}
      <Button type="submit" disabled={busy || (mode !== 'login' && !challenge)}>{busy ? '处理中…' : titles[mode]}</Button>
    </form>
  </CardContent></Card>
}

/** 玩家验证码入口保留邀请审批；管理员继续使用原账号与动态 MFA，找回功能不能重置管理员。 */
export function EmailAccess({ api, report, signedIn }: {
  api: Api; report: (value: unknown) => void; signedIn: (session: Session, identity: string) => Promise<void>
}) {
  const [kind, setKind] = useState<'player' | 'admin'>('player')
  const [mode, setMode] = useState<Mode>('login')
  // 请求完成前保留同一份表单和提交内容，避免切换身份/用途后由旧响应覆盖当前提示或验证码。
  const [playerBusy, setPlayerBusy] = useState(false)
  return <>
    <div className="auth-tabs" role="tablist" aria-label="登录身份">
      <Button role="tab" disabled={playerBusy} aria-selected={kind === 'player'} variant={kind === 'player' ? 'default' : 'ghost'} onClick={() => {setKind('player'); setMode('login')}}>玩家邮箱</Button>
      <Button role="tab" disabled={playerBusy} aria-selected={kind === 'admin'} variant={kind === 'admin' ? 'default' : 'ghost'} onClick={() => setKind('admin')}>管理员</Button>
    </div>
    {kind === 'admin' ? <BusinessForm key="admin" title="登录" fields={[
      {name:'login',label:'管理员账号'}, {name:'password',label:'密码',type:'password'}, {name:'otp_code',label:'动态验证码',optional:true},
    ]} report={report} submit={async values => signedIn(await api.request<Session>('/api/login', {...values, kind:'admin'}), values.login)} /> : <>
      <PlayerEmailForm key={mode} api={api} mode={mode} busy={playerBusy} setBusy={setPlayerBusy} report={report} signedIn={signedIn} completed={() => setMode('login')} />
      <div className="auth-tabs">{(Object.keys(titles) as Mode[]).filter(value => value !== mode).map(value => <Button key={value} disabled={playerBusy} variant="ghost" onClick={() => setMode(value)}>{titles[value]}</Button>)}</div>
      <p className="muted-value">邮箱验证码用于确认地址归属。新账号仍需邀请与管理员批准；密码恢复不会解除封禁。</p>
    </>}
  </>
}
