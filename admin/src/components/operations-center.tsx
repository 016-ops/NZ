/** 管理员运营中心：账号与仓库、商城、开放地图、活动和监控分别组织，沿用现有权限及命令审计。 */
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Activity, ArrowLeft, ArrowRight, Bell, Boxes, Check, ChevronRight, Cpu, Gift, HardDrive, Map, MemoryStick, MessageSquare, Plus, RefreshCw, Search, Server, ShieldCheck, ShoppingBag, Trash2, Trophy, Users, Wifi } from 'lucide-react'
import { useActionFeedback } from './action-feedback'
import { CatalogItemPicker } from './catalog-item-picker'
import { FeedbackPage } from './feedback-pages'
import { CampaignPresentationEditor } from './operations-campaign-presentation'
import { ItemArtwork, itemName } from './item-artwork'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { BusinessForm, Details } from './business'
import { CommandButton, Empty, Notice, PageHeading, Panel } from './platform-pages'
import { RetryEnvelope, type Catalog, type Data, type Metadata, type Role } from '@/lib/api'
import { accountStatusLabel, assetCodeLabel, difficultyChoices, campaignLabels, formatDate, mapKindLabel, rewardLabels, useResource, type Campaign, type InventoryItem, type PlayMap, type Reward, type RewardType, type RuntimeApi, type ShopList, type ShopOffer } from '@/lib/platform'

export type OperationsView = 'overview' | 'users' | 'shop' | 'maps' | 'campaigns' | 'monitoring' | 'feedback'
export const operationsLabels: Record<OperationsView, string> = { overview: '运营概览', users: '玩家管理', shop: '商城管理', maps: '地图与模式', campaigns: '活动', monitoring: '服务器监控', feedback: '玩家反馈' }

/** 表单错误统一转为文本；绝不把远端返回的 HTML 当作界面执行。 */
function errorText(value: unknown) { return value instanceof Error ? value.message : String(value) }

/** 对任意命令应用标准幂等、原因和错误处理；刷新只在服务端确认提交后触发。 */
async function submitCommand(api: RuntimeApi, action: string, data: Data, meta: Metadata, refresh: () => void) {
  const result = await api.command(action, data, meta)
  refresh()
  return result
}

/** 按管理员角色组织入口；最终权限由后端校验，隐藏菜单不构成安全边界。 */
export function OperationsCenter({ api, view, setView, role }: { api: RuntimeApi; view: OperationsView; setView: (value: OperationsView) => void; role: Role }) {
  const views: OperationsView[] = role === 'owner' ? ['overview', 'users', 'shop', 'maps', 'campaigns', 'monitoring', 'feedback'] : role === 'resources' ? ['overview', 'users', 'shop', 'campaigns', 'monitoring'] : role === 'users' ? ['overview', 'users', 'monitoring', 'feedback'] : ['overview', 'users', 'shop', 'campaigns', 'monitoring', 'feedback']
  const [feedback, setFeedback] = useState<unknown>(null)
  const { notify } = useActionFeedback()
  const report = useCallback((value: unknown) => { setFeedback(value); const message = typeof value === 'string' ? value : '操作已完成，详情可在当前页面查看。'; notify({ title: /失败|不正确|无法|未完成|过期|没有|取消/.test(message) ? '操作未完成' : '操作反馈', message, tone: /失败|不正确|无法|未完成|过期|没有|取消/.test(message) ? 'error' : 'success' }) }, [notify])
  const selected = views.includes(view) ? view : 'overview'
  return <div className="nz-operations"><div className="nz-operations-nav" role="tablist" aria-label="运营管理">{views.map(item => <button key={item} role="tab" aria-selected={selected === item} onClick={() => { setFeedback(null); setView(item) }}>{operationsLabels[item]}</button>)}<a href="/console">完整工作台 <ArrowRight size={13} /></a></div>
    {selected === 'users' ? <UsersManager api={api} report={report} canAssets={role === 'owner' || role === 'resources'} canUsers={role === 'owner' || role === 'users'} /> : selected === 'shop' ? <ShopManager api={api} readOnly={role === 'auditor'} /> : selected === 'maps' ? <MapManager api={api} /> : selected === 'campaigns' ? <CampaignManager api={api} report={report} readOnly={role === 'auditor'} /> : selected === 'monitoring' ? <MonitoringPage api={api} owner={role === 'owner'} report={report} /> : selected === 'feedback' ? <FeedbackPage api={api} management readOnly={role === 'auditor'} /> : <OperationsOverview api={api} setView={setView} allowed={views} />}
    {feedback !== null && <div className="nz-operation-feedback"><div className="nz-section-title"><h3>操作反馈</h3><Button variant="ghost" size="sm" onClick={() => setFeedback(null)}>收起</Button></div>{typeof feedback === 'string' ? <Notice error={/error|失败|^[A-Z_]+:/i.test(feedback)}>{feedback}</Notice> : <Details value={feedback} />}</div>}
  </div>
}

/** 概览保持入口导向，不在未读取监控数据时展示虚构在线人数或运营指标。 */
function OperationsOverview({ api, setView, allowed }: { api: RuntimeApi; setView: (value: OperationsView) => void; allowed: OperationsView[] }) {
  const status = useResource<Data>(api, '/api/status')
  const cards: { view: OperationsView; icon: typeof Users; description: string }[] = [
    { view: 'users', icon: Users, description: '查看角色与仓库，发放道具，管理账号状态。' },
    { view: 'shop', icon: ShoppingBag, description: '决定玩家可以买到的道具与使用期限。' },
    { view: 'maps', icon: Map, description: '设置本期开放地图、模式与难度。' },
    { view: 'campaigns', icon: Gift, description: '从奖励草稿到正式发布，管理活动。' },
    { view: 'monitoring', icon: Activity, description: '观察 CPU、内存、磁盘、网络与服务。' },
    { view: 'feedback', icon: MessageSquare, description: '回复玩家问题，跟进工单与截图。' },
  ]
  return <section className="nz-page"><PageHeading title="运营工作台" description="让每次管理有据可查，让每场挑战有序运行。" /><div className="nz-admin-welcome"><div><ShieldCheck size={30} /><h2>准备好，迎接下一批战友。</h2><p>先设置开放地图和商城，再发布活动。所有变更继续经过权限校验与审计。</p></div><span className="nz-tag">{status.data?.test_profile ? '合成测试环境' : status.data ? '当前服务环境' : '环境读取中'}</span></div>{status.error && <Notice error>{status.error}</Notice>}<div className="nz-admin-entry-grid">{cards.filter(card => allowed.includes(card.view)).map(card => <button key={card.view} onClick={() => setView(card.view)}><card.icon size={26} strokeWidth={1.4} /><h3>{operationsLabels[card.view]}</h3><p>{card.description}</p><ArrowRight size={18} /></button>)}</div><a className="nz-workbench-link" href="/console"><Boxes size={19} /><span><strong>完整工作台</strong><small>邀请、管理员、批量任务、复核、审计与原生规则等已有能力</small></span><ChevronRight size={19} /></a></section>
}

