/** 玩家与 Launcher 共享的活动、榜单与仓库；所有可见记录来自当前接口，不生成示例成绩或资产。 */
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, ArrowRight, Award, CalendarCheck, Check, ChevronRight, Clock3, Gift, LoaderCircle, Package, RefreshCw, ShieldCheck, Trophy, Users } from 'lucide-react'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { RetryEnvelope, type Data } from '@/lib/api'
import { campaignCoverSource } from '@/lib/campaign-cover'
import { ItemArtwork, itemName } from './item-artwork'
import { CampaignCover } from './campaign-cover'
import { useActionFeedback } from './action-feedback'
import { difficultyChoices, inventoryStateLabel, campaignLabels, formatDate, formatDuration, publicPath, rewardLabels, useResource, type Campaign, type InventoryItem, type PlayMap, type Ranking, type Reward, type RuntimeApi } from '@/lib/platform'

/** 查询失败和业务反馈使用独立视觉，不以成功色展示服务端拒绝。 */
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`nz-notice ${error ? 'is-error' : ''}`} role={error ? 'alert' : 'status'}>{error ? <ShieldCheck size={17} /> : <Check size={17} />}<div>{children}</div></div>
}

/** 无数据状态说明下一步，不用占位数字冒充已发布结果。 */
export function Empty({ title, children, icon = <Package size={34} /> }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return <div className="nz-empty"><span className="nz-empty-symbol">{icon}</span><h3>{title}</h3>{children && <p>{children}</p>}</div>
}

/** 统一页面标题、说明和可选操作，供网页及桌面复用。 */
export function PageHeading({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return <div className="nz-page-heading"><div><h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</div>
}

/** 独立幂等提交按钮；明确确认后才写入，失败保留原键，提交成功后刷新失败也不重放写入。 */
export function CommandButton({ api, action, data, children, reason, confirm, disabled, unavailableReason, resultData, onDone, variant = 'default' }: {
  api: RuntimeApi; action: string; data: Data; children: ReactNode; reason: string; confirm?: string; disabled?: boolean
  unavailableReason?: string; resultData?: unknown
  onDone?: (value: unknown) => void; variant?: 'default' | 'outline' | 'ghost' | 'destructive'
}) {
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const feedback = useActionFeedback()
  const submitting = useRef(false)
  const retry = useRef(new RetryEnvelope())
  /** 并发点击只执行一次；错误只渲染文本，业务原始对象不写到日志或浏览器持久存储。 */
  async function execute() {
    if (submitting.current) return
    if (disabled) { feedback.showResult({ title: '当前无法领取', message: unavailableReason || '暂不满足操作条件。', tone: 'info' }); return }
    submitting.current = true; setBusy(true)
    try {
      const result = await api.command(action, data, retry.current.prepare({ action, ...data }, reason))
      retry.current.success(); setOpen(false)
      if (action === 'campaign.checkin') {
        const repeated = Boolean((result as { already_checked_in?: boolean }).already_checked_in)
        feedback.showResult({ title: repeated ? '今天已经签到' : '签到成功', message: repeated ? '今日签到已经记录，不会重复累计天数。' : '今日签到已记录，达成条件后即可领取对应奖励。', tone: repeated ? 'info' : 'success' })
      } else if (action === 'campaign.claim') feedback.showResult({ title: '奖励领取成功', message: '奖励已发放至你的仓库。', data: resultData || result })
      else feedback.notify({ title: '操作已完成' })
      // 写入完成后刷新失败不能变成“提交失败”，更不能重新执行已成功的业务操作。
      try { await onDone?.(result) } catch { feedback.notify({ title: '操作已完成，页面刷新失败', message: '请手动刷新查看最新记录。', tone: 'info' }) }
    } catch (error) {
      const message = error instanceof Error ? error.message : '操作失败，请重试。'
      if (action.startsWith('campaign.')) feedback.showResult({ title: '操作未完成', message, tone: 'error' })
      else feedback.notify({ title: '操作未完成', message, tone: 'error' })
    }
    finally { submitting.current = false; setBusy(false) }
  }
  return <div className="nz-command"><Button variant={variant} disabled={busy || (disabled && !unavailableReason)} aria-disabled={disabled || busy} onClick={() => confirm && !disabled ? setOpen(true) : void execute()}>{busy ? <LoaderCircle className="animate-spin" size={15} /> : null}{children}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}><DialogContent className="nz-dialog"><DialogHeader><DialogTitle>确认本次操作</DialogTitle><DialogDescription>{confirm}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>取消</Button><Button disabled={busy} onClick={() => void execute()}>{busy ? '处理中…' : '确认执行'}</Button></DialogFooter></DialogContent></Dialog>
  </div>
}

