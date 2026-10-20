/** Owner 敏感操作的邮箱再验证弹窗；挑战绑定原正文，取消或卸载都会拒绝尚未执行的操作。 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Button } from './ui/button'
import type { StepUpChallenge, StepUpHandler, StepUpProof, StepUpRequest } from '@/lib/api'

/** 只展示已登记的动作名与目标摘要；新密码、认证材料及任意业务正文不能进入确认文案。 */
export function stepUpDescription(action: string, data: Record<string, unknown>) {
  const actions: Record<string, string> = { 'admin.create': '创建管理员', 'admin.update': '调整管理员权限或状态', 'admin.password.reset': '重置管理员密码',
    'user.email.set': '修改玩家邮箱', 'node.enroll': '登记游戏节点', 'asset.batch': '批量发放资产', 'asset.compensate': '补偿资产',
    'asset.batch.enqueue': '创建持久发放批次', 'asset.batch.retry': '重试失败发放目标', 'asset.review.resolve': '记录资产复核决定', 'asset.grant': '发放重要资产' }
  let target = '请核对原页面中已确认的目标与内容。'
  if (action === 'admin.create' && typeof data.login === 'string') target = '管理员账号：' + data.login
  else {
    for (const [key, label] of [['target_account_id', '玩家'], ['admin_id', '管理员'], ['batch_id', '批次'], ['operation_id', '资产操作'], ['node_id', '节点']]) {
      if (typeof data[key] === 'string') { target = label + '编号尾号：' + String(data[key]).slice(-8); break }
    }
  }
  return { action: actions[action] || '敏感管理操作', target }
}

/** 返回 Api 可注入的处理器和弹窗；不保存管理员密码/验证码到任何浏览器存储。 */
export function useAdminStepUp() {
  const [request, setRequest] = useState<StepUpRequest | null>(null)
  const [password, setPassword] = useState(''), [code, setCode] = useState('')
  const [challenge, setChallenge] = useState<StepUpChallenge | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [cooldown, setCooldown] = useState(0)
  const pending = useRef<{ resolve: (proof: StepUpProof) => void; reject: (error: Error) => void } | null>(null)
  const description = request ? stepUpDescription(request.action, request.data) : null
  const fingerprint = challenge?.operation_summary?.action === request?.action && /^[a-f0-9]{12}$/.test(challenge?.operation_summary?.confirmation || '') ? challenge?.operation_summary?.confirmation : null
  const handler: StepUpHandler = useCallback(value => new Promise((resolve, reject) => {
    if (pending.current) { reject(new Error('请先完成当前邮箱验证。')); return }
    pending.current = { resolve, reject }; setRequest(value); setPassword(''); setCode(''); setChallenge(null); setError(''); setCooldown(0)
  }), [])
  useEffect(() => () => { pending.current?.reject(new Error('页面已关闭，本次操作未提交。')); pending.current = null }, [])
  useEffect(() => { if (!cooldown) return; const timer = setTimeout(() => setCooldown(value => value - 1), 1000); return () => clearTimeout(timer) }, [cooldown])
  /** 取消不会发送原命令；只销毁当前内存认证材料，服务器挑战由过期时间自动失效。 */
  function cancel() { if (busy) return; pending.current?.reject(new Error('已取消邮箱验证，本次操作未提交。')); pending.current = null; setRequest(null); setPassword(''); setCode(''); setChallenge(null) }
  /** 通过Api提供的受限回调申请绑定挑战，失败时保留待操作但不提交原命令。 */
  async function send() {
    if (!request || busy || cooldown) return
    setBusy(true); setError('')
    try { const next = await request.sendCode(password); setChallenge(next); setCode(''); setCooldown(next.retry_after) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '验证码发送失败。') }
    finally { setBusy(false) }
  }
  const dialog = <Dialog open={!!request} onOpenChange={open => { if (!open) cancel() }}><DialogContent className="nz-dialog"><DialogHeader><DialogTitle>验证管理员邮箱</DialogTitle><DialogDescription>此操作涉及账号或重要资产。请输入当前密码，并使用发送至管理员邮箱的验证码确认。</DialogDescription></DialogHeader><form className="grid gap-4" onSubmit={event => {
    event.preventDefault(); if (!challenge || !pending.current) return
    pending.current.resolve({ current_password: password, challenge_id: challenge.challenge_id, code }); pending.current = null
    setRequest(null); setPassword(''); setCode(''); setChallenge(null)
  }}>
    {description && <div className="nz-stepup-summary"><strong>{description.action}</strong><p>{description.target}</p></div>}
    <Label htmlFor="owner-step-password">当前管理员密码</Label><Input id="owner-step-password" required type="password" autoComplete="off" disabled={busy} value={password} onChange={event => { setPassword(event.target.value); setChallenge(null); setCode('') }} />
    <Button type="button" variant="outline" disabled={busy || !password || cooldown > 0} onClick={() => void send()}>{busy ? '发送中…' : cooldown ? `${cooldown} 秒后可重发` : '发送验证邮件'}</Button>
    {challenge && <p className="nz-form-hint">验证码发送至 {challenge.recipient_hint || '管理员邮箱'}，5 分钟内有效。</p>}
    {fingerprint && <p className="nz-form-hint">操作指纹：{fingerprint}。请与邮件中的指纹核对后继续。</p>}
    <Label htmlFor="owner-step-code">邮箱验证码</Label><Input id="owner-step-code" required pattern="[0-9]{6}" inputMode="numeric" maxLength={6} autoComplete="one-time-code" disabled={!challenge || busy} value={code} onChange={event => setCode(event.target.value)} />
    {error && <p role="alert" className="nz-form-error">{error}</p>}
    <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={cancel}>取消</Button><Button disabled={busy || !challenge}>验证并继续</Button></DialogFooter>
  </form></DialogContent></Dialog>
  return { handler, dialog }
}