/** 发放使用目录下拉与预览确认，冻结提交正文及原幂等元数据，避免重试产生重复道具。 */
function GrantPanel({ api, accountId, refresh }: { api: RuntimeApi; accountId: string; refresh: () => void }) {
  const catalog = useResource<Catalog>(api, '/api/views/catalog')
  const [itemKey, setItemKey] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [duration, setDuration] = useState('')
  const [reason, setReason] = useState('')
  const [preview, setPreview] = useState<{ payload: Data; meta: Metadata; value: unknown } | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const retry = useRef(new RetryEnvelope())
  const { notify } = useActionFeedback()
  const options = Object.entries(catalog.data?.items || {})
  /** 表单任何输入改变即废弃预览，确认按钮只提交当前已核对的冻结内容。 */
  function change(update: () => void) { update(); setPreview(null); setMessage(null) }
  async function generate(event: FormEvent) {
    event.preventDefault()
    if (busy || !catalog.data) return
    setBusy(true); setMessage(null)
    try {
      const item = catalog.data.items[itemKey]
      if (!item) throw new Error('请从当前道具目录中选择有效道具。')
      const payload: Data = { target_account_id: accountId, catalog_version: catalog.data.id, changes: [{ type: 'item', item_key: itemKey, quantity, ...(duration ? { duration_seconds: Number(duration) * 86400 } : {}) }] }
      const meta = retry.current.prepare(payload, reason)
      const value = await api.request('/api/asset-preview', { ...payload, ...meta })
      setPreview({ payload, meta, value }); notify({ title: '发放预览已生成', message: '请核对道具、数量与有效期后确认。', tone: 'info' })
    } catch (error) { setMessage(errorText(error)); notify({ title: '操作未完成', message: errorText(error), tone: 'error' }) }
    finally { setBusy(false) }
  }
  async function confirm() {
    if (busy || !preview) return
    setBusy(true); setMessage(null)
    try { await api.command('asset.grant', preview.payload, preview.meta); retry.current.success(); setPreview(null); setMessage('道具已发放，仓库正在刷新。'); notify({ title: '道具已发放', message: '仓库正在刷新。', tone: 'success' }); refresh() }
    catch (error) { setMessage(errorText(error)); notify({ title: '操作未完成', message: errorText(error), tone: 'error' }) }
    finally { setBusy(false) }
  }
  return <Panel title="发放道具"><form className="nz-form-grid" onSubmit={generate}><div className="nz-field nz-span-2"><Label htmlFor="grant-item">道具目录</Label><CatalogItemPicker id="grant-item" items={catalog.data?.items || {}} value={itemKey} onChange={key => change(() => setItemKey(key))} /></div><div className="nz-field"><Label htmlFor="grant-quantity">数量</Label><Input id="grant-quantity" type="number" min={1} required value={quantity} onChange={event => change(() => setQuantity(Number(event.target.value)))} /></div><div className="nz-field"><Label htmlFor="grant-duration">有效天数（留空为永久）</Label><Input id="grant-duration" type="number" min={1} value={duration} onChange={event => change(() => setDuration(event.target.value))} /></div><div className="nz-field nz-span-2"><Label htmlFor="grant-reason">发放原因</Label><Input id="grant-reason" required maxLength={256} value={reason} onChange={event => change(() => setReason(event.target.value))} /></div><Button disabled={busy || !options.length} type="submit" variant="outline">{busy ? '处理中…' : '生成发放预览'}</Button></form>{catalog.error && <Notice error>{catalog.error}</Notice>}{preview && <div className="nz-preview"><h4>确认发放内容</h4><p className="nz-form-hint">{String(catalog.data?.items[itemKey]?.name || catalog.data?.items[itemKey]?.label || itemKey)} × {quantity} · {duration ? `${duration} 天` : '永久'}</p><details className="nz-details"><summary>查看完整变更预览</summary><Details value={preview.value} /></details><Button disabled={busy} onClick={() => void confirm()}>确认本次发放</Button></div>}{message && <Notice error={!message.startsWith('道具已')}>{message}</Notice>}</Panel>
}

