/** Web容忍额外字段而本机桥严格拒绝；共用表单必须符合每个目的的最小账号正文。 */
import { describe, expect, it, vi } from 'vitest'
import { playerAccessPayload, submitPlayerEmailRequest } from '@/components/email-access'

describe('两端邮箱账号正文合同', () => {
  /** 补验邮箱省略密码，注册携昵称且不传邀请；找回仅携带所需的新密码。 */
  it('按用途保持严格桥允许的字段，不在verify中发送空密码', () => {
    const values = { email: ' player@example.invalid ', password: 'private-test-password', nickname: ' 战友 ', code: '123456', challenge_id: 'challenge' }
    expect(playerAccessPayload('verify', values)).toEqual({ email: 'player@example.invalid', code: '123456', challenge_id: 'challenge' })
    expect(playerAccessPayload('register', values)).toEqual({ email: 'player@example.invalid', password: values.password, nickname: '战友', code: '123456', challenge_id: 'challenge' })
    expect(Object.keys(playerAccessPayload('reset', values)).sort()).toEqual(['challenge_id', 'code', 'email', 'password'])
  })
  /** 空确认与不一致均在共享请求边界阻断；不产生注册或找回密码API调用。 */
  it('密码确认未通过时不调用API', async () => {
    const request = vi.fn().mockResolvedValue({ ok: true })
    const metadata = { request_id: 'test-request', idempotency_key: 'test-idempotency', reason: '测试密码确认' }
    for (const mode of ['register', 'reset'] as const) {
      const payload = playerAccessPayload(mode, { email: 'player@example.invalid', password: 'private-test-password', nickname: '战友', code: '123456', challenge_id: 'challenge' })
      await expect(submitPlayerEmailRequest({ request }, mode, payload, '', metadata)).rejects.toThrow('请再次输入密码')
      await expect(submitPlayerEmailRequest({ request }, mode, payload, 'different-password', metadata)).rejects.toThrow('两次输入的密码不一致')
    }
    expect(request).not.toHaveBeenCalled()
  })
  /** 确认一致才提交既有字段；HTTP和严格本机桥都不会收到任何额外确认密码字段。 */
  it('密码确认一致才提交且不扩展后端正文', async () => {
    const request = vi.fn().mockResolvedValue({ ok: true })
    const metadata = { request_id: 'test-request', idempotency_key: 'test-idempotency', reason: '测试密码确认' }
    const values = { email: 'player@example.invalid', password: 'private-test-password', nickname: '战友', code: '123456', challenge_id: 'challenge' }
    for (const mode of ['register', 'reset'] as const) {
      const payload = playerAccessPayload(mode, values)
      await submitPlayerEmailRequest({ request }, mode, payload, values.password, metadata)
      expect(request).toHaveBeenLastCalledWith('/api/player/' + (mode === 'register' ? 'register' : 'password-reset'), { ...payload, ...metadata })
    }
    expect(request).toHaveBeenCalledTimes(2)
    for (const call of request.mock.calls) expect(Object.keys(call[1]).sort()).toEqual([...Object.keys(playerAccessPayload(call[0] === '/api/player/register' ? 'register' : 'reset', values)), ...Object.keys(metadata)].sort())
  })
})
