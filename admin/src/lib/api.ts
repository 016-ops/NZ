/** HTTP 契约集中在此：沿用旧字段，所有会话和重试信息只存在浏览器内存。 */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type Data = Record<string, Json>
export type Kind = 'admin' | 'player'
export type Role = 'owner' | 'users' | 'resources' | 'auditor' | 'player'
export interface Session { token: string; subject_id: string; kind: Kind; expires_in: number; character_required?: boolean }
export interface Status { profile: string; evidence: string; test_profile: boolean; retail: Record<string, string> }
export interface Metadata { request_id: string; idempotency_key: string; reason: string }
export interface Catalog { id: string; currencies: Record<string, { min: number; max: number }>; items: Record<string, Data>; offers: Record<string, Data>; maps: Record<string, { capacity: number }> }
export interface Room { id: string; owner: string; map_key: string; capacity: number; state: string; revision: number; match_id: string | null; invite_only?: boolean; members?: { account_id: string; slot: number; ready: number }[] }
export type ExtraAction = 'admin.create' | 'admin.update' | 'admin.password.reset' | 'admin.mfa.reset' | 'admin.recovery.rotate' | 'asset.batch.preview' | 'asset.batch.enqueue' | 'asset.batch.retry' | 'asset.review.resolve' | 'room.invite' | 'node.enroll' | 'pve.revive'
export type Action = 'campaign.save' | 'campaign.presentation.save' | 'campaign.checkin' | 'campaign.claim' | 'feedback.create' | 'feedback.reply' | 'feedback.status' | 'native.rules.preview' | 'native.rules.publish' | 'user.email.set' | ExtraAction | 'invite.create' | 'invite.revoke' | 'user.approve' | 'user.activate' | 'user.disable' | 'user.reset' | 'session.revoke' | 'sanction.create' | 'sanction.revoke' | 'character.create' | 'room.create' | 'room.join' | 'room.leave' | 'room.ready' | 'room.configure' | 'room.start' | 'room.cancel' | 'asset.grant' | 'asset.batch' | 'asset.compensate' | 'inventory.consume' | 'inventory.equip'

/** 管理员邮箱再认证的挑战和一次性证明；只允许当前操作在内存中短暂使用。 */
export interface StepUpChallenge { challenge_id: string; recipient_hint?: string; expires_in: number; retry_after: number; operation_summary?: { action: string; confirmation: string } }
export interface StepUpProof { current_password: string; challenge_id: string; code: string }
export interface StepUpRequest { action: string; data: Data; metadata: Metadata; sendCode(password: string): Promise<StepUpChallenge> }
export type StepUpHandler = (request: StepUpRequest) => Promise<StepUpProof>
export interface FeedbackAttachment { id: string; url: string; mime: string; width: number; height: number; bytes: number; created_at?: number }