/** 用户详情使用真实库存实例；未知版本的记录禁止回收，防止对过期快照扣减资产。 */
function UserDetail({ api, accountId, close, report, canAssets, canUsers }: { api: RuntimeApi; accountId: string; close: () => void; report: (value: unknown) => void; canAssets: boolean; canUsers: boolean }) {
  const user = useResource<Data>(api, `/api/views/user?account_id=${encodeURIComponent(accountId)}`)
  const [reason, setReason] = useState('')
  const detail = user.data
  const rawInventory = detail?.native_inventory as unknown as { items?: InventoryItem[] } | undefined
  const items = rawInventory?.items || (Array.isArray(detail?.inventory) ? detail.inventory as unknown as InventoryItem[] : [])
  const itemLabels = detail?.item_labels as Record<string, string> | undefined
  // 当前管理区只显示仍有效的封禁；历史撤销记录保留在完整详情与审计中。
  const sanctions = (Array.isArray(detail?.sanctions) ? detail.sanctions as Data[] : []).filter(sanction => !sanction.revoked && (!sanction.expires_at || Number(sanction.expires_at) > Date.now() / 1000))
  return <><Button variant="ghost" onClick={close}><ArrowLeft size={16} />返回玩家列表</Button><PageHeading title={String(detail?.email || detail?.login || '玩家详情')} description="账号状态、角色信息和仓库变更集中管理。" action={<Button variant="outline" onClick={user.refresh}><RefreshCw size={15} />刷新</Button>} />{user.error && <Notice error>{user.error}</Notice>}{user.loading && <p className="nz-loading">正在读取玩家详情…</p>}{detail && <><div className="nz-account-summary"><span className="nz-avatar">{String(detail.nickname || detail.email || detail.login || '玩').slice(0, 1)}</span><div><h2>{String((detail.character as Data)?.nickname || detail.nickname || detail.login || '玩家')}</h2><p>{sanctions.length ? '封禁中' : accountStatusLabel(detail.status)}</p></div><div><span>军衔等级</span><strong>{detail.level === null || detail.level === undefined ? '待接入' : String(detail.level)}</strong></div><div><span>已读取仓库</span><strong>{items.length} 件</strong></div></div><div className="nz-admin-split">{canAssets && <GrantPanel key={accountId} api={api} accountId={accountId} refresh={user.refresh} />}{canUsers && <Panel title="账号管理"><div className="nz-inline-actions"><CommandButton api={api} action="user.approve" data={{ target_account_id: accountId }} reason="管理员批准账号" onDone={user.refresh} variant="outline">批准注册</CommandButton><CommandButton api={api} action="user.activate" data={{ target_account_id: accountId }} reason="管理员激活账号" onDone={user.refresh} variant="outline">激活账号</CommandButton><CommandButton api={api} action="session.revoke" data={{ target_account_id: accountId }} reason="管理员结束玩家会话" confirm="确认结束该玩家当前会话？" onDone={user.refresh} variant="outline">结束会话</CommandButton></div><BusinessForm title="封禁玩家" fields={[{ name: 'hours', label: '封禁小时（0 为永久）', type: 'number' }, { name: 'reason', label: '封禁原因' }]} report={report} submit={(values, meta) => submitCommand(api, 'sanction.create', { target_account_id: accountId, expires_at: Number(values.hours) === 0 ? null : Math.floor(Date.now() / 1000) + Number(values.hours) * 3600 }, meta, user.refresh)} />{sanctions.map(sanction => <div key={String(sanction.id)} className="nz-sanction"><span>{String(sanction.reason || '账号封禁')}<small>{formatDate(Number(sanction.expires_at), '永久')}</small></span><CommandButton api={api} action="sanction.revoke" data={{ sanction_id: sanction.id }} reason="管理员解除玩家封禁" confirm="确认解除这条封禁？其他有效封禁仍会生效。" onDone={user.refresh} variant="outline">解除封禁</CommandButton></div>)}</Panel>}</div>
    <div className="nz-section-title"><h2>玩家仓库</h2><span>回收操作将记录完整流水</span></div><div className="nz-field nz-reason-field"><Label htmlFor="remove-reason">回收原因</Label><Input id="remove-reason" placeholder="回收前请填写原因" value={reason} onChange={event => setReason(event.target.value)} /></div>{items.length ? <div className="nz-table-wrap"><table className="nz-table"><thead><tr><th>道具</th><th>数量</th><th>有效期</th><th>操作</th></tr></thead><tbody>{items.map((item, index) => <tr key={item.instance_id || item.id || index}><td><strong className="nz-admin-item-name"><ItemArtwork itemId={item.item_id} itemKey={item.item_key} name={item.name || itemLabels?.[item.item_key || '']} />{itemName(item.item_key || item.item_id, item.name || itemLabels?.[item.item_key || ''])}</strong><details className="nz-details"><summary>技术详情</summary><p>实例编号：{item.instance_id || item.id}</p><p>道具资源：{item.item_key || item.item_id}</p></details></td><td>{item.quantity}</td><td>{formatDate(item.expires_at, '永久')}</td><td><CommandButton api={api} action="asset.remove" data={{ target_account_id: accountId, instance_id: item.instance_id || item.id || '', quantity: item.quantity, expected_version: item.version ?? 0 }} reason={reason} disabled={!canAssets || !reason.trim() || item.version === undefined || !(item.instance_id || item.id)} confirm={`确认回收 ${itemName(item.item_key || item.item_id, item.name || itemLabels?.[item.item_key || ''])} 的全部 ${item.quantity} 件？`} onDone={user.refresh} variant="destructive"><Trash2 size={14} />回收</CommandButton></td></tr>)}</tbody></table></div> : <Empty title="没有可显示的仓库道具">真实库存尚未提供或当前仓库为空。请刷新，或在完整工作台查看账号原始详情。</Empty>}<details className="nz-details"><summary>账号完整详情</summary><Details value={detail} /></details></>}
  </>
}

/** 搜索触发明确查询，不在每次键入时请求；切换详情后保留列表搜索条件。 */
function UsersManager({ api, report, canAssets, canUsers }: { api: RuntimeApi; report: (value: unknown) => void; canAssets: boolean; canUsers: boolean }) {
  const [search, setSearch] = useState('')
  const [applied, setApplied] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const users = useResource<Data[]>(api, `/api/views/users?search=${encodeURIComponent(applied)}`)
  if (selected) return <section className="nz-page"><UserDetail api={api} canAssets={canAssets} canUsers={canUsers} accountId={selected} close={() => { setSelected(null); users.refresh() }} report={report} /></section>
  return <section className="nz-page"><PageHeading title="玩家管理" description="查看已注册玩家的资料与仓库，管理每一次资产和账号操作。" /><form className="nz-search-form" onSubmit={event => { event.preventDefault(); setApplied(search); users.refresh() }}><Search size={18} /><Input aria-label="搜索玩家" placeholder="搜索邮箱、账号或昵称" value={search} onChange={event => setSearch(event.target.value)} /><Button type="submit">搜索玩家</Button></form>{users.error && <Notice error>{users.error}</Notice>}{users.loading ? <p className="nz-loading">正在读取玩家…</p> : users.data?.length ? <div className="nz-table-wrap"><table className="nz-table"><thead><tr><th>玩家</th><th>状态</th><th>军衔</th><th>注册时间</th><th /></tr></thead><tbody>{users.data.map(user => <tr key={String(user.id)}><td><strong>{String(user.email || user.login)}</strong><span className="nz-table-sub">{String(user.nickname || user.login || '')}</span></td><td><span className={`nz-tag ${user.status === 'ACTIVE' ? 'is-positive' : ''}`}>{accountStatusLabel(user.status)}</span></td><td>{user.level == null ? <span className="nz-muted">待接入</span> : String(user.level)}</td><td>{formatDate(Number(user.created_at))}</td><td><Button variant="ghost" onClick={() => setSelected(String(user.id))}>管理玩家 <ChevronRight size={14} /></Button></td></tr>)}</tbody></table></div> : <Empty title="暂未找到玩家" icon={<Users size={36} />}>换个关键词，或等待玩家完成注册后刷新。</Empty>}</section>
}

