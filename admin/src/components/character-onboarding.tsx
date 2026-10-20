/** 已激活但缺少角色的旧账号补建入口；完成前不展示仓库、奖励或启动游戏等依赖角色的功能。 */
import { useRef, useState } from 'react'
import { RetryEnvelope } from '@/lib/api'
import type { RuntimeApi } from '@/lib/platform'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Panel, Notice } from './platform-pages'
import { useActionFeedback } from './action-feedback'

export function CharacterOnboarding({ api, onCompleted }: { api: RuntimeApi; onCompleted: () => void | Promise<void> }) {
  const [nickname, setNickname] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const retry = useRef(new RetryEnvelope())
  const { notify } = useActionFeedback()
  return <section className="nz-page nz-onboarding"><Panel title="给战友一个称呼"><p>你的账号已经就绪。完成游戏昵称后，即可查看仓库、参与活动并从启动器登录游戏。</p><form className="grid gap-4" onSubmit={async event => {
    event.preventDefault(); if (busy) return
    setBusy(true); setError('')
    try { const payload = { nickname: nickname.trim() }; await api.command('character.create', payload, retry.current.prepare(payload, '玩家补建游戏角色')); retry.current.success(); notify({ title: '角色已创建', message: '欢迎归队，下一场冒险从这里开始。', tone: 'success' }); await onCompleted() }
    catch (reason) { const message = reason instanceof Error ? reason.message : '角色创建失败，请重试。'; setError(message); notify({ title: '角色尚未创建', message, tone: 'error' }) }
    finally { setBusy(false) }
  }}><Label htmlFor="onboard-nickname">游戏昵称</Label><Input id="onboard-nickname" required minLength={1} maxLength={24} disabled={busy} value={nickname} onChange={event => setNickname(event.target.value)} /><Button disabled={busy}>{busy ? '创建中…' : '完成角色创建'}</Button>{error && <Notice error>{error}</Notice>}</form></Panel></section>
}