/** 奖励条件面向玩家显示；时长以小时或分钟表达，保持服务端秒数作为实际判断标准。 */
function rewardCondition(reward: Reward) {
  if (reward.type === 'LEADERBOARD') return `榜单第 ${reward.rank_from}–${reward.rank_to} 名`
  if (reward.type === 'PLAYTIME_SECONDS') return `累计游玩 ${Number(reward.threshold) >= 3600 ? `${Number(reward.threshold) / 3600} 小时` : `${Number(reward.threshold) / 60} 分钟`}`
  return `${rewardLabels[reward.type]} ${reward.threshold} ${reward.type === 'CLEAR_COUNT' ? '次' : '天'}`
}

/** 活动中心与独立详情共享奖励组件；网页通过真实slug导航，桌面未传导航器时保持内存选中。 */
export function CampaignsPage({ api, authenticated, onLogin, slug, onOpen, onBack, maps = [] }: {
  api: RuntimeApi; authenticated: boolean; onLogin?: () => void; slug?: string | null
  onOpen?: (slug: string) => void; onBack?: () => void; maps?: PlayMap[]
}) {
  const [localSlug, setLocalSlug] = useState<string | null>(null)
  const activeSlug = slug === undefined ? localSlug : slug
  const campaigns = useResource<Campaign[]>(api, activeSlug ? null : publicPath('campaigns', authenticated))
  const detail = useResource<Campaign>(api, activeSlug ? `${authenticated ? '/api/views' : '/api/public'}/campaign-detail?slug=${encodeURIComponent(activeSlug)}` : null)
  const rows = Array.isArray(campaigns.data) ? campaigns.data : []
  const active = detail.data
  const refresh = activeSlug ? detail.refresh : campaigns.refresh
  const error = activeSlug ? detail.error : campaigns.error
  const loading = activeSlug ? detail.loading : campaigns.loading
  /** 默认模板提供奖励区；管理端只能选择纯文本/奖励/榜单模块，不能注入HTML或脚本。 */
  const sections = active?.presentation?.sections?.length ? active.presentation.sections : [{ key: 'rewards', kind: 'rewards' as const, title: '本期奖励' }]
  function open(next: string) { if (onOpen) onOpen(next); else setLocalSlug(next) }
  function back() { if (onBack) onBack(); else setLocalSlug(null) }
  const rewardRows = active?.rewards || active?.definition.rewards || []
  const rewards = <div className="nz-reward-grid">{rewardRows.map((reward, index) => <article key={reward.key} className={`nz-reward ${reward.claimed ? 'is-claimed' : ''}`}><div className="nz-reward-top"><span>{rewardLabels[reward.type]}</span><span>{String(index + 1).padStart(2, '0')}</span></div><div className="nz-reward-icon"><ItemArtwork itemKey={reward.changes[0]?.item_key} /></div><h3>{reward.title}</h3><p>{rewardCondition(reward)}</p><ul>{reward.changes.map((change, n) => <li key={n}><Package size={13} /><span>{itemName(change.item_key)} × {change.quantity}</span><small>{change.duration_seconds ? `${change.duration_seconds / 86400} 天` : '永久'}</small></li>)}</ul>
    {authenticated ? reward.type === 'LEADERBOARD' || reward.delivery === 'AUTOMATIC' ? <div className="nz-automatic-reward"><ShieldCheck size={15} />{reward.delivery_status === 'SUCCEEDED' ? '奖励已自动发放' : reward.delivery_status === 'FAILED' ? '发放待处理，请联系管理员' : reward.delivery_status === 'PENDING' ? '奖励正在自动发放' : '封榜后自动发放'}</div> : <CommandButton api={api} action="campaign.claim" data={{ campaign_id: active?.id || '', reward_key: reward.key }} reason="玩家领取活动奖励" disabled={!reward.eligible || reward.claimed || active?.state === 'PAUSED'} unavailableReason={reward.claimed ? '本档奖励已经领取，无需重复操作。' : active?.state === 'PAUSED' ? '活动暂时暂停，请等待管理员恢复。' : `尚未满足领取条件：${rewardCondition(reward)}。`} resultData={{ changes: reward.changes }} onDone={refresh}>{reward.claimed ? <><Check size={15} />已领取</> : reward.eligible ? '领取奖励' : '尚未达成'}</CommandButton> : <Button variant="outline" onClick={onLogin}>登录后查看进度 <ArrowRight size={15} /></Button>}
  </article>)}</div>
  return <section className={`nz-page nz-campaign-page nz-campaign-theme-${active?.presentation?.theme || 'default'}`}>
    {activeSlug && <Button variant="ghost" onClick={back}><ArrowLeft size={16} />返回活动中心</Button>}
    <div className="nz-event-banner"><CampaignCover api={api} className="nz-campaign-cover" src={campaignCoverSource(active?.presentation)} alt="" /><div><span className="nz-season-label"><Gift size={16} />{active ? campaignLabels[active.state] : '活动中心'}</span><h1>{active?.presentation?.title || active?.title || <>每次相聚，<br />都有好礼。</>}</h1><p>{active?.presentation?.summary || '查看正在开放的活动，挑选下一份属于你的战备补给。'}</p></div><div className="nz-event-emblem" aria-hidden="true"><Gift size={82} strokeWidth={1} /><span>活动补给</span></div></div>
    <div className="nz-section-title"><h2>{activeSlug ? '活动详情' : '全部活动'}</h2><Button variant="ghost" size="sm" onClick={refresh}><RefreshCw size={14} />刷新</Button></div>
    {loading && <p className="nz-loading" role="status"><LoaderCircle className="animate-spin" size={18} />正在读取活动…</p>}{error && <Notice error>{error}</Notice>}
    {!activeSlug && !loading && !error && !rows.length && <Empty title="新一轮补给正在准备" icon={<Gift size={36} />}>活动发布后，奖励条件与领取入口会出现在这里。</Empty>}
    {!activeSlug && <div className="nz-campaign-center-grid">{rows.map(row => <article className={`nz-campaign-card nz-campaign-theme-${row.presentation?.theme || 'default'}`} key={row.id}><CampaignCover api={api} src={campaignCoverSource(row.presentation)} alt="" loading="lazy" decoding="async" /><div><span className="nz-tag">{campaignLabels[row.state]}</span><h2>{row.presentation?.title || row.title}</h2><p>{row.presentation?.summary || '查看活动规则与奖励详情。'}</p><small>{formatDate(row.definition.starts_at, '暂无', 'Asia/Shanghai')} 至 {formatDate(row.definition.ends_at, '暂无', 'Asia/Shanghai')}</small><a href={`/activities/${encodeURIComponent(row.presentation?.slug || row.id)}`} className="nz-text-link" onClick={event => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey) { event.preventDefault(); open(row.presentation?.slug || row.id) } }}>进入活动 <ArrowRight size={16} /></a></div></article>)}</div>}
    {active && <><div className="nz-campaign-summary"><div><h2>{active.title}</h2><p>{formatDate(active.definition.starts_at, '暂无', 'Asia/Shanghai')} 至 {formatDate(active.definition.ends_at, '暂无', 'Asia/Shanghai')} · 北京时间</p></div>{active.test_only && <span className="nz-tag">合成测试活动</span>}{authenticated && <CommandButton api={api} action="campaign.checkin" data={{ campaign_id: active.id }} reason="玩家活动签到" disabled={active.state !== 'PUBLISHED'} onDone={refresh}><CalendarCheck size={16} />今日签到</CommandButton>}</div>
    {authenticated && active.progress && <div className="nz-progress-strip"><div><strong>{active.progress.checkin_days}</strong><span>已签到 / 天</span></div><div><strong>{active.progress.launcher_login_days}</strong><span>启动器登录 / 天</span></div><div><strong>{Math.floor(active.progress.playtime_seconds / 60)}</strong><span>累计游玩 / 分钟</span></div><div><strong>{active.progress.clear_count}</strong><span>成功通关 / 次</span></div></div>}
    {sections.map(section => <section key={section.key} className="nz-campaign-section"><h2>{section.title}</h2>{section.kind === 'text' ? <p className="nz-preline">{section.body}</p> : section.kind === 'rewards' ? rewards : <LeaderboardsPage api={api} authenticated={authenticated} maps={maps} campaignId={active.id} compact />}</section>)}
    <div className="nz-rules-note"><ShieldCheck size={19} /><div><strong>奖励规则</strong><p>普通奖励达成后手动领取。排行奖励在封榜后自动发放，每名玩家每期仅获得最高一档。实际游玩与通关进度以可信结算为准。</p></div></div></>}
  </section>
}