/** 商品沿用已有价格档位；未开放期限编辑的商品保持服务端原值不传。 */
function OfferEditor({ api, offer, revision, refresh, readOnly }: { api: RuntimeApi; offer: ShopOffer; revision: number; refresh: () => void; readOnly: boolean }) {
  const [enabled, setEnabled] = useState(offer.enabled)
  const [order, setOrder] = useState(offer.sort_order)
  const [prices, setPrices] = useState(offer.prices)
  const [periods, setPeriods] = useState(offer.period_hours)
  return <Panel className="nz-offer-editor"><div className="nz-offer-heading"><span className="nz-small-symbol"><ShoppingBag size={24} /></span><div><h3>{offer.label}</h3><span>{assetCodeLabel('currency', offer.currency)}</span><details className="nz-details"><summary>技术详情</summary><p>商品编号：{offer.commodity_id}</p><p>币种代码：{offer.currency}</p></details></div><label className="nz-checkbox"><input type="checkbox" checked={enabled} disabled={readOnly} onChange={event => setEnabled(event.target.checked)} />上架</label></div><div className="nz-offer-tiers">{prices.map((price, index) => <div key={index}><Label htmlFor={`offer-${offer.commodity_id}-${index}`}>价格 · 档位 {index + 1}</Label><Input id={`offer-${offer.commodity_id}-${index}`} type="number" min={0} value={price} disabled={readOnly} onChange={event => setPrices(previous => previous.map((value, n) => n === index ? Number(event.target.value) : value))} /><Label>{offer.period_editable ? '期限小时（-1 为永久）' : '固定档位参数'}<Input aria-label={`${offer.label} 档位 ${index + 1} 使用小时`} type="number" min={-1} value={periods[index] ?? 0} disabled={readOnly || !offer.period_editable} onChange={event => setPeriods(previous => previous.map((value, n) => n === index ? Number(event.target.value) : value))} /></Label></div>)}</div><div className="nz-offer-footer"><Label>展示顺序<Input aria-label={`${offer.label} 展示顺序`} type="number" value={order} disabled={readOnly} onChange={event => setOrder(Number(event.target.value))} /></Label>{!readOnly && <CommandButton api={api} action="shop.offer.save" data={{ commodity_id: offer.commodity_id, expected_revision: revision, enabled, sort_order: order, prices, ...(offer.period_editable ? { period_hours: periods } : {}) }} reason="管理员更新商城商品" onDone={refresh}>保存商品</CommandButton>}</div></Panel>
}

/** 管理商城展示服务端分页的真实商品，修改冲突由 expected_revision 阻止覆盖。 */
function ShopManager({ api, readOnly }: { api: RuntimeApi; readOnly: boolean }) {
  const [search, setSearch] = useState('')
  const [applied, setApplied] = useState('')
  const [offset, setOffset] = useState(0)
  const shop = useResource<ShopList>(api, `/api/views/shop-admin?limit=20&offset=${offset}&search=${encodeURIComponent(applied)}`)
  return <section className="nz-page"><PageHeading title="商城管理" description="选择上架商品，调整价格与期限。所有商品均来自已登记的客户端目录。" /><form className="nz-search-form" onSubmit={event => { event.preventDefault(); setOffset(0); setApplied(search); shop.refresh() }}><Search size={18} /><Input aria-label="搜索商品" placeholder="搜索商品名称或编号" value={search} onChange={event => setSearch(event.target.value)} /><Button type="submit">查询商品</Button></form>{shop.error && <Notice error>{shop.error}</Notice>}{shop.loading && <p className="nz-loading">正在读取商城目录…</p>}<div className="nz-offer-grid">{shop.data?.items.map(offer => <OfferEditor readOnly={readOnly} key={`${offer.commodity_id}-${shop.data?.revision}`} api={api} offer={offer} revision={shop.data!.revision} refresh={shop.refresh} />)}</div>{!shop.loading && !shop.error && !shop.data?.items.length && <Empty title="当前没有匹配商品" icon={<ShoppingBag size={35} />}>试试其他关键词，或先登记客户端商品目录。</Empty>}<div className="nz-pagination"><Button variant="outline" disabled={!offset || shop.loading} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</Button><span>共 {shop.data?.total ?? 0} 件 · 第 {Math.floor(offset / 20) + 1} 页</span><Button variant="outline" disabled={shop.loading || offset + 20 >= (shop.data?.total || 0)} onClick={() => setOffset(value => value + 20)}>下一页</Button></div></section>
}

/** 地图策略采用全量保存，页面编辑副本不修改已读取快照，默认选项由服务端提供。 */
function MapManager({ api }: { api: RuntimeApi }) {
  const policy = useResource<{ revision: number; maps: PlayMap[] }>(api, '/api/views/play-policy')
  const [maps, setMaps] = useState<PlayMap[]>([])
  useEffect(() => { if (policy.data) setMaps(policy.data.maps.map(map => ({ ...map, submodes: [...map.submodes] }))) }, [policy.data])
  /** 选中难度仅来自 available_submodes；保留地图 ID 与类型，防止界面创建不存在的原生模式。 */
  function update(id: number, values: Partial<PlayMap>) { setMaps(previous => previous.map(map => map.map_id === id ? { ...map, ...values } : map)) }
  return <section className="nz-page"><PageHeading title="地图与模式" description="设置开放挑战。未开放的组合无法创建房间，玩家会收到明确提示。" action={policy.data && <CommandButton api={api} action="play-policy.save" data={{ expected_revision: policy.data.revision, maps: maps.map(map => ({ map_id: map.map_id, enabled: !!map.enabled, submodes: map.submodes })) }} reason="管理员调整开放地图与难度" confirm="确认发布新的开放地图与模式？后续建房与开局将使用新策略。" onDone={policy.refresh}>保存开放策略</CommandButton>} />{policy.error && <Notice error>{policy.error}</Notice>}{policy.loading && <p className="nz-loading">正在读取开放策略…</p>}<div className="nz-policy-grid">{maps.map(map => <Panel key={map.map_id} className={map.enabled ? 'is-enabled' : ''}><div className="nz-policy-heading"><Map size={27} strokeWidth={1.3} /><span className="nz-tag">{mapKindLabel(map.kind)}</span><label className="nz-checkbox"><input type="checkbox" checked={!!map.enabled} onChange={event => update(map.map_id, { enabled: event.target.checked })} />开放</label></div><h3>{map.name}</h3>{map.map_id === 160 && <p className="nz-variant-note">同名变体 · 首发默认关闭</p>}<div className="nz-policy-difficulties">{difficultyChoices(map.available_submodes || map.submodes).map(option => <label className="nz-checkbox" key={option.value}><input type="checkbox" disabled={!map.enabled} checked={map.submodes.includes(option.value)} onChange={event => update(map.map_id, { submodes: event.target.checked ? [...map.submodes, option.value] : map.submodes.filter(value => value !== option.value) })} />{option.label}</label>)}</div><details className="nz-details"><summary>技术详情</summary><p>地图编号：{map.map_id}；模式编号：{map.mode_id}。</p>{difficultyChoices(map.available_submodes || map.submodes).map(option => <p key={option.value}>{option.label}：{option.value}</p>)}</details></Panel>)}</div></section>
}

interface RewardDraft { id: string; key?: string; title: string; type: RewardType; threshold: string; item: string; quantity: string; duration: string; rankFrom: string; rankTo: string; priority: string; additionalChanges?: Reward['changes']; mapIds?: number[]; difficulties?: number[]; playerCounts?: number[] }

