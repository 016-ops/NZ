/** Web容忍额外字段而本机桥严格拒绝；共用表单必须符合每个目的的最小账号正文。 */
import { describe, expect, it } from 'vitest'
import { playerAccessPayload } from '@/components/email-access'

describe('两端邮箱账号正文合同', () => {
  /** 补验邮箱省略密码，注册携昵称且不传邀请；找回仅携带所需的新密码。 */
  it('按用途保持严格桥允许的字段，不在verify中发送空密码', () => {
    const values = { email: ' player@example.invalid ', password: 'private-test-password', nickname: ' 战友 ', code: '123456', challenge_id: 'challenge' }
    expect(playerAccessPayload('verify', values)).toEqual({ email: 'player@example.invalid', code: '123456', challenge_id: 'challenge' })
    expect(playerAccessPayload('register', values)).toEqual({ email: 'player@example.invalid', password: values.password, nickname: '战友', code: '123456', challenge_id: 'challenge' })
    expect(Object.keys(playerAccessPayload('reset', values)).sort()).toEqual(['challenge_id', 'code', 'email', 'password'])
  })
})
