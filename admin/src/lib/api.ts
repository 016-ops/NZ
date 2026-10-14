/** HTTP 契约集中在此：沿用旧字段，所有会话和重试信息只存在浏览器内存。 */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type Data = Record<string, Json>
export type Kind = 'admin' | 'player'
export type Role = 'owner' | 'users' | 'resources' | 'auditor' | 'player'
export interface Session { token: string; subject_id: string; kind: Kind; expires_in: number }
export interface Status { profile: string; evidence: string; test_profile: boolean; retail: Record<string, string> }
export interface Metadata { request_id: string; idempotency_key: string; reason: string }
export interface Catalog { id: string; currencies: Record<string, { min: number; max: number }>; items: Record<string, Data>; offers: Record<string, Data>; maps: Record<string, { capacity: number }> }
export interface Room { id: string; owner: string; map_key: string; capacity: number; state: string; revision: number; match_id: string | null }
export type Action = 'invite.create' | 'invite.revoke' | 'user.approve' | 'user.activate' | 'user.disable' | 'user.reset' | 'session.revoke' | 'sanction.create' | 'sanction.revoke' | 'character.create' | 'room.create' | 'room.join' | 'room.leave' | 'room.ready' | 'room.configure' | 'room.start' | 'room.cancel' | 'asset.grant' | 'asset.batch' | 'asset.compensate' | 'shop.purchase' | 'inventory.consume' | 'inventory.equip'

/** 每个提交入口维护自己的重试槽：内容改变才换键，成功后清空，失败保留。 */
export class RetryEnvelope {
  private payload = ''
  private metadata: Metadata | null = null
  /** 输入表单内容和原因；相同内容失败重试返回原元数据，内容改变时生成新的业务幂等键。 */
  prepare(data: unknown, reason: string): Metadata {
    const payload = JSON.stringify(data)
    if (!this.metadata || payload !== this.payload) {
      this.payload = payload
      this.metadata = { request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID(), reason }
    }
    return this.metadata
  }
  /** 成功响应已确认提交，释放重试槽；下一次提交即使内容相同也属于新操作。 */
  success() { this.payload = ''; this.metadata = null }
}

/** 401 通知根组件清理会话；错误保留后端业务码，禁止浏览器自动重试写请求。 */
export class Api {
  // 时长转换会依赖当前时间，因此按幂等键冻结最终 HTTP payload，避免超时重试改变 expires_at。
  private pending = new Map<string, object>()
  /** 保存内存会话与过期回调；不向 localStorage、Cookie 或 URL 写入 Bearer。 */
  constructor(private session: Session | null, private expire: () => void) {}
  /** 根据是否有正文发送 GET/POST，返回类型化 JSON；401 清理会话，其他错误保留业务码抛出。 */
  async request<T = Json>(path: string, data?: object): Promise<T> {
    const headers: Record<string, string> = {}
    if (this.session) headers.Authorization = `Bearer ${this.session.token}`
    if (data !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-NZ-Request'] = '1' }
    const response = await fetch(path, { headers, cache: 'no-store', method: data === undefined ? 'GET' : 'POST', body: data === undefined ? undefined : JSON.stringify(data) })
    const result = await response.json()
    if (!response.ok) {
      if (response.status === 401) this.expire()
      throw new Error(`${result.error}: ${result.message}`)
    }
    return result as T
  }
  /** 按幂等键冻结最终命令正文，失败保留供重试；成功后删除冻结副本并返回后端结果。 */
  async command(action: Action, data: Data, metadata: Metadata) {
    const key = metadata.idempotency_key
    let payload = this.pending.get(key)
    if (!payload) { payload = { ...data, ...metadata, action }; this.pending.set(key, payload) }
    const result = await this.request('/api/command', payload)
    this.pending.delete(key)
    return result
  }
}