/** 新活动默认空奖励草稿，运营者必须明确填写真实目录道具，不能意外发布预设奖品。 */
function newReward(): RewardDraft { return { id: crypto.randomUUID(), title: '', type: 'CHECKIN_DAYS', threshold: '1', item: '', quantity: '1', duration: '', rankFrom: '1', rankTo: '1', priority: '1' } }

/** 一个奖励档位可包含多种道具；编辑已有配置时保留全部内容，避免只保存第一件造成丢失。 */
function ExtraRewardItems({ items, change, catalogItems }: { items: Reward['changes']; change: (items: Reward['changes']) => void; catalogItems: Record<string, Data> }) {
  function update(index: number, values: Partial<Reward['changes'][number]>) { change(items.map((item, n) => n === index ? { ...item, ...values } : item)) }
  return <div className="nz-extra-rewards">{items.map((item, index) => <div className="nz-extra-reward" key={index}><Label>附加道具<CatalogItemPicker id={`extra-reward-${index}-${item.item_key}`} items={catalogItems} value={item.item_key} onChange={key => update(index, { item_key: key })} /></Label><Label>数量<Input aria-label={`附加道具 ${index + 1} 数量`} required type="number" min={1} value={item.quantity} onChange={event => update(index, { quantity: Number(event.target.value) })} /></Label><Label>天数（留空永久）<Input aria-label={`附加道具 ${index + 1} 天数`} type="number" min={1} value={item.duration_seconds ? item.duration_seconds / 86400 : ''} onChange={event => { const next = { ...item }; if (event.target.value) next.duration_seconds = Number(event.target.value) * 86400; else delete next.duration_seconds; change(items.map((value, n) => n === index ? next : value)) }} /></Label><Button type="button" variant="ghost" aria-label={`移除附加道具 ${index + 1}`} onClick={() => change(items.filter((_, n) => n !== index))}><Trash2 size={14} /></Button></div>)}<Button type="button" variant="ghost" disabled={items.length >= 19} onClick={() => change([...items, { type: 'item', item_key: '', quantity: 1 }])}><Plus size={14} />同一奖励增加道具</Button></div>
}

/** 榜单奖励可限定地图、难度和人数；全部不选表示按当前期活动内所有榜单判定。 */
function RankingRewardFilters({ row, maps, change }: { row: RewardDraft; maps: PlayMap[]; change: (values: Partial<RewardDraft>) => void }) {
  const difficulties = [...new Set(maps.flatMap(map => map.available_submodes || map.submodes))].sort((a, b) => a - b)
  function toggle(field: 'mapIds' | 'difficulties' | 'playerCounts', value: number, checked: boolean) {
    const previous = row[field] || []
    change({ [field]: checked ? [...previous, value] : previous.filter(item => item !== value) })
  }
  return <details className="nz-reward-filters"><summary>限定排行奖励范围（选填）</summary><p>留空表示不限制该项。每名玩家仍只获得本期最高一档排行奖励。</p><div><h4>挑战地图</h4>{maps.map(map => <label className="nz-checkbox" key={map.map_id}><input type="checkbox" checked={row.mapIds?.includes(map.map_id) || false} onChange={event => toggle('mapIds', map.map_id, event.target.checked)} />{map.name}</label>)}</div><div><h4>挑战难度</h4>{difficultyChoices(difficulties).map(option => <label className="nz-checkbox" key={option.value}><input type="checkbox" checked={row.difficulties?.includes(option.value) || false} onChange={event => toggle('difficulties', option.value, event.target.checked)} />{option.label}</label>)}<details className="nz-details"><summary>技术详情</summary>{difficultyChoices(difficulties).map(option => <p key={option.value}>{option.label}：{option.value}</p>)}</details></div><div><h4>小队人数</h4>{Array.from({ length: 8 }, (_, index) => index + 1).map(count => <label className="nz-checkbox" key={count}><input type="checkbox" checked={row.playerCounts?.includes(count) || false} onChange={event => toggle('playerCounts', count, event.target.checked)} />{count} 人</label>)}</div></details>
}

