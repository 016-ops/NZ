/** 门户与桌面共享的运营契约；业务凭据由各自传输层持有，不进入页面地址或持久存储。 */
import { useCallback, useEffect, useState } from 'react'
import type { Data, Metadata, FeedbackAttachment } from './api'

export interface RuntimeApi {
  request<T = unknown>(path: string, data?: object): Promise<T>
  command(action: string, data: Data, metadata: Metadata): Promise<unknown>
  uploadFeedbackAttachment?(ticketId: string, file: File, uploadKey: string): Promise<FeedbackAttachment>
  readFeedbackAttachment?(id: string): Promise<Blob>
  uploadCampaignCover?(file: File, uploadKey: string): Promise<FeedbackAttachment>
  readCampaignCover?(mediaId: string): Promise<Blob>
}
export interface PlayMap {
  map_id: number; name: string; mode_id: number; submodes: number[]; available_submodes?: number[]
  kind: string; enabled?: boolean
}
export interface PortalInfo {
  profile: string; test_profile: boolean; maps: PlayMap[]
  capabilities: Record<string, boolean>; campaigns: Campaign[]
}
export type RewardType = 'CHECKIN_DAYS' | 'LAUNCHER_LOGIN_DAYS' | 'PLAYTIME_SECONDS' | 'CLEAR_COUNT' | 'LEADERBOARD'
export interface Reward {
  key: string; title: string; type: RewardType; threshold?: number; priority?: number
  rank_from?: number; rank_to?: number; eligible?: boolean; claimed?: boolean
  delivery?: 'AUTOMATIC' | 'CLAIM'; delivery_status?: 'PENDING' | 'FAILED' | 'SUCCEEDED' | null
  changes: { type: 'item'; item_key: string; quantity: number; duration_seconds?: number }[]
  map_ids?: number[]; difficulties?: number[]; player_counts?: number[]
}
export interface CampaignPresentation { slug: string; title: string; summary: string; theme: 'default' | 'tactical' | 'anniversary'; cover_id?: string | null; cover_url?: string | null; sections: { key: string; kind: 'text' | 'rewards' | 'leaderboards'; title: string; body?: string }[]; revision: number }
export interface Campaign {
  presentation?: CampaignPresentation
  id: string; title: string; state: 'DRAFT' | 'PUBLISHED' | 'PAUSED' | 'ENDED'; revision: number
  test_only?: boolean
  definition: { starts_at: number; ends_at: number; timezone: string; rewards: Reward[] }
  progress?: { checkin_days: number; launcher_login_days: number; playtime_seconds: number; clear_count: number }
  rewards?: Reward[]
  payouts?: { pending: number; failed: number; succeeded: number }
}
export interface InventoryItem {
  gid?: string | number; id?: string; instance_id: string; item_id?: number; item_key?: string; name?: string
  quantity: number; expires_at?: number | null; version?: number; state?: string | Record<string, unknown>; equipment_slot?: string | number
}
export interface ShopOffer {
  commodity_id: number; label: string; enabled: boolean; sort_order: number; prices: number[]
  period_hours: number[]; period_editable: boolean; currency: string; item_ids: number[]; revision: number
}
export interface ShopList { revision: number; total: number; items: ShopOffer[] }
export interface Ranking {
  entries: { rank: number; result_id: string; member_ids: string[]; nicknames: string[]; duration_ms: number; settled_at: number }[]
  finalized: boolean; test_only: boolean; native_results_enabled: boolean
}
export const rewardLabels: Record<RewardType, string> = {
  CHECKIN_DAYS: '累计签到', LAUNCHER_LOGIN_DAYS: '启动器登录', PLAYTIME_SECONDS: '游玩时长', CLEAR_COUNT: '成功通关', LEADERBOARD: '赛季排行',
}
export const campaignLabels: Record<Campaign['state'], string> = { DRAFT: '草稿', PUBLISHED: '进行中', PAUSED: '已暂停', ENDED: '已结束' }

/** 把服务端秒数按本机日期呈现；缺失时不伪造永久期限或采样时间。 */
export function formatDate(value?: number | null, fallback = '暂无', timeZone?: string) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false, ...(timeZone ? { timeZone } : {}) }) : fallback
}

/** 模式类型使用玩家熟悉的中文；遇到未登记类型时保留原值，不猜测具体玩法。 */
export function mapKindLabel(kind: string) {
  return ({ hunt: '僵尸猎场', tower_defense: '塔防战', survival: '生存战', defense: '保卫战' } as Record<string, string>)[kind] || '模式待确认'
}

/** 排行统一展示毫秒精度；排序与入榜资格由服务端决定。 */
export function formatDuration(milliseconds: number) {
  const seconds = Math.max(0, milliseconds) / 1000
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${(seconds % 60).toFixed(3).padStart(6, '0')}`
}

/** 可取消的只读查询：切换筛选或卸载后，旧响应不能覆盖当前页面；写操作由明确用户交互触发。 */
export function useResource<T>(api: RuntimeApi, path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    let active = true
    setData(null); setError(null)
    if (!path) { setLoading(false); return }
    setLoading(true)
    api.request<T>(path).then(value => { if (active) setData(value) })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '读取失败，请稍后重试。') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api, path, revision])
  return { data, error, loading, refresh }
}

/** 公开读取和登录后读取共用一套接口形状；桌面可注入固定白名单的本机代理。 */
export function publicPath(view: 'campaigns' | 'leaderboards', authenticated: boolean) {
  return authenticated ? `/api/views/${view}` : `/api/public/${view}`
}

/** 活动详情只接受单个URL片段；未知路径不误映射成另一活动，返回中心时使用null。 */
export function activitySlugFromPath(path: string): string | null {
  const match = /^\/activities\/([a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?)\/?$/.exec(path)
  return match ? match[1] : null
}

/** 普通账号页面使用中文状态；未登记的新状态明确待确认，原枚举仍保留在技术详情。 */
export function accountStatusLabel(value?: unknown) {
  return ({ ACTIVE: '正常', PENDING_REVIEW: '待审核', DISABLED: '已停用', BANNED: '封禁中' } as Record<string, string>)[String(value)] || '状态待确认'
}
/** 库存界面不直接显示原生状态代码；复杂状态对象继续保留在服务端，不在卡片上猜测细节。 */
export function inventoryStateLabel(value?: string) {
  return ({ ACTIVE: '可用', AVAILABLE: '可用', READY: '可用', EXPIRED: '已过期', CONSUMED: '已使用', DELETED: '已回收', DISABLED: '已停用' } as Record<string, string>)[value || ''] || ''
}

/** 难度中文名未核实前只显示当前选项序号；value保持服务端原值，禁止按编号猜测普通/英雄。 */
export function difficultyChoices(values: readonly number[]) {
  return values.map((value, index) => ({ value, label: `待确认难度 · 选项 ${index + 1}` }))
}

/** 币种未有可信中文名时不猜测；装备槽位只沿用已有已核实标签，其余明确待确认。 */
export function assetCodeLabel(field: 'currency' | 'slot' | 'equipment_slot', value: unknown) {
  if (field === 'currency') return '币种名称待确认'
  return value === 'primary' ? '主武器' : '装备槽位待确认'
}
