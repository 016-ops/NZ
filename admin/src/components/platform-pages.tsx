/** 玩家与 Launcher 共享的活动、榜单、仓库和商城；所有可见记录来自当前接口，不生成示例成绩或资产。 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowRight, Award, CalendarCheck, Check, ChevronRight, Clock3, Gift, LoaderCircle, Package, RefreshCw, ShieldCheck, ShoppingBag, Trophy, Users } from 'lucide-react'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { RetryEnvelope, type Data } from '@/lib/api'
import { campaignLabels, formatDate, formatDuration, publicPath, rewardLabels, useResource, type Campaign, type InventoryItem, type PlayMap, type Ranking, type Reward, type RuntimeApi, type ShopList, type ShopOffer } from '@/lib/platform'

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
export function CommandButton({ api, action, data, children, reason, confirm, disabled, onDone, variant = 'default' }: {
  api: RuntimeApi; action: string; data: Data; children: ReactNode; reason: string; confirm?: string; disabled?: boolean
  onDone?: (value: unknown) => void; variant?: 'default' | 'outline' | 'ghost' | 'destructive'
}) {
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const retry = useRef(new RetryEnvelope())
  /** 并发点击只执行一次；错误只渲染文本，业务原始对象不写到日志或浏览器持久存储。 */
  async function execute() {
    if (busy) return
    setBusy(true); setMessage(null)
    try {
      const result = await api.command(action, data, retry.current.prepare({ action, ...data }, reason))
      retry.current.success(); setOpen(false); setFailed(false); setMessage('操作已完成。'); onDone?.(result)
    } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : '操作失败，请重试。') }
    finally { setBusy(false) }
  }
  return <div className="nz-command"><Button variant={variant} disabled={disabled || busy} onClick={() => confirm ? setOpen(true) : void execute()}>{busy ? <LoaderCircle className="animate-spin" size={15} /> : null}{children}</Button>
    {message && <span className={`nz-command-message ${failed ? 'is-error' : ''}`} role={failed ? 'alert' : 'status'}>{message}</span>}
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}><DialogContent className="nz-dialog"><DialogHeader><DialogTitle>确认本次操作</DialogTitle><DialogDescription>{confirm}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>取消</Button><Button disabled={busy} onClick={() => void execute()}>{busy ? '处理中…' : '确认执行'}</Button></DialogFooter></DialogContent></Dialog>
  </div>
}

/** 奖励条件面向玩家显示；时长以小时或分钟表达，保持服务端秒数作为实际判断标准。 */
function rewardCondition(reward: Reward) {
  if (reward.type === 'LEADERBOARD') return `榜单第 ${reward.rank_from}–${reward.rank_to} 名`
  if (reward.type === 'PLAYTIME_SECONDS') return `累计游玩 ${Number(reward.threshold) >= 3600 ? `${Number(reward.threshold) / 3600} 小时` : `${Number(reward.threshold) / 60} 分钟`}`
  return `${rewardLabels[reward.type]} ${reward.threshold} ${reward.type === 'CLEAR_COUNT' ? '次' : '天'}`
}

