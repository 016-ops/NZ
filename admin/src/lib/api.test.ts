/** 验证真实业务风险：响应丢失后的重试必须复用键，修改内容后必须换键。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Api, RetryEnvelope } from './api'

describe('submission idempotency', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('retains metadata on failure, changes it with payload, clears it on success', () => {
    const retry = new RetryEnvelope()
    const first = retry.prepare({ amount: 10 }, '测试')
    expect(retry.prepare({ amount: 10 }, '测试')).toEqual(first)
    const changed = retry.prepare({ amount: 11 }, '测试')
    expect(changed.idempotency_key).not.toBe(first.idempotency_key)
    retry.success()
    expect(retry.prepare({ amount: 11 }, '测试').idempotency_key).not.toBe(changed.idempotency_key)
  })
  it('freezes the actual invite expiry after a lost response', async () => {
    // 第一次响应丢失后，表单重新换算当前时间；HTTP 请求仍必须发送首次冻结的有效期。
    const fetcher=vi.fn().mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValue({ok:true,status:200,json:async () => ({status:'COMMITTED'})})
    vi.stubGlobal('fetch',fetcher)
    const api=new Api(null,()=>{})
    const meta={request_id:'request',idempotency_key:'invite-key',reason:'测试邀请'}
    await expect(api.command('invite.create',{max_uses:1,expires_at:1000},meta)).rejects.toThrow('网络连接')
    await api.command('invite.create',{max_uses:1,expires_at:1100},meta)
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body)
  })
})

/** Owner再验证必须绑定原命令和同一幂等键；验证码只在最终命令中出现，不污染业务摘要。 */
describe('管理员邮箱再验证', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('在服务端要求验证后绑定原业务正文并提交一次性证明', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ error: 'REAUTH_REQUIRED', message: 'Email challenge required' }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ challenge_id: 'challenge', retry_after: 60, expires_in: 300 }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ status: 'COMMITTED' }) })
    vi.stubGlobal('fetch', fetcher)
    const metadata = { request_id: 'request', idempotency_key: 'same-key', reason: '管理员测试操作' }
    const api = new Api(null, () => {}, async request => {
      expect(request.data).toEqual({ batch_id: 'batch' })
      expect(request.metadata).toEqual(metadata)
      const challenge = await request.sendCode('private-test-password')
      return { current_password: 'private-test-password', challenge_id: challenge.challenge_id, code: '123456' }
    })
    await api.command('asset.batch.retry', { batch_id: 'batch' }, metadata)
    const challengeBody = JSON.parse(fetcher.mock.calls[1][1].body)
    expect(challengeBody.payload).toEqual({ batch_id: 'batch' })
    expect(challengeBody.metadata).toEqual(metadata)
    const finalBody = JSON.parse(fetcher.mock.calls[2][1].body)
    expect(finalBody.idempotency_key).toBe(metadata.idempotency_key)
    expect(finalBody.code).toBe('123456')
    expect(finalBody.challenge_id).toBe('challenge')
  })
})