/** 保留机器可判定的业务码，同时把界面消息转为中文；未知英文正文不会直接泄露到玩家界面。 */
export class ApiError extends Error {
  /** code与status供业务分支判断，message只保留本地化后可展示的文本。 */
  constructor(public code: string, message: string, public status: number) { super(localizedError(code, message)); this.name = 'ApiError' }
}
export function localizedError(code: string, message = '') {
  const labels: Record<string, string> = {
    NETWORK: '网络连接暂不可用，请检查连接后重试。',
    AUTH: '账号或密码不正确，请检查后重试。', LOGIN_REQUIRED: '请先登录账号。', EMAIL_NOT_VERIFIED: '请先验证邮箱。',
    EMAIL_CODE: '邮箱验证码不正确或已过期，请重新获取。', ADMIN_EMAIL_CODE: '验证码不正确、已过期或已使用，请重新发送。',
    REAUTH_REQUIRED: '此操作需要再次验证管理员邮箱。', MAIL_NOT_CONFIGURED: '邮箱服务尚未配置，请联系管理员。', MAIL_DELIVERY_FAILED: '邮件发送失败，请稍后重试。',
    FORBIDDEN: '当前账号没有执行此操作的权限。', VERSION_CONFLICT: '数据已变化，请刷新后重新确认。', SNAPSHOT_CHANGED: '仓库已更新，请刷新后继续读取。',
    NOT_FOUND: '请求的内容不存在或暂未开放。', RATE_LIMIT: '操作过于频繁，请稍后再试。', INVALID: '提交内容不符合要求，请检查表单。',
  }
  return /[\u3400-\u9fff]/.test(message) ? message : labels[code] || '操作未完成，请稍后重试；如仍有问题，请通过反馈中心联系管理员。'
}

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
  constructor(private session: Session | null, private expire: () => void, private stepUp?: StepUpHandler) {}
  /** 根据是否有正文发送 GET/POST，返回类型化 JSON；401 清理会话，其他错误保留业务码抛出。 */
  async request<T = Json>(path: string, data?: object): Promise<T> {
    const headers: Record<string, string> = {}
    if (this.session) headers.Authorization = `Bearer ${this.session.token}`
    if (data !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-NZ-Request'] = '1' }
    let response: Response
    try { response = await fetch(path, { headers, cache: 'no-store', method: data === undefined ? 'GET' : 'POST', body: data === undefined ? undefined : JSON.stringify(data) }) }
    catch { throw new ApiError('NETWORK', '', 0) }
    // 反向代理错误页或断开的测试服务可能返回 HTML；统一给出可读错误，不向界面泄漏原始正文。
    let result: { error?: string; message?: string }
    try { result = await response.json() }
    catch { throw new Error('服务暂时无法读取，请检查连接后重试。') }
    if (!response.ok) {
      if (response.status === 401) this.expire()
      throw new ApiError(result.error || 'REQUEST_FAILED', result.message || '', response.status)
    }
    return result as T
  }
  /** 按幂等键冻结最终命令正文，失败保留供重试；成功后删除冻结副本并返回后端结果。 */
  async command(action: Action, data: Data, metadata: Metadata) {
    const key = metadata.idempotency_key
    let payload = this.pending.get(key)
    if (!payload) { payload = { ...data, ...metadata, action }; this.pending.set(key, payload) }
    let result: Json
    try { result = await this.request('/api/command', payload) }
    catch (error) {
      if (!(error instanceof ApiError) || error.code !== 'REAUTH_REQUIRED' || !this.stepUp) throw error
      // 服务端先拒绝未验证写入；挑战绑定原业务正文和幂等键，认证材料不进入冻结副本。
      const business = Object.fromEntries(Object.entries(payload).filter(([field]) => !['action', 'request_id', 'idempotency_key', 'reason', 'current_password', 'challenge_id', 'code', 'otp_code'].includes(field))) as Data
      const proof = await this.stepUp({ action, data: business, metadata,
        sendCode: current_password => this.request<StepUpChallenge>('/api/admin/step-up-code', { action, payload: business, metadata, current_password }) })
      result = await this.request('/api/command', { ...payload, ...proof })
    }
    this.pending.delete(key)
    return result
  }
  /** 二进制上传仅向固定同源地址发送，沿用已有鉴权与一次性上传键；不把图片写到浏览器持久存储。 */
  private async uploadImage(path: string, file: File, uploadKey: string): Promise<FeedbackAttachment> {
    if (file.size > 5 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error('请选择不超过 5 MiB 的 PNG、JPEG 或 WebP 图片。')
    const body = new FormData(); body.append('file', file); body.append('upload_key', uploadKey)
    let response: Response
    try { response = await fetch(path, { method: 'POST', headers: { ...(this.session ? { Authorization: `Bearer ${this.session.token}` } : {}), 'X-NZ-Request': '1' }, body, cache: 'no-store' }) }
    catch { throw new ApiError('NETWORK', '', 0) }
    let value: FeedbackAttachment & { error?: string; message?: string }
    try { value = await response.json() } catch { throw new Error('图片服务暂时无法读取，请稍后重试。') }
    if (!response.ok) { if (response.status === 401) this.expire(); throw new ApiError(value.error || 'UPLOAD_FAILED', value.message || '', response.status) }
    return value
  }
  /** 反馈图片路径只由已知工单ID构造；私图读取另走带鉴权的二进制接口。 */
  uploadFeedbackAttachment(ticketId: string, file: File, uploadKey: string) { return this.uploadImage(`/api/feedback/${encodeURIComponent(ticketId)}/attachments`, file, uploadKey) }
  /** 活动封面走管理员公共媒体上传，不复用私有反馈图片或接受外部URL。 */
  uploadCampaignCover(file: File, uploadKey: string) { return this.uploadImage('/api/admin/media', file, uploadKey) }
  /** 反馈截图经鉴权读取为临时Blob；界面负责撤销 object URL，公开页面不可访问。 */
  async readFeedbackAttachment(id: string): Promise<Blob> {
    const response = await fetch(`/api/feedback/attachments/${encodeURIComponent(id)}`, { headers: this.session ? { Authorization: `Bearer ${this.session.token}` } : {}, cache: 'no-store' })
    if (!response.ok) { if (response.status === 401) this.expire(); throw new Error('截图读取失败，请确认登录状态和工单权限。') }
    return response.blob()
  }

}