/** 活动编辑器提供五种奖励条件，生成服务端冻结定义；保存不自动发布或发奖。 */
function CampaignEditor({ api, refresh, report, campaign, clear }: { api: RuntimeApi; refresh: () => void; report: (value: unknown) => void; campaign?: Campaign | null; clear: () => void }) {
  const [title, setTitle] = useState('')
  const [starts, setStarts] = useState('')
  const [ends, setEnds] = useState('')
  const [rewards, setRewards] = useState<RewardDraft[]>([newReward()])
  const [busy, setBusy] = useState(false)
  const retry = useRef(new RetryEnvelope())
  const catalog = useResource<Catalog>(api, '/api/views/catalog')
  const policy = useResource<{ maps: PlayMap[] }>(api, '/api/views/play-policy')
  /** 编辑既有草稿时转换北京时间与奖励规则；已有奖励 key 保持稳定，保存携带 revision。 */
  useEffect(() => {
    const localTime = (seconds: number) => new Date((seconds + 8 * 3600) * 1000).toISOString().slice(0, 16)
    setTitle(campaign?.title || ''); setStarts(campaign ? localTime(campaign.definition.starts_at) : ''); setEnds(campaign ? localTime(campaign.definition.ends_at) : '')
    setRewards(campaign ? campaign.definition.rewards.map(rule => ({ id: crypto.randomUUID(), key: rule.key, title: rule.title, type: rule.type, threshold: String(rule.type === 'PLAYTIME_SECONDS' ? Number(rule.threshold) / 60 : rule.threshold || 1), item: rule.changes[0]?.item_key || '', quantity: String(rule.changes[0]?.quantity || 1), duration: rule.changes[0]?.duration_seconds ? String(rule.changes[0].duration_seconds / 86400) : '', rankFrom: String(rule.rank_from || 1), rankTo: String(rule.rank_to || 1), priority: String(rule.priority || 1), additionalChanges: rule.changes.slice(1).map(change => ({ ...change })), mapIds: [...(rule.map_ids || [])], difficulties: [...(rule.difficulties || [])], playerCounts: [...(rule.player_counts || [])] })) : [newReward()])
  }, [campaign])
  /** 编辑副本不改变已有活动；发布后的定义不可改，继续调整时建立新的草稿。 */
  function update(id: string, values: Partial<RewardDraft>) { setRewards(previous => previous.map(row => row.id === id ? { ...row, ...values } : row)) }
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return
    setBusy(true)
    try {
      // datetime-local 明确表示北京时间；加上 +08:00，避免管理员本机时区改变活动实际时间。
      const startsAt = new Date(`${starts}:00+08:00`).getTime() / 1000
      const endsAt = new Date(`${ends}:00+08:00`).getTime() / 1000
      if (!Number.isFinite(startsAt) || !Number.isFinite(endsAt) || endsAt <= startsAt) throw new Error('活动结束时间必须晚于开始时间。')
      if (!rewards.length) throw new Error('请至少添加一项奖励。')
      if (rewards.some(row => !catalog.data?.items[row.item] || row.additionalChanges?.some(change => !catalog.data?.items[change.item_key]))) throw new Error('请为每项奖励选择完整的目录道具名称。')
      const rules: Reward[] = rewards.map(row => ({ key: row.key || `reward-${row.id}`, title: row.title, type: row.type,
        ...(row.type === 'LEADERBOARD' ? { rank_from: Number(row.rankFrom), rank_to: Number(row.rankTo), priority: Number(row.priority), ...(row.mapIds?.length ? { map_ids: row.mapIds } : {}), ...(row.difficulties?.length ? { difficulties: row.difficulties } : {}), ...(row.playerCounts?.length ? { player_counts: row.playerCounts } : {}) } : { threshold: row.type === 'PLAYTIME_SECONDS' ? Number(row.threshold) * 60 : Number(row.threshold) }),
        changes: [{ type: 'item', item_key: row.item, quantity: Number(row.quantity), ...(row.duration ? { duration_seconds: Number(row.duration) * 86400 } : {}) }, ...(row.additionalChanges || [])],
      }))
      const payload = { ...(campaign ? { campaign_id: campaign.id, expected_revision: campaign.revision } : {}), title, definition: { starts_at: startsAt, ends_at: endsAt, timezone: 'Asia/Shanghai', rewards: rules } } as unknown as Data
      await api.command('campaign.save', payload, retry.current.prepare(payload, '管理员保存活动草稿'))
      retry.current.success(); setTitle(''); setStarts(''); setEnds(''); setRewards([newReward()]); refresh(); clear(); report('活动草稿已保存。审核配置后再发布，当前不会发放奖励。')
    } catch (error) { report(errorText(error)) }
    finally { setBusy(false) }
  }
  return <Panel title={campaign ? `编辑草稿：${campaign.title}` : '新建活动草稿'}><form onSubmit={save} className="nz-campaign-editor"><div className="nz-form-grid"><div className="nz-field nz-span-2"><Label htmlFor="campaign-title">活动名称</Label><Input id="campaign-title" required maxLength={120} value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：开服集结礼" /></div><div className="nz-field"><Label htmlFor="campaign-start">开始时间（北京时间）</Label><Input id="campaign-start" type="datetime-local" required value={starts} onChange={event => setStarts(event.target.value)} /></div><div className="nz-field"><Label htmlFor="campaign-end">结束时间（北京时间）</Label><Input id="campaign-end" type="datetime-local" required value={ends} onChange={event => setEnds(event.target.value)} /></div></div>
    {rewards.map((row, index) => <fieldset key={row.id} className="nz-reward-editor"><legend>奖励 {index + 1}</legend><div className="nz-form-grid"><div className="nz-field"><Label htmlFor={`reward-title-${row.id}`}>奖励名称</Label><Input id={`reward-title-${row.id}`} required value={row.title} onChange={event => update(row.id, { title: event.target.value })} /></div><div className="nz-field"><Label htmlFor={`reward-type-${row.id}`}>达成条件</Label><select id={`reward-type-${row.id}`} value={row.type} onChange={event => update(row.id, { type: event.target.value as RewardType })}>{Object.entries(rewardLabels).map(([type, label]) => <option key={type} value={type}>{label}</option>)}</select></div>{row.type === 'LEADERBOARD' ? <><div className="nz-field"><Label htmlFor={`reward-rank-from-${row.id}`}>起始名次</Label><Input id={`reward-rank-from-${row.id}`} type="number" min={1} required value={row.rankFrom} onChange={event => update(row.id, { rankFrom: event.target.value })} /></div><div className="nz-field"><Label htmlFor={`reward-rank-to-${row.id}`}>结束名次</Label><Input id={`reward-rank-to-${row.id}`} type="number" min={Number(row.rankFrom) || 1} required value={row.rankTo} onChange={event => update(row.id, { rankTo: event.target.value })} /></div><div className="nz-field"><Label htmlFor={`reward-priority-${row.id}`}>奖励档位（越大越优先）</Label><Input id={`reward-priority-${row.id}`} type="number" min={1} required value={row.priority} onChange={event => update(row.id, { priority: event.target.value })} /></div></> : <div className="nz-field"><Label htmlFor={`reward-threshold-${row.id}`}>{row.type === 'PLAYTIME_SECONDS' ? '累计分钟' : row.type === 'CLEAR_COUNT' ? '成功通关次数' : '累计天数'}</Label><Input id={`reward-threshold-${row.id}`} type="number" min={1} required value={row.threshold} onChange={event => update(row.id, { threshold: event.target.value })} /></div>}<div className="nz-field"><Label htmlFor={`reward-item-${row.id}`}>奖励道具</Label><CatalogItemPicker id={`reward-item-${row.id}`} items={catalog.data?.items || {}} value={row.item} onChange={key => update(row.id, { item: key })} /></div><div className="nz-field"><Label htmlFor={`reward-quantity-${row.id}`}>道具数量</Label><Input id={`reward-quantity-${row.id}`} type="number" min={1} required value={row.quantity} onChange={event => update(row.id, { quantity: event.target.value })} /></div><div className="nz-field"><Label htmlFor={`reward-duration-${row.id}`}>有效天数（留空永久）</Label><Input id={`reward-duration-${row.id}`} type="number" min={1} value={row.duration} onChange={event => update(row.id, { duration: event.target.value })} /></div></div><ExtraRewardItems catalogItems={catalog.data?.items || {}} items={row.additionalChanges || []} change={items => update(row.id, { additionalChanges: items })} />{row.type === 'LEADERBOARD' && <RankingRewardFilters row={row} maps={(policy.data?.maps || []).filter(map => [113, 164, 191, 141, 140, 168, 96].includes(map.map_id))} change={values => update(row.id, values)} />}<Button type="button" variant="ghost" disabled={rewards.length === 1} onClick={() => setRewards(previous => previous.filter(reward => reward.id !== row.id))}><Trash2 size={14} />移除此奖励</Button></fieldset>)}
    <div className="nz-inline-actions"><Button type="button" variant="outline" onClick={() => setRewards(previous => [...previous, newReward()])}><Plus size={15} />添加奖励</Button><Button type="submit" disabled={busy}>{busy ? '保存中…' : '保存为草稿'}</Button>{campaign && <Button type="button" variant="ghost" disabled={busy} onClick={clear}>取消编辑</Button>}</div><p className="nz-form-hint">草稿不会发奖。发布后条件和奖励将冻结；普通奖励由玩家手动领取，排行奖励封榜后自动发放。</p></form></Panel>
}