/** 活动任务从服务端读取资格；仅登录 Launcher 记录登录天数，网页浏览不会自行累加。 */
export function CampaignsPage({ api, authenticated, onLogin }: { api: RuntimeApi; authenticated: boolean; onLogin?: () => void }) {
  const campaigns = useResource<Campaign[]>(api, publicPath('campaigns', authenticated))
  const rows = Array.isArray(campaigns.data) ? campaigns.data : []
  const [selected, setSelected] = useState('')
  const active = rows.find(row => row.id === selected) || rows[0]
  const rewardRows = active?.rewards || active?.definition?.rewards || []
  return <section className="nz-page nz-campaign-page">
    <div className="nz-event-banner"><div><span className="nz-season-label"><Gift size={16} />开服补给</span><h1>战友集结，<br />好礼随行。</h1><p>签到、登录、通关，每一程都有回响。<br />完成目标后，在这里领取你的专属补给。</p></div><div className="nz-event-emblem" aria-hidden="true"><Gift size={82} strokeWidth={1} /><span>REWARDS</span></div></div>
    <div className="nz-section-title"><h2>本期活动</h2><Button variant="ghost" size="sm" onClick={campaigns.refresh}><RefreshCw size={14} />刷新</Button></div>
    {campaigns.loading && <p className="nz-loading" role="status"><LoaderCircle className="animate-spin" size={18} />正在读取活动…</p>}
    {campaigns.error && <Notice error>{campaigns.error}</Notice>}
    {!campaigns.loading && !campaigns.error && !rows.length && <Empty title="新一轮补给正在准备" icon={<Gift size={36} />}>活动发布后，奖励条件与领取入口会出现在这里。</Empty>}
    {rows.length > 0 && <><div className="nz-segmented" role="tablist" aria-label="选择活动">{rows.map(row => <button key={row.id} role="tab" aria-selected={active?.id === row.id} onClick={() => setSelected(row.id)}>{row.title}<span>{campaignLabels[row.state]}</span></button>)}</div>
      {active && <><div className="nz-campaign-summary"><div><h2>{active.title}</h2><p>{formatDate(active.definition?.starts_at, '暂无', 'Asia/Shanghai')} 至 {formatDate(active.definition?.ends_at, '暂无', 'Asia/Shanghai')} · 活动时间以北京时间为准</p></div>{active.test_only && <span className="nz-tag">合成测试活动</span>}
        {authenticated && <CommandButton api={api} action="campaign.checkin" data={{ campaign_id: active.id }} reason="玩家活动签到" disabled={active.state !== 'PUBLISHED'} onDone={campaigns.refresh}><CalendarCheck size={16} />今日签到</CommandButton>}
      </div>
      {authenticated && active.progress && <div className="nz-progress-strip"><div><strong>{active.progress.checkin_days}</strong><span>已签到 / 天</span></div><div><strong>{active.progress.launcher_login_days}</strong><span>启动器登录 / 天</span></div><div><strong>{Math.floor(active.progress.playtime_seconds / 60)}</strong><span>累计游玩 / 分钟</span></div><div><strong>{active.progress.clear_count}</strong><span>成功通关 / 次</span></div></div>}
      <div className="nz-reward-grid">{rewardRows.map((reward, index) => <article key={reward.key} className={`nz-reward ${reward.claimed ? 'is-claimed' : ''}`}><div className="nz-reward-top"><span>{rewardLabels[reward.type]}</span><span>{String(index + 1).padStart(2, '0')}</span></div><div className="nz-reward-icon">{reward.type === 'LEADERBOARD' ? <Trophy size={34} /> : reward.type === 'PLAYTIME_SECONDS' ? <Clock3 size={34} /> : <Gift size={34} />}</div><h3>{reward.title}</h3><p>{rewardCondition(reward)}</p><ul>{reward.changes.map((change, n) => <li key={n}><Package size={13} /><span>{change.item_key} × {change.quantity}</span><small>{change.duration_seconds ? `${change.duration_seconds / 86400} 天` : '永久'}</small></li>)}</ul>
        {authenticated ? reward.type === 'LEADERBOARD' || reward.delivery === 'AUTOMATIC' ? <div className="nz-automatic-reward"><ShieldCheck size={15} />{reward.delivery_status === 'SUCCEEDED' ? '奖励已自动发放' : reward.delivery_status === 'FAILED' ? '发放待处理，请联系管理员' : reward.delivery_status === 'PENDING' ? '奖励正在自动发放' : '封榜后自动发放'}</div> : <CommandButton api={api} action="campaign.claim" data={{ campaign_id: active.id, reward_key: reward.key }} reason="玩家领取活动奖励" disabled={!reward.eligible || reward.claimed || active.state === 'PAUSED'} onDone={campaigns.refresh}>{reward.claimed ? <><Check size={15} />已领取</> : reward.eligible ? '领取奖励' : '尚未达成'}</CommandButton> : <Button variant="outline" onClick={onLogin}>登录后查看进度 <ArrowRight size={15} /></Button>}
      </article>)}</div></>}
    </>}
    <div className="nz-rules-note"><ShieldCheck size={19} /><div><strong>奖励规则</strong><p>普通奖励达成后手动领取。排行奖励在封榜后自动发放，每名玩家在每期活动仅获得最高一档。实际游玩与通关进度以可信结算为准。</p></div></div>
  </section>
}

/** 根据地图目录确定人数上限；未识别类型时按猎场展示，服务端仍严格校验榜单参数。 */
export function mapCapacity(map?: PlayMap) {
  return /tower|defen|塔防/i.test(map?.kind || '') ? 4 : 8
}

