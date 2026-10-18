/** 完整本地控制台；保留旧业务入口，页面能力展示以服务端 profile 为准。 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, Boxes, ChevronRight, ClipboardList, DoorOpen, LayoutDashboard, LogOut, MailPlus, Moon, RefreshCw, ScrollText, Sun, UserRound, UsersRound, Wallet, type LucideIcon } from 'lucide-react'
import { EmailAccess } from '@/components/email-access'
import { NativeRules } from '@/components/native-rules'
import { Administrators, Batches, Reviews, PveRuns } from '@/components/operations'
import { RoomWorkspace } from '@/components/rooms'
import { Overview } from '@/components/overview'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { ActionButton, BusinessForm, Details, Records, type Values } from '@/components/business'
import { Api, type Action, type Catalog, type Data, type Json, type Metadata, type Role, type Room, type Session, type Status } from '@/lib/api'

type View = 'native-rules' | 'admins' | 'batches' | 'reviews' | 'pve-runs' | 'me' | 'users' | 'invitations' | 'resources' | 'audit' | 'outbox' | 'rooms' | 'catalog' | 'dashboard'
const labels: Record<View, string> = { 'native-rules': '活动奖励', admins: '管理员', batches: '批量任务', reviews: '人工复核', 'pve-runs': 'PvE 诊断', me: '我的账号', users: '用户管理', invitations: '邀请管理', resources: '资源中心', audit: '审计记录', outbox: '同步任务', rooms: '房间控制', catalog: '版本目录', dashboard: '运行状态' }
const icons: Record<View, LucideIcon> = { 'native-rules':Wallet, admins:UsersRound, batches:ClipboardList, reviews:ScrollText, 'pve-runs':Activity, me:UserRound, users:UsersRound, invitations:MailPlus, resources:Wallet, audit:ScrollText, outbox:ClipboardList, rooms:DoorOpen, catalog:Boxes, dashboard:LayoutDashboard }
const descriptions: Record<View,string> = { 'native-rules':'配置签到、碎片抽奖与宝物工坊奖励。', admins:'管理员角色、会话与 MFA 恢复。', batches:'冻结目标、预览并追踪持久发放任务。', reviews:'保留历史，通过明确决定处理待复核操作。', 'pve-runs':'观察合成规则执行与持久化结果。', me:'查看角色、钱包与装备，管理自己的本地账号。',users:'审核账号、调整状态，记录每一次管理操作。',invitations:'创建和管理邀请，让新的玩家加入本地服务。',resources:'先核对预览，再确认资源变更。',audit:'追踪账号、资产和房间操作的实际结果。',outbox:'查看已提交事件的同步与重试状态。',rooms:'查看房间状态、席位和对局分配。',catalog:'当前环境允许使用的地图、币种与物品。',dashboard:'账号、会话和本地业务的当前状态。' }
const reason = { name: 'reason', label: '操作原因' }
const hours = { name: 'hours', label: '有效小时（0 为永久）', type: 'number' }

/** 组织登录、受邀注册和按角色显示的工作空间；会话只保存在组件内存，退出或 401 立即清空。 */
export function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [role, setRole] = useState<Role>('player')
  const [status, setStatus] = useState<Status | null>(null)
  const [view, setView] = useState<View>('me')
  const [data, setData] = useState<unknown>(null)
  const [feedback, setFeedback] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [identity,setIdentity] = useState('')
  const [dark,setDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const activeView=useRef(view); activeView.current=view
  // 页面级主题一次切换，所有基础组件共享同一套语义颜色；只保存视觉状态，不保存凭据。
  useEffect(() => { document.documentElement.classList.toggle('dark',dark) },[dark])
  // 切页第一帧可能仍持有上一页对象；列表必须先检查实际形状，不能仅靠 TypeScript 断言。
  const records: Data[] = Array.isArray(data) ? data : []
  const rooms: Room[] = Array.isArray(data) ? data : []
  /** 清理过期身份和已加载业务数据，避免退出后继续显示前一个主体的记录。 */
  const expire = useCallback(() => { setSession(null); setData(null); setRole('player'); setFeedback(null) }, [])
  const api = useMemo(() => new Api(session, expire), [session, expire])
  /** 把操作结果或错误交给统一反馈区；不把反馈作为提交成功的持久化证据。 */
  const report = useCallback((value: unknown) => setFeedback(value), [])

  /** 环境读取无业务写入；StrictMode 重挂载及网络错误都不会创建重复记录。 */
  useEffect(() => { let active = true; api.request<Status>('/api/status').then(value => { if (active) setStatus(value) }).catch(error => { if (active) report(error.message) }); return () => { active = false } }, [api, report])
  /** 重新读取当前视图；慢响应只有在页面仍被选中时才能覆盖数据，资源表单自行管理查询。 */
  const refresh = useCallback(async () => {
    if (view !== 'resources') {
      const next=await api.request(`/api/views/${view}`)
      if (activeView.current===view) setData(next)
    }
  }, [api, view])
  /** 切页请求带取消标记，慢响应不能覆盖之后选中的页面。 */
  useEffect(() => {
    if (!session) return
    let active = true; setLoading(true); setData(null)
    if (view === 'resources') { setLoading(false); return }
    api.request(`/api/views/${view}`).then(value => { if (active) setData(value) }).catch(error => { if (active) report(error.message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api, session, view, report])

  /** 先提交业务命令再刷新读模型；刷新错误不能把已成功的写入误判成失败重试。 */
  const command = async (action: Action, values: Data, meta: Metadata) => {
    const result = await api.command(action, values, meta)
    // 已收到提交成功响应时，刷新失败属于查询失败；不能让用户把它当成写入失败重试。
    try { await refresh() } catch (error) { report(`操作已提交，刷新失败：${String(error)}`) }
    return result
  }
  const views: View[] = session?.kind === 'player' ? ['me', 'rooms', 'catalog', 'pve-runs'] : ['dashboard', 'users', ...(role === 'owner' ? ['admins' as View, 'native-rules' as View] : []), ...(role === 'owner' || role === 'auditor' ? ['batches' as View, 'reviews' as View, 'pve-runs' as View] : []), ...(role === 'owner' || role === 'users' ? ['invitations' as View] : []), ...(role === 'owner' || role === 'resources' ? ['resources' as View] : []), 'rooms', 'catalog', 'audit', ...(role === 'owner' || role === 'resources' || role === 'auditor' ? ['outbox' as View] : [])]
  const userWrites = role === 'owner' || role === 'users'
  // 错误与成功使用不同反馈层级；原始业务错误文本仍保留，不以成功色暗示请求已提交。
  const feedbackError=typeof feedback==='string' && /(^[A-Z_]+:|error:|failed|失败)/i.test(feedback)

  return <div className="console-app">
    <header className="console-header"><div className="header-inner"><div className="brand-lockup"><span className="brand-mark">NZ</span><div><p>NZ / REVIVAL</p><span>本地运营工作台</span></div></div><div className="header-actions"><span className="environment-tag"><Activity size={14} />{status?.test_profile ? '合成环境' : '本地环境'}</span><Button variant="ghost" size="icon" aria-label="切换明暗主题" onClick={() => setDark(value => !value)}>{dark ? <Sun size={18} /> : <Moon size={18} />}</Button>{session && <Button variant="ghost" onClick={async () => { try { await api.request('/api/logout', {}) } finally { expire() } }}><LogOut size={16} />退出</Button>}</div></div></header>
    <main className={`console-main ${session ? 'authenticated' : 'unauthenticated'}`}>
      {!session ? <div className="auth-layout"><section className="auth-intro"><span className="intro-caption">本地服务入口</span><h1>账号、资源与对局，<br />在同一个工作台。</h1><p>玩家使用邮箱与独立密码登录，管理员使用专属账号和动态验证码。</p><div className="auth-context"><span className="status-chip positive">{status ? '控制面可用' : '正在连接'}</span><span>{status?.test_profile ? 'TEST_PROFILE 合成测试环境' : '当前客户端研究环境'}</span></div><div className="auth-boundary"><span>客户端登录</span><span>多人战斗</span><span>游戏内同步</span><p>上述原客户端能力尚未接入。</p></div></section><section className="auth-form-panel"><EmailAccess api={api} report={report} signedIn={async (next, label) => {
          const me = await new Api(next, expire).request<{ role?: Role }>('/api/views/me')
          setIdentity(label); setSession(next); setRole(me.role || 'player'); setView(next.kind === 'admin' ? 'dashboard' : 'me'); setFeedback(null)
        }} />
      </section></div> : <div className="workspace-layout"><aside className="workspace-sidebar"><span className="sidebar-caption">工作空间</span><nav aria-label="控制台导航">{views.map(item => { const Icon=icons[item]; return <Button key={item} variant="ghost" aria-current={item===view?'page':undefined} className={`nav-item ${item===view?'active':''}`} onClick={() => { setData(null); setLoading(item !== 'resources'); setView(item) }}><Icon size={18} />{labels[item]}{item===view && <ChevronRight size={14} className="nav-chevron" />}</Button> })}</nav><div className="sidebar-identity"><span className="identity-avatar">{identity.charAt(0).toUpperCase()}</span><div><strong>{identity}</strong><span>{role==='owner'?'超级管理员':session.kind==='player'?'玩家':'管理员'}</span></div></div></aside><section className="workspace-content"><div className="environment-notice"><span className="status-chip muted">{status?.test_profile ? 'TEST_PROFILE' : '本地服务'}</span><p>当前数据属于本地控制面，原客户端登录、战斗和资源同步尚未接入。</p></div><div className="page-heading"><div><p className="breadcrumb">控制台 <ChevronRight size={12} /> {labels[view]}</p><h1>{labels[view]}</h1><p>{descriptions[view]}</p></div><ActionButton report={report} action={async () => { await refresh(); return '已刷新' }}><RefreshCw size={15} />刷新</ActionButton></div>
          {loading ? <div className="loading-state" role="status"><span />读取中…</div> : <>
            {view === 'native-rules' ? <NativeRules api={api} report={report} /> : view === 'admins' ? <Administrators api={api} records={records} report={report} refresh={refresh} owner={role === 'owner'} /> : view === 'batches' ? <Batches api={api} records={records} report={report} refresh={refresh} owner={role === 'owner'} /> : view === 'reviews' ? <Reviews api={api} records={records} report={report} refresh={refresh} owner={role === 'owner'} /> : view === 'pve-runs' ? <PveRuns api={api} records={records} report={report} refresh={refresh} owner={role === 'owner'} player={session.kind === 'player'} /> : view === 'dashboard' ? <Overview data={data as Data | null} invite={userWrites ? () => { setData(null); setLoading(true); setView('invitations') } : undefined} /> : view === 'resources' ? <Resources api={api} report={report} /> : view === 'users' ? <>
              <BusinessForm title="查询用户" fields={[{ name: 'search', label: '账号搜索', optional: true }]} report={report} submit={async values => { const found = await api.request(`/api/views/users?search=${encodeURIComponent(values.search)}`); setData(found); return '查询完成' }} />
              {records.map(user => <Card key={String(user.id)}><CardHeader><CardTitle>{String(user.email || user.login)}</CardTitle></CardHeader><CardContent className="space-y-4"><Details value={user} /><div className="flex flex-wrap gap-2">
                <ActionButton report={report} action={() => api.request(`/api/views/user?account_id=${encodeURIComponent(String(user.id))}`)}>详情</ActionButton>
                {userWrites && ([['批准', 'user.approve'], ['停用', 'user.disable'], ['激活', 'user.activate'], ['踢下线', 'session.revoke']] as [string, Action][]).map(([label, action]) => <ActionButton key={action} report={report} confirm={`确认对 ${user.login} 执行${label}？`} action={meta => command(action, { target_account_id: user.id }, meta)}>{label}</ActionButton>)}
              </div>{userWrites && <div className="grid gap-4 lg:grid-cols-2">
                <BusinessForm title="全服封禁" fields={[reason, hours]} report={report} submit={(values, meta) => command('sanction.create', { target_account_id: user.id, expires_at: Number(values.hours) === 0 ? null : Math.floor(Date.now() / 1000) + Number(values.hours) * 3600 }, meta)} />
                <BusinessForm title="撤销指定封禁" fields={[{ name: 'sanction_id', label: '封禁 ID' }, reason]} report={report} submit={(values, meta) => command('sanction.revoke', { sanction_id: values.sanction_id }, meta)} />
                <BusinessForm title="设置玩家邮箱" fields={[{ name: 'email', label: '邮箱地址', type: 'email' }, ...(role === 'owner' ? [{ name: 'current_password', label: '当前管理员密码', type: 'password' }, { name: 'otp_code', label: '新的动态验证码' }] : []), reason]} report={report} submit={(values, meta) => command('user.email.set', { ...values, target_account_id: user.id }, meta)} />
                <BusinessForm title="重置玩家凭据" fields={[{ name: 'new_password', label: '新密码', type: 'password' }, reason]} report={report} submit={(values, meta) => command('user.reset', { target_account_id: user.id, new_password: values.new_password }, meta)} />
              </div>}</CardContent></Card>)}
            </> : view === 'invitations' ? <>
              <BusinessForm title="生成邀请（令牌仅首次展示）" fields={[{ name: 'max_uses', label: '最多使用次数', type: 'number' }, { name: 'hours', label: '有效小时', type: 'number' }, reason]} report={report} submit={(values, meta) => command('invite.create', { max_uses: Number(values.max_uses), expires_at: Math.floor(Date.now() / 1000) + Number(values.hours) * 3600 }, meta)} />
              {records.map(invite => <Card key={String(invite.id)}><CardContent className="space-y-4 pt-6"><Details value={invite} />{!invite.revoked && <ActionButton report={report} confirm="确认撤销此邀请？" action={meta => command('invite.revoke', { invitation_id: invite.id }, meta)}>撤销邀请</ActionButton>}</CardContent></Card>)}
            </> : view === 'rooms' ? <RoomWorkspace api={api} rooms={rooms} session={session} report={report} command={command} /> : view === 'me' ? <>
              <Details value={data} />{data && !(data as Data).character ? <BusinessForm title="初始化本地角色" fields={[{ name: 'nickname', label: '昵称' }]} report={report} submit={(values, meta) => command('character.create', values, meta)} /> : status?.test_profile && <>
                <BusinessForm title="购买测试装备" fields={[{ name: 'offer_key', label: '商品', options: [['test-rifle-gp', '测试步枪 / 500 GP']] }]} report={report} submit={(values, meta) => command('shop.purchase', { ...values, catalog_version: status?.profile || '' }, meta)} />
                {((data as Data | null)?.inventory as Data[] | undefined)?.map(item => <Card key={String(item.id)}><CardContent className="space-y-3 pt-6"><Details value={item} />
                  {item.item_key === 'test-rifle' && <ActionButton report={report} action={meta => command('inventory.equip', { instance_id: item.id, slot: 'primary', catalog_version: status?.profile || '' }, meta)}>装备到下局快照</ActionButton>}
                  {item.item_key === 'test-token' && <ActionButton report={report} action={meta => command('inventory.consume', { instance_id: item.id, quantity: 1, catalog_version: status?.profile || '' }, meta)}>消耗一个测试道具</ActionButton>}
                </CardContent></Card>)}
              </>}
            </> : <Card><CardContent className="pt-6"><Records value={data} /></CardContent></Card>}
          </>}
        </section>
      </div>}
      {feedback !== null && <Card className={`feedback-panel ${feedbackError?'error':''}`} role="status" aria-live="polite"><CardHeader><CardTitle>{feedbackError?'操作未完成':'操作结果'}</CardTitle></CardHeader><CardContent><Details value={feedback} /></CardContent></Card>}
    </main><footer className="console-footer">NZ / REVIVAL · 独立本地服务</footer>
  </div>
}

/** 预览与提交分离：预览返回完整前后状态，确认时发送冻结 payload 和原幂等键。 */
function Resources({ api, report }: { api: Api; report: (value: unknown) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [users, setUsers] = useState<Data[]>([])
  const [preview, setPreview] = useState<{ data: Data; metadata: Metadata; result: Json } | null>(null)
  const [operations, setOperations] = useState<Json | null>(null)
  useEffect(() => {
    let active = true
    Promise.all([api.request<Catalog>('/api/views/catalog'), api.request<Data[]>('/api/views/users')]).then(([next, targets]) => { if (active) { setCatalog(next); setUsers(targets) } }).catch(error => report(error.message))
    return () => { active = false }
  }, [api, report])
  if (!catalog) return <p>读取资源目录…</p>
  if (!Object.keys(catalog.currencies).length) return <p>当前客户端资源目录未验证，资源写入默认拒绝。</p>
  if (!users.length) return <p>先邀请、批准用户并初始化本地角色。</p>
  const options: [string, string][] = users.map(user => [String(user.id), String(user.login)])
  return <div className="space-y-5">
    <BusinessForm title="发放预览" fields={[{ name: 'target_account_id', label: '目标', options }, { name: 'type', label: '资源类型', options: [['currency', '测试币种'], ['item', '测试物品']] }, { name: 'resource', label: '币种代码或测试物品 key' }, { name: 'amount', label: '增量 / 数量', type: 'number' }, reason]} report={report} submit={async (values, metadata) => {
      const changes: Data[] = [values.type === 'currency' ? { type: 'currency', code: values.resource, delta: Number(values.amount) } : { type: 'item', item_key: values.resource, quantity: Number(values.amount) }]
      const data = { target_account_id: values.target_account_id, catalog_version: catalog.id, changes }
      const result = await api.request('/api/asset-preview', { ...data, ...metadata })
      setPreview({ data, metadata, result }); return '预览已生成，请核对后确认'
    }} />
    {preview && <Card><CardHeader><CardTitle>提交前预览</CardTitle></CardHeader><CardContent className="space-y-4"><Details value={preview.result} />
      <ActionButton confirm="确认按此预览提交增量发放？后端将重新校验余额和库存。" report={report} action={async () => { const result = await api.command('asset.grant', preview.data, preview.metadata); setPreview(null); return result }}>确认本次增量操作</ActionButton>
    </CardContent></Card>}
    <BusinessForm title="查询流水" fields={[{ name: 'account_id', label: '目标', options }]} report={report} submit={async values => { setOperations(await api.request(`/api/views/operations?account_id=${encodeURIComponent(values.account_id)}`)); return '流水已读取' }} />
    {operations && <Records value={operations} />}
  </div>
}