/** 活动状态操作独立于编辑，发布、暂停和封榜都有明确确认，避免把保存草稿误当发布。 */
function CampaignManager({ api, report, readOnly }: { api: RuntimeApi; report: (value: unknown) => void; readOnly: boolean }) {
  const campaigns = useResource<Campaign[]>(api, '/api/views/campaign-admin')
  const [editing, setEditing] = useState<Campaign | null>(null)
  return <section className="nz-page"><PageHeading title="活动" description="配置签到、登录、时长、通关与排行奖励。从草稿开始，审核后发布。" />{!readOnly && <CampaignEditor api={api} refresh={campaigns.refresh} report={report} campaign={editing} clear={() => setEditing(null)} />}{campaigns.error && <Notice error>{campaigns.error}</Notice>}<div className="nz-section-title"><h2>活动列表</h2><Button variant="ghost" onClick={campaigns.refresh}><RefreshCw size={14} />刷新</Button></div>{campaigns.data?.map(campaign => <Panel key={campaign.id}><div className="nz-campaign-admin-heading"><div><h3>{campaign.title}</h3><p>{formatDate(campaign.definition.starts_at, '暂无', 'Asia/Shanghai')} 至 {formatDate(campaign.definition.ends_at, '暂无', 'Asia/Shanghai')}（北京时间）</p></div><span className="nz-tag">{campaignLabels[campaign.state]}</span></div>{!readOnly && <div className="nz-inline-actions">{campaign.state === 'DRAFT' && <Button variant="outline" onClick={() => { setEditing(campaign); window.scrollTo({ top: 140, behavior: 'smooth' }) }}>编辑草稿</Button>}{(campaign.state === 'DRAFT' || campaign.state === 'PAUSED') && <CommandButton api={api} action="campaign.publish" data={{ campaign_id: campaign.id }} reason="管理员发布活动" confirm="发布后奖励定义将冻结。确认奖励道具、门槛和时间无误后继续。" onDone={campaigns.refresh}>{campaign.state === 'PAUSED' ? '恢复活动' : '发布活动'}</CommandButton>}{campaign.state === 'PUBLISHED' && <CommandButton api={api} action="campaign.pause" data={{ campaign_id: campaign.id }} reason="管理员暂停活动" confirm="确认暂停本期活动？" onDone={campaigns.refresh} variant="outline">暂停活动</CommandButton>}{campaign.state !== 'DRAFT' && <CommandButton api={api} action="leaderboard.finalize" data={{ campaign_id: campaign.id }} reason="管理员封存活动排行榜" confirm="封榜后排行结果固定，并自动发放符合条件的排行奖励。确认继续？" onDone={campaigns.refresh} variant="outline"><Trophy size={15} />封榜</CommandButton>}{campaign.state !== 'ENDED' && <CommandButton api={api} action="campaign.end" data={{ campaign_id: campaign.id }} reason="管理员结束活动" confirm="确认结束本期活动？" onDone={campaigns.refresh} variant="outline">结束活动</CommandButton>}</div>}<CampaignPresentationEditor key={`${campaign.id}-${campaign.presentation?.revision || 1}`} api={api} campaign={campaign} refresh={campaigns.refresh} readOnly={readOnly} />{campaign.payouts && <div className="nz-payout-summary"><span><Check size={14} />已发放 {campaign.payouts.succeeded}</span><span>待发放 {campaign.payouts.pending}</span><span className={campaign.payouts.failed ? 'is-error' : ''}>发放失败 {campaign.payouts.failed}</span></div>}<details className="nz-details"><summary>查看奖励配置</summary><div className="nz-campaign-rule-list">{campaign.definition.rewards.map(reward => <div key={reward.key}><strong>{reward.title}</strong><span>{rewardLabels[reward.type]}</span><span>{reward.changes.map(change => `${itemName(change.item_key)} × ${change.quantity}`).join('、')}</span></div>)}</div></details></Panel>)}{!campaigns.loading && !campaigns.error && !campaigns.data?.length && <Empty title="第一期活动，从这里开始" icon={<Gift size={36} />}>填写上方草稿，审核奖励后再发布。</Empty>}</section>
}

interface MonitorNode { id: string; name: string; platform: string; last_seen: number | null; state: string; cpu_percent: number | null; memory_total: number | null; memory_used: number | null; memory_percent: number | null; rx_bps: number | null; tx_bps: number | null; disks?: unknown[]; services?: Record<string, unknown>; thresholds?: Data; revision?: number; enabled?: boolean; alerts?: string[] }
interface Monitoring { nodes: MonitorNode[]; samples: { timestamp?: number; at?: number; cpu_percent?: number; memory_percent?: number; rx_bps?: number; tx_bps?: number }[]; retention: { raw_days: number; aggregate_days: number } }

/** 指标缺失显示待采集，不把未知值当作零；容量根据字节转换成可读单位。 */
function metric(value: number | null | undefined, unit = '%') { return value === null || value === undefined ? '待采集' : `${value.toFixed(1)}${unit}` }
function bytes(value: number | null | undefined) { return value === null || value === undefined ? '待采集' : `${(value / 1024 ** 3).toFixed(1)} GiB` }
function bandwidth(value: number | null | undefined) { return value === null || value === undefined ? '待采集' : `${(value / 1024).toFixed(1)} KiB/s` }