/** 根据地图目录确定人数上限；未识别类型时按猎场展示，服务端仍严格校验榜单参数。 */
export function mapCapacity(map?: PlayMap) {
  return /tower|defen|塔防/i.test(map?.kind || '') ? 4 : 8
}

/** 地图、人数和难度分别筛选，同队仅呈现服务端返回的最佳成绩，不在网页拼接或推算排名。 */
export function LeaderboardsPage({ api, authenticated, maps, campaignId: lockedCampaignId, compact = false }: { api: RuntimeApi; authenticated: boolean; maps: PlayMap[]; campaignId?: string; compact?: boolean }) {
  const campaigns = useResource<Campaign[]>(api, publicPath('campaigns', authenticated))
  const fieldId = useId()
  const [mapId, setMapId] = useState('')
  const [difficulty, setDifficulty] = useState('')
  const [players, setPlayers] = useState(1)
  const [campaignId, setCampaignId] = useState('')
  const activeMap = maps.find(map => String(map.map_id) === mapId) || maps[0]
  const levels = activeMap?.submodes || []
  const activeDifficulty = levels.includes(Number(difficulty)) ? difficulty : String(levels[0] ?? '')
  const campaignRows = Array.isArray(campaigns.data) ? campaigns.data : []
  const campaign = lockedCampaignId ? campaignRows.find(row => row.id === lockedCampaignId) : campaignRows.find(row => row.id === campaignId) || campaignRows[0]
  const query = activeMap && activeDifficulty && campaign ? new URLSearchParams({ campaign_id: campaign.id, map_id: String(activeMap.map_id), difficulty: activeDifficulty, player_count: String(Math.min(players, mapCapacity(activeMap))) }).toString() : null
  const ranking = useResource<Ranking>(api, query ? `${publicPath('leaderboards', authenticated)}?${query}` : null)
  return <section className="nz-page">
    {!compact && <PageHeading title="先锋榜" description="同一张地图，同样的队伍人数，向更快的通关纪录出发。" action={<span className="nz-tag"><Trophy size={14} />赛季挑战</span>} />}
    <div className="nz-board-filter"><div><Label htmlFor={`${fieldId}-campaign`}>活动赛季</Label><select id={`${fieldId}-campaign`} value={campaign?.id || ''} disabled={!!lockedCampaignId} onChange={event => setCampaignId(event.target.value)}><option value="" disabled>等待赛季开放</option>{campaignRows.map(row => <option key={row.id} value={row.id}>{row.title}</option>)}</select></div><div><Label htmlFor={`${fieldId}-map`}>挑战地图</Label><select id={`${fieldId}-map`} value={activeMap?.map_id || ''} onChange={event => { setMapId(event.target.value); setPlayers(1); setDifficulty('') }}><option value="" disabled>暂无开放地图</option>{maps.map(map => <option key={map.map_id} value={map.map_id}>{map.name}</option>)}</select></div><div><Label htmlFor={`${fieldId}-difficulty`}>难度</Label><select id={`${fieldId}-difficulty`} value={activeDifficulty} onChange={event => setDifficulty(event.target.value)}><option value="" disabled>等待地图配置</option>{difficultyChoices(levels).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div></div>
    <details className="nz-details"><summary>技术详情</summary><p>当前地图编号：{activeMap?.map_id ?? '暂无'}；模式编号：{activeMap?.mode_id ?? '暂无'}。</p>{difficultyChoices(levels).map(option => <p key={option.value}>{option.label}：{option.value}</p>)}</details><div className="nz-squad-tabs" role="tablist" aria-label="小队人数">{Array.from({ length: mapCapacity(activeMap) }, (_, index) => index + 1).map(count => <button key={count} role="tab" aria-selected={Math.min(players, mapCapacity(activeMap)) === count} onClick={() => setPlayers(count)}><Users size={16} />{count} 人</button>)}</div>
    <div className="nz-ranking-caption"><span>{activeMap?.name || '挑战地图'} <ChevronRight size={12} /> {Math.min(players, mapCapacity(activeMap))} 人榜</span><span>{ranking.data?.finalized ? '已封榜' : '本期记录'}<Button variant="ghost" size="icon" aria-label="刷新榜单" onClick={ranking.refresh}><RefreshCw size={14} /></Button></span></div>
    {ranking.error && <Notice error>{ranking.error}</Notice>}
    {ranking.loading ? <p className="nz-loading"><LoaderCircle className="animate-spin" size={18} />正在读取成绩…</p> : ranking.data?.entries?.length ? <div className="nz-table-wrap"><table className="nz-table nz-ranking-table"><thead><tr><th>名次</th><th>先锋小队</th><th>通关用时</th><th>结算时间</th></tr></thead><tbody>{ranking.data.entries.map(entry => <tr key={entry.result_id}><td><span className={`nz-rank-position ${entry.rank <= 3 ? 'is-podium' : ''}`}>{entry.rank <= 3 ? <Award size={20} /> : null}{entry.rank}</span></td><td><strong>{entry.nicknames.length ? entry.nicknames.join(' / ') : '小队成员'}</strong><span className="nz-table-sub">{entry.member_ids?.length || Math.min(players, mapCapacity(activeMap))} 人小队</span></td><td className="nz-time">{formatDuration(entry.duration_ms)}</td><td>{formatDate(entry.settled_at)}</td></tr>)}</tbody></table></div> : <Empty title="首个纪录，等你来写" icon={<Trophy size={39} />}>当前筛选下暂无已通过验证的成绩。完成有效挑战后，小队最佳纪录将在这里展示。</Empty>}
    <div className="nz-board-rules"><div><span className="nz-rule-icon"><ShieldCheck size={21} /></span><h3>胜利，才有名字</h3><p>僵尸猎场仅任务成功可入榜；塔防必须击杀 Boss 并成功结算。</p></div><div><span className="nz-rule-icon"><Clock3 size={21} /></span><h3>每一毫秒，都算数</h3><p>按地图、人数与难度分别排行。同成员小队仅保留最佳通关时间。</p></div><div><span className="nz-rule-icon"><Gift size={21} /></span><h3>榜单，连接荣誉与奖励</h3><p>封榜后按活动规则自动发奖。目前可信实玩结算接入仍待验收。</p></div></div>
  </section>
}

/** 库存只显示服务端返回的实例、数量和期限；军衔不由网页根据道具或场次猜测。 */
export function InventoryPage({ api }: { api: RuntimeApi }) {
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState<number | null>(null)
  const [snapshotRevision, setSnapshotRevision] = useState<number | null>(null)
  const [loaded, setLoaded] = useState<InventoryItem[]>([])
  const inventory = useResource<{ items: InventoryItem[]; wallet?: unknown; next_gid?: number | null; revision: number }>(api, `/api/views/player-inventory?limit=200${cursor === null ? '' : `&after_gid=${cursor}&revision=${snapshotRevision}`}`)
  // 后续页锁定同一库存 revision；发生变化时保留可见旧快照并提示刷新，绝不拼接不一致库存。
  useEffect(() => {
    if (!inventory.data) return
    const result = inventory.data
    setSnapshotRevision(result.revision)
    setLoaded(previous => cursor === null ? result.items : [...new Map([...previous, ...result.items].map(item => [item.instance_id, item])).values()])
  }, [inventory.data])
  /** 刷新从第一页重建快照，丢弃旧游标；不复用过期 revision。 */
  function refresh() { setCursor(null); setSnapshotRevision(null); setLoaded([]); inventory.refresh() }
  const items = loaded.filter(item => `${item.name || ''} ${item.item_id || ''} ${item.item_key || ''}`.toLowerCase().includes(query.toLowerCase()))
  return <section className="nz-page"><PageHeading title="我的仓库" description="每件装备，都准备好下一次出发。" action={<Button variant="outline" onClick={refresh}><RefreshCw size={15} />刷新仓库</Button>} /><div className="nz-inventory-toolbar"><Input aria-label="搜索仓库道具" placeholder="搜索已读取道具的名称或编号" value={query} onChange={event => setQuery(event.target.value)} /><span>已读取 {loaded.length} 件{inventory.data?.next_gid ? ' · 还有更多道具' : ''}</span></div>
    {inventory.error && <Notice error>{inventory.error}</Notice>}{inventory.loading && <p className="nz-loading">正在读取仓库…</p>}
    {!inventory.loading && !inventory.error && !items.length && <Empty title={query ? '没有找到这件道具' : '仓库正在等待第一份补给'}>{query ? '试试其他名称或道具编号。' : '获得装备后，刷新即可查看。'}</Empty>}
    <div className="nz-inventory-grid">{items.map(item => <article className="nz-inventory-item" key={item.instance_id || String(item.gid)}><span className="nz-item-symbol"><ItemArtwork itemId={item.item_id} itemKey={item.item_key} name={item.name} /></span><span className="nz-item-count">×{item.quantity}</span><h3>{itemName(item.item_key || item.item_id, item.name)}</h3><p>{item.expires_at ? formatDate(item.expires_at) : '永久道具'}</p>{typeof item.state === 'string' && inventoryStateLabel(item.state) && <small>{inventoryStateLabel(item.state)}</small>}{item.equipment_slot && <small>已装备</small>}</article>)}</div>
    {inventory.data?.next_gid && <div className="nz-pagination"><Button variant="outline" disabled={inventory.loading} onClick={() => setCursor(inventory.data?.next_gid || null)}>继续读取仓库</Button></div>}
  </section>
}

/** 通用轻量面板沿用 shadcn Card，避免管理和玩家模块出现两套组件行为。 */
export function Panel({ title, children, className = '' }: { title?: string; children: ReactNode; className?: string }) {
  return <Card className={`nz-panel ${className}`}>{title && <CardHeader><CardTitle>{title}</CardTitle></CardHeader>}<CardContent>{children}</CardContent></Card>
}
