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
    await expect(api.command('invite.create',{max_uses:1,expires_at:1000},meta)).rejects.toThrow('connection lost')
    await api.command('invite.create',{max_uses:1,expires_at:1100},meta)
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body)
  })
})