/** 地图、人数和难度分别筛选，同队仅呈现服务端返回的最佳成绩，不在网页拼接或推算排名。 */
export function LeaderboardsPage({ api, authenticated, maps }: { api: RuntimeApi; authenticated: boolean; maps: PlayMap[] }) {
  const campaigns = useResource<Campaign[]>(api, publicPath('campaigns', authenticated))
  const [mapId, setMapId] = useState('')
  const [difficulty, setDifficulty] = useState('')
  const [players, setPlayers] = useState(1)
  const [campaignId, setCampaignId] = useState('')
  const activeMap = maps.find(map => String(map.map_id) === mapId) || maps[0]
  const levels = activeMap?.submodes || []
  const activeDifficulty = levels.includes(Number(difficulty)) ? difficulty : String(levels[0] ?? '')
  const campaignRows = Array.isArray(campaigns.data) ? campaigns.data : []
  const campaign = campaignRows.find(row => row.id === campaignId) || campaignRows[0]
  const query = activeMap && activeDifficulty && campaign ? new URLSearchParams({ campaign_id: campaign.id, map_id: String(activeMap.map_id), difficulty: activeDifficulty, player_count: String(Math.min(players, mapCapacity(activeMap))) }).toString() : null
  const ranking = useResource<Ranking>(api, query ? `${publicPath('leaderboards', authenticated)}?${query}` : null)
  return <section className="nz-page">
    <PageHeading title="先锋榜" description="同一张地图，同样的队伍人数，向更快的通关纪录出发。" action={<span className="nz-tag"><Trophy size={14} />赛季挑战</span>} />
    <div className="nz-board-filter"><div><Label htmlFor="rank-campaign">活动赛季</Label><select id="rank-campaign" value={campaign?.id || ''} onChange={event => setCampaignId(event.target.value)}><option value="" disabled>等待赛季开放</option>{campaignRows.map(row => <option key={row.id} value={row.id}>{row.title}</option>)}</select></div><div><Label htmlFor="rank-map">挑战地图</Label><select id="rank-map" value={activeMap?.map_id || ''} onChange={event => { setMapId(event.target.value); setPlayers(1); setDifficulty('') }}><option value="" disabled>暂无开放地图</option>{maps.map(map => <option key={map.map_id} value={map.map_id}>{map.name}</option>)}</select></div><div><Label htmlFor="rank-difficulty">难度</Label><select id="rank-difficulty" value={activeDifficulty} onChange={event => setDifficulty(event.target.value)}><option value="" disabled>等待地图配置</option>{levels.map(level => <option key={level} value={level}>难度 {level}</option>)}</select></div></div>
    <div className="nz-squad-tabs" role="tablist" aria-label="小队人数">{Array.from({ length: mapCapacity(activeMap) }, (_, index) => index + 1).map(count => <button key={count} role="tab" aria-selected={Math.min(players, mapCapacity(activeMap)) === count} onClick={() => setPlayers(count)}><Users size={16} />{count} 人</button>)}</div>
    <div className="nz-ranking-caption"><span>{activeMap?.name || '挑战地图'} <ChevronRight size={12} /> {Math.min(players, mapCapacity(activeMap))} 人榜</span><span>{ranking.data?.finalized ? '已封榜' : '本期记录'}<Button variant="ghost" size="icon" aria-label="刷新榜单" onClick={ranking.refresh}><RefreshCw size={14} /></Button></span></div>
    {ranking.error && <Notice error>{ranking.error}</Notice>}
    {ranking.loading ? <p className="nz-loading"><LoaderCircle className="animate-spin" size={18} />正在读取成绩…</p> : ranking.data?.entries?.length ? <div className="nz-table-wrap"><table className="nz-table nz-ranking-table"><thead><tr><th>名次</th><th>先锋小队</th><th>通关用时</th><th>结算时间</th></tr></thead><tbody>{ranking.data.entries.map(entry => <tr key={entry.result_id}><td><span className={`nz-rank-position ${entry.rank <= 3 ? 'is-podium' : ''}`}>{entry.rank <= 3 ? <Award size={20} /> : null}{entry.rank}</span></td><td><strong>{entry.nicknames.length ? entry.nicknames.join(' / ') : '小队成员'}</strong><span className="nz-table-sub">{entry.member_ids.length} 人小队</span></td><td className="nz-time">{formatDuration(entry.duration_ms)}</td><td>{formatDate(entry.settled_at)}</td></tr>)}</tbody></table></div> : <Empty title="首个纪录，等你来写" icon={<Trophy size={39} />}>当前筛选下暂无已通过验证的成绩。完成有效挑战后，小队最佳纪录将在这里展示。</Empty>}
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
    <div className="nz-inventory-grid">{items.map(item => <article className="nz-inventory-item" key={item.instance_id || String(item.gid)}><span className="nz-item-symbol"><Package size={31} strokeWidth={1.25} /></span><span className="nz-item-count">×{item.quantity}</span><h3>{item.name || item.item_key || `道具 ${item.item_id}`}</h3><p>{item.expires_at ? formatDate(item.expires_at) : '永久道具'}</p>{typeof item.state === 'string' && <small>{item.state}</small>}{item.equipment_slot && <small>已装备</small>}</article>)}</div>
    {inventory.data?.next_gid && <div className="nz-pagination"><Button variant="outline" disabled={inventory.loading} onClick={() => setCursor(inventory.data?.next_gid || null)}>继续读取仓库</Button></div>}
  </section>
}