/** CPU/内存时间序列只画真实样本；少于两个采样点时显示等待，不伪造平滑曲线。 */
function MetricTrend({ samples, field, title }: { samples: Monitoring['samples']; field: 'cpu_percent' | 'memory_percent'; title: string }) {
  const values = samples.map(sample => sample[field]).filter((value): value is number => typeof value === 'number')
  return <div className="nz-trend"><h4>{title}</h4>{values.length >= 2 ? <svg viewBox="0 0 500 130" role="img" aria-label={`${title}，${values.length} 个真实采样点`}><path d="M0 30H500M0 65H500M0 100H500" stroke="currentColor" opacity=".12" fill="none" /><polyline points={values.map((value, index) => `${index * 500 / (values.length - 1)},${120 - Math.max(0, Math.min(100, value)) * 1.1}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg> : <p>等待更多真实采样点</p>}</div>
}

/** 监控展示最后采样时间、离线状态与缺失值；只在当前页可见时定时刷新，不制造告警消息。 */
function MonitoringPage({ api, owner, report }: { api: RuntimeApi; owner: boolean; report: (value: unknown) => void }) {
  const [selected, setSelected] = useState('')
  const [enrollment, setEnrollment] = useState<{ node_token: string; node_id?: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const monitoring = useResource<Monitoring>(api, `/api/views/monitoring${selected ? `?node_id=${encodeURIComponent(selected)}` : ''}`)
  const node = monitoring.data?.nodes.find(value => value.id === selected) || monitoring.data?.nodes[0]
  // 初次读取后选中真实首节点，再请求该节点趋势；无节点时不构造示例曲线。
  useEffect(() => { if (!selected && monitoring.data?.nodes[0]) setSelected(monitoring.data.nodes[0].id) }, [selected, monitoring.data])
  /** 令牌仅存在当前组件内存，离开页面即卸载；复制必须由管理员点击，不自动写入剪贴板。 */
  function enrolled(value: unknown) {
    if (value && typeof value === 'object' && 'node_token' in value && typeof value.node_token === 'string') {
      setEnrollment(value as { node_token: string; node_id?: string }); setCopied(false)
    } else report(value)
  }
  async function copyToken() {
    if (!enrollment) return
    try { await navigator.clipboard.writeText(enrollment.node_token); setCopied(true) }
    catch { report('剪贴板不可用，请在令牌输入框中手动复制。') }
  }
  useEffect(() => { const timer = window.setInterval(() => { if (!document.hidden) monitoring.refresh() }, 30000); return () => clearInterval(timer) }, [monitoring.refresh])
  const metrics = node ? [
    { title: 'CPU 使用率', value: metric(node.cpu_percent), icon: Cpu, detail: node.platform },
    { title: '内存使用率', value: metric(node.memory_percent), icon: MemoryStick, detail: `${bytes(node.memory_used)} / ${bytes(node.memory_total)}` },
    { title: '网络接收', value: bandwidth(node.rx_bps), icon: Wifi, detail: '接收速率' },
    { title: '网络发送', value: bandwidth(node.tx_bps), icon: Wifi, detail: '发送速率' },
  ] : []
  return <section className="nz-page"><PageHeading title="服务器监控" description="实时观察资源与服务状态。离线与缺失数据始终明确显示。" action={<Button variant="outline" onClick={monitoring.refresh}><RefreshCw size={15} />立即刷新</Button>} />{monitoring.error && <Notice error>{monitoring.error}</Notice>}<div className="nz-monitor-layout"><aside className="nz-node-list">{monitoring.data?.nodes.map(value => <button key={value.id} className={node?.id === value.id ? 'is-selected' : ''} onClick={() => setSelected(value.id)}><Server size={20} /><span><strong>{value.name}</strong><small>{value.platform}</small></span><i className={value.state === 'ONLINE' || value.state === 'HEALTHY' ? 'is-online' : ''} /></button>)}</aside><div>{node ? <><div className="nz-monitor-heading"><h2>{node.name}</h2><span className="nz-tag">{{ ONLINE: '在线', STALE: '离线', NO_DATA: '待采集', DISABLED: '已停用' }[node.state] || node.state}</span><small>最后采集 {formatDate(node.last_seen, '尚未采集')}</small></div>{!!node.alerts?.length && <Notice error>当前告警：{node.alerts.map(alert => ({ cpu_percent: 'CPU 使用率', memory_percent: '内存使用率', disk_percent: '磁盘使用率', services: '服务健康' }[alert] || alert)).join('、')}</Notice>}<div className="nz-monitor-metrics">{metrics.map(value => <Panel key={value.title}><value.icon size={21} /><span>{value.title}</span><strong>{value.value}</strong><small>{value.detail}</small></Panel>)}</div><div className="nz-monitor-charts"><MetricTrend samples={monitoring.data?.samples || []} field="cpu_percent" title="CPU 趋势" /><MetricTrend samples={monitoring.data?.samples || []} field="memory_percent" title="内存趋势" /></div><div className="nz-admin-split"><Panel title="磁盘状态"><HardDrive size={22} />{node.disks?.length ? <Details value={node.disks} /> : <p className="nz-muted">等待磁盘采样。</p>}</Panel><Panel title="服务健康"><Activity size={22} />{node.services && Object.keys(node.services).length ? <Details value={node.services} /> : <p className="nz-muted">等待服务健康采样。</p>}</Panel></div><p className="nz-form-hint">原始采样保留 {monitoring.data?.retention.raw_days ?? 7} 天，聚合采样保留 {monitoring.data?.retention.aggregate_days ?? 30} 天。</p>{owner && <BusinessForm title="调整告警阈值" fields={[{ name: 'cpu', label: 'CPU 告警阈值（%）', type: 'number' }, { name: 'memory', label: '内存告警阈值（%）', type: 'number' }, { name: 'disk', label: '磁盘告警阈值（%）', type: 'number', optional: true }, { name: 'link', label: '链路带宽（Mbps，选填）', type: 'number', optional: true }, { name: 'reason', label: '调整原因' }]} report={report} submit={(values, meta) => submitCommand(api, 'monitor.node.update', { node_id: node.id, enabled: node.enabled ?? true, expected_revision: node.revision ?? 1, thresholds: { ...(node.thresholds || {}), cpu_percent: Number(values.cpu), memory_percent: Number(values.memory), ...(values.disk ? { disk_percent: Number(values.disk) } : {}), ...(values.link ? { link_mbps: Number(values.link) } : {}) } }, meta, monitoring.refresh)} />}</> : !monitoring.loading && !monitoring.error ? <Empty title="还没有接入监控节点" icon={<Server size={38} />}>登记节点并启动采集器后，CPU、内存、带宽和服务健康将在这里展示。</Empty> : <p className="nz-loading">正在读取监控节点…</p>}</div></div>{owner && <Panel title="登记监控节点"><BusinessForm title="登记节点" fields={[{ name: 'name', label: '节点名称' }, { name: 'platform', label: '系统类型', options: [['linux', 'Linux'], ['windows', 'Windows']] }, { name: 'reason', label: '登记原因' }]} report={enrolled} submit={(values, meta) => submitCommand(api, 'monitor.node.enroll', { name: values.name, platform: values.platform }, meta, monitoring.refresh)} /><p className="nz-form-hint">节点令牌仅首次返回。请在受限位置配置采集器，不要分享截图或复制到公开文档。</p>{enrollment && <div className="nz-node-token"><Label htmlFor="node-token">本次节点令牌</Label><Input id="node-token" type="password" readOnly value={enrollment.node_token} autoComplete="off" /><div className="nz-inline-actions"><Button variant="outline" onClick={() => void copyToken()}>{copied ? '已复制' : '复制令牌'}</Button><Button variant="ghost" onClick={() => setEnrollment(null)}>已保存，收起</Button></div><p className="nz-form-hint">离开本页后不再显示。将令牌用于受信节点的采集器配置。</p></div>}</Panel>}</section>
}