/** 单件商城选购保留服务端报价原文，购买时不自行重算价格或构造报价。 */
function ShopCard({ api, offer, onPurchased }: { api: RuntimeApi; offer: ShopOffer; onPurchased: () => void }) {
  const [priceIndex, setPriceIndex] = useState(0)
  const [quantity, setQuantity] = useState(1)
  const [quote, setQuote] = useState<Data | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const retry = useRef(new RetryEnvelope())
  const lines = [{ commodity_id: offer.commodity_id, price_index: priceIndex, quantity }]
  // 参数改变后废弃旧报价，避免把另一个期限或数量的价格误用于购买。
  useEffect(() => { setQuote(null); setMessage(null) }, [priceIndex, quantity, offer.revision])
  /** 报价虽不扣资产也使用幂等元数据，网络失败可继续相同请求，不自动发起购买。 */
  async function preview() {
    if (busy) return
    setBusy(true); setMessage(null)
    try { const value = await api.command('player.shop.quote', { lines }, retry.current.prepare({ lines }, '玩家商品报价')); retry.current.success(); setQuote(value as Data) }
    catch (error) { setMessage(error instanceof Error ? error.message : '报价失败。') }
    finally { setBusy(false) }
  }
  return <article className="nz-shop-item"><span className="nz-shop-symbol"><ShoppingBag size={39} strokeWidth={1.2} /></span><p className="nz-shop-kind">战备商城</p><h3>{offer.label}</h3><div className="nz-shop-price"><strong>{offer.prices[priceIndex]?.toLocaleString('zh-CN')}</strong><span>{offer.currency}</span></div><div className="nz-shop-options"><Label>{offer.period_editable ? '使用期限' : '购买档位'}<select aria-label={`${offer.label} 使用期限`} value={priceIndex} onChange={event => setPriceIndex(Number(event.target.value))}>{offer.prices.map((_, index) => <option key={index} value={index}>{offer.period_editable ? offer.period_hours[index] === -1 ? '永久' : `${offer.period_hours[index]} 小时` : `档位 ${index + 1}`}</option>)}</select></Label><Label>数量<Input aria-label={`${offer.label} 购买数量`} type="number" min={1} max={20} value={quantity} onChange={event => setQuantity(Math.max(1, Math.min(20, Number(event.target.value) || 1)))} /></Label></div>
    {message && <Notice error>{message}</Notice>}{quote ? <><Notice>报价已确认，请核对期限、数量和余额后购买。</Notice><CommandButton api={api} action="player.shop.purchase" data={{ lines, quote }} reason="玩家商城购买" confirm={`确认购买 ${offer.label} × ${quantity}？服务端将重新校验报价与余额。`} onDone={() => { setQuote(null); onPurchased() }}>确认购买</CommandButton></> : <Button variant="outline" disabled={busy} onClick={() => void preview()}>{busy ? '报价中…' : '查看购买报价'}<ArrowRight size={14} /></Button>}</article>
}

/** 商城只展示实际上架的目录；空目录不生成推荐商品。 */
export function PlayerShopPage({ api }: { api: RuntimeApi }) {
  const [offset, setOffset] = useState(0)
  const shop = useResource<ShopList>(api, `/api/views/player-shop?limit=24&offset=${offset}`)
  return <section className="nz-page"><PageHeading title="战备商城" description="为下一场并肩作战，添置趁手装备。" />{shop.error && <Notice error>{shop.error}</Notice>}{shop.loading && <p className="nz-loading">正在读取商品…</p>}<div className="nz-shop-grid">{shop.data?.items?.map(offer => <ShopCard key={offer.commodity_id} api={api} offer={offer} onPurchased={shop.refresh} />)}</div>{!shop.loading && !shop.error && !shop.data?.items?.length && <Empty title="战备补给准备中" icon={<ShoppingBag size={37} />}>管理员上架商品后，即可在这里选购。</Empty>}{(shop.data?.total || offset) ? <div className="nz-pagination"><Button variant="outline" disabled={!offset || shop.loading} onClick={() => setOffset(value => Math.max(0, value - 24))}>上一页</Button><span>第 {Math.floor(offset / 24) + 1} 页 · 共 {shop.data?.total ?? 0} 件</span><Button variant="outline" disabled={shop.loading || offset + 24 >= (shop.data?.total || 0)} onClick={() => setOffset(value => value + 24)}>下一页</Button></div> : null}</section>
}

/** 通用轻量面板沿用 shadcn Card，避免管理和玩家模块出现两套组件行为。 */
export function Panel({ title, children, className = '' }: { title?: string; children: ReactNode; className?: string }) {
  return <Card className={`nz-panel ${className}`}>{title && <CardHeader><CardTitle>{title}</CardTitle></CardHeader>}<CardContent>{children}</CardContent></Card>
}
