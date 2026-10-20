/** NZ 门户与玩家/管理员双入口；共享内存认证与内容页面，不把会话写入 Cookie、URL 或浏览器持久存储。 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type MouseEvent, type ReactNode } from 'react'
import { Activity, ArrowDown, ArrowRight, Bell, Check, ChevronRight, Compass, Download, Gamepad2, Gift, Home, Layers3, LogOut, Menu, Package, ShieldCheck, ShoppingBag, Swords, Trophy, UserRound, Users, X } from 'lucide-react'
import { Api, type Action, type Data, type Role, type Session } from '@/lib/api'
import { useResource, type PortalInfo, type RuntimeApi } from '@/lib/platform'
import { Button } from '@/components/ui/button'
import { EmailAccess } from '@/components/email-access'
import { Details } from '@/components/business'
import { CampaignsPage, Empty, InventoryPage, LeaderboardsPage, Notice, PageHeading, Panel, PlayerShopPage } from '@/components/platform-pages'
import { OperationsCenter, operationsLabels, type OperationsView } from '@/components/operations-center'

type PublicPage = 'home' | 'guide' | 'player' | 'admin' | 'activities' | 'leaderboards'
type PlayerView = 'overview' | 'inventory' | 'shop' | 'activities' | 'leaderboards'
const pagePaths: Record<PublicPage, string> = { home: '/', guide: '/guide', player: '/player', admin: '/admin', activities: '/activities', leaderboards: '/leaderboards' }
const pageTitles: Record<PublicPage, string> = { home: '首页', guide: '上手指南', player: '玩家中心', admin: '管理中心', activities: '开服活动', leaderboards: '先锋榜' }
const Navigation = createContext<(page: PublicPage) => void>(() => undefined)

/** 顶层路由白名单；已有 /console 和 /launcher 由 main.tsx 单独分发。 */
function readPage(): PublicPage {
  const path = window.location.pathname.replace(/\/$/, '') || '/'
  return (Object.entries(pagePaths).find(([, value]) => value === path)?.[0] as PublicPage | undefined) || 'home'
}

/** 同源页内导航保留当前内存会话；辅助点击与新标签页仍遵循浏览器标准行为。 */
function PageLink({ page, children, className = '', onClick }: { page: PublicPage; children: ReactNode; className?: string; onClick?: () => void }) {
  const navigate = useContext(Navigation)
  function click(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault(); navigate(page); onClick?.()
  }
  return <a href={pagePaths[page]} className={className} onClick={click}>{children}</a>
}

/** 门户外壳负责认证、公开目录与页面切换；业务写入只在用户操作中发起。 */
export function PortalApp() {
  const [page, setPage] = useState<PublicPage>(readPage)
  const [session, setSession] = useState<Session | null>(null)
  const [identity, setIdentity] = useState('')
  const [role, setRole] = useState<Role>('player')
  const [playerView, setPlayerView] = useState<PlayerView>('overview')
  const [adminView, setAdminView] = useState<OperationsView>('overview')
  const [feedback, setFeedback] = useState<string | null>(null)
  const [mobileMenu, setMobileMenu] = useState(false)
  /** 退出与过期同时清除身份及提示，已加载子页面随会话变更卸载。 */
  const expire = useCallback(() => { setSession(null); setIdentity(''); setRole('player'); setFeedback(null) }, [])
  const rawApi = useMemo(() => new Api(session, expire), [session, expire])
  // 静态动作来自本应用固定组件；Api 继续负责请求冻结、鉴权和幂等，桌面采用另一受限传输实现。
  const api = useMemo<RuntimeApi>(() => ({ request: rawApi.request.bind(rawApi), command: (action, data, metadata) => rawApi.command(action as Action, data, metadata) }), [rawApi])
  const publicApi = useMemo<RuntimeApi>(() => { const client = new Api(null, () => undefined); return { request: client.request.bind(client), command: (action, data, metadata) => client.command(action as Action, data, metadata) } }, [])
  const portal = useResource<PortalInfo>(publicApi, '/api/public/portal')
  /** 切页只改变公开路径，不把业务凭据放进地址；前进后退恢复页面。 */
  const navigate = useCallback((next: PublicPage) => { window.history.pushState(null, '', pagePaths[next]); setPage(next); setMobileMenu(false); setFeedback(null); window.scrollTo({ top: 0, behavior: 'instant' }) }, [])
  useEffect(() => { const sync = () => setPage(readPage()); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync) }, [])
  useEffect(() => { document.title = `${pageTitles[page]} · NZ Revival`; setMobileMenu(false) }, [page])
  useEffect(() => { const dark = document.documentElement.classList.contains('dark'); document.documentElement.classList.add('dark'); return () => { if (!dark) document.documentElement.classList.remove('dark') } }, [])
  /** 登录后先读取本人权限，再挂载工作区；玩家与管理员使用独立认证入口和服务端身份。 */
  async function signedIn(next: Session, label: string) {
    const me = await new Api(next, expire).request<{ role?: Role }>('/api/views/me')
    setIdentity(label); setSession(next); setRole(me.role || 'player'); setFeedback(null)
  }
  async function logout() { try { await rawApi.request('/api/logout', {}) } finally { expire() } }
  const playerLoggedIn = session?.kind === 'player'
  const privatePage = page === 'player' || page === 'admin'
  return <Navigation.Provider value={navigate}><div className={`nz-app ${privatePage ? 'nz-private-app' : ''}`}>
    <header className="nz-site-header"><div className="nz-header-inner"><PageLink page="home" className="nz-brand"><span className="nz-brand-symbol">N<span>Z</span></span><span><strong>逆战新生</strong><small>NZ Revival</small></span></PageLink><nav className={mobileMenu ? 'is-open' : ''} aria-label="官网导航">{(['home', 'activities', 'leaderboards', 'guide'] as PublicPage[]).map(item => <PageLink key={item} page={item} className={page === item ? 'is-active' : ''}>{pageTitles[item]}</PageLink>)}</nav><div className="nz-header-actions">{session ? <><span className="nz-header-identity">{identity}</span><Button variant="ghost" size="icon" aria-label="退出当前账号" onClick={() => void logout()}><LogOut size={17} /></Button></> : null}<PageLink page={session?.kind === 'admin' ? 'admin' : 'player'} className="nz-button nz-button-small"><UserRound size={15} />{session?.kind === 'admin' ? '管理中心' : playerLoggedIn ? '玩家中心' : '登录 / 注册'}</PageLink><button className="nz-mobile-menu" aria-label={mobileMenu ? '关闭导航' : '展开导航'} aria-expanded={mobileMenu} onClick={() => setMobileMenu(value => !value)}>{mobileMenu ? <X size={22} /> : <Menu size={22} />}</button></div></div></header>
    <main>{feedback && <div className="nz-content"><Notice error>{feedback}</Notice></div>}
      {page === 'home' ? <PortalHome portal={portal.data} loading={portal.loading} error={portal.error} /> : page === 'guide' ? <GuidePage /> : page === 'activities' ? <div className="nz-content"><CampaignsPage api={playerLoggedIn ? api : publicApi} authenticated={!!playerLoggedIn} onLogin={() => navigate('player')} /></div> : page === 'leaderboards' ? <div className="nz-content"><LeaderboardsPage api={playerLoggedIn ? api : publicApi} authenticated={!!playerLoggedIn} maps={portal.data?.maps || []} /></div> : !session ? <AuthPage key={page} kind={page === 'admin' ? 'admin' : 'player'} api={rawApi} signedIn={signedIn} report={value => setFeedback(typeof value === 'string' ? value : JSON.stringify(value))} /> : session.kind !== (page === 'admin' ? 'admin' : 'player') ? <div className="nz-content nz-auth-mismatch"><Empty title="此入口需要另一种账号身份" icon={<ShieldCheck size={36} />}>请退出当前账号后，使用{page === 'admin' ? '管理员' : '玩家'}入口登录。</Empty><Button onClick={() => void logout()}>退出当前账号</Button></div> : page === 'admin' ? <div className="nz-content nz-admin-content"><OperationsCenter api={api} view={adminView} setView={setAdminView} role={role} /></div> : <PlayerWorkspace api={api} portal={portal.data} identity={identity} view={playerView} setView={setPlayerView} />}
    </main>
    <footer className="nz-site-footer"><div><PageLink page="home" className="nz-footer-brand">NZ Revival</PageLink><p>再一次，与战友并肩。</p></div><div><PageLink page="guide">上手指南</PageLink><PageLink page="admin">管理入口</PageLink><a href="/console">完整工作台</a></div><span>独立社区 · 开发测试阶段</span></footer>
  </div></Navigation.Provider>
}

/** 首页用原创主视觉承载主题，地图与状态读取公开目录；错误时不展示虚构在线人数。 */
function PortalHome({ portal, loading, error }: { portal: PortalInfo | null; loading: boolean; error: string | null }) {
  const hunt = portal?.maps.filter(map => !/tower|defen|塔防/i.test(map.kind)) || []
  const defense = portal?.maps.filter(map => /tower|defen|塔防/i.test(map.kind)) || []
  return <><section className="nz-hero"><div className="nz-hero-art" /><div className="nz-hero-vignette" /><div className="nz-hero-inner"><div className="nz-hero-topline"><span className="nz-live-dot" />战友集结中<span className="nz-hero-rule" />合作闯关 / 全新起点</div><h1>这一战，<br />我们再并肩。</h1><p>重回熟悉的战场，找回并肩的默契。<br />从僵尸猎场到塔防前线，下一份纪录由你们写下。</p><div className="nz-hero-actions"><PageLink page="player" className="nz-button"><Gamepad2 size={20} />进入玩家中心<ArrowRight size={18} /></PageLink><PageLink page="guide" className="nz-text-link">获取 Launcher <ChevronRight size={16} /></PageLink></div><div className="nz-hero-footnote"><ShieldCheck size={14} />统一账号登录 · 道具与奖励有迹可循</div></div><a href="#open-maps" className="nz-hero-scroll" aria-label="查看开放地图"><ArrowDown size={18} /></a></section>
    <div className="nz-signal-bar"><span><i className={portal ? 'is-online' : ''} />{loading ? '连接平台中' : portal ? portal.test_profile ? '合成测试环境' : '平台服务已连接' : '平台连接暂不可用'}</span><span>游戏入口 <strong>Windows Launcher</strong></span><span>实玩验收 <strong>分阶段进行</strong></span><PageLink page="guide">查看加入流程 <ArrowRight size={14} /></PageLink></div>
    <div className="nz-content"><section className="nz-home-events"><div className="nz-home-section-heading"><div><span>属于战友的回归礼</span><h2>每一次出发，都有回响。</h2></div><PageLink page="activities" className="nz-text-link">全部活动 <ArrowRight size={16} /></PageLink></div><div className="nz-home-event-grid"><PageLink page="activities" className="nz-feature-event"><div><span className="nz-tag nz-tag-gold"><Gift size={13} />开服集结</span><h3>签到赴约，<br />领走你的战备礼。</h3><p>登录、签到、时长、通关与排行奖励<br />以本期已发布活动为准</p><span className="nz-text-link">查看奖励 <ArrowRight size={17} /></span></div><div className="nz-event-object" aria-hidden="true"><Gift size={88} strokeWidth={1} /><span /></div></PageLink><PageLink page="leaderboards" className="nz-feature-ranking"><Trophy size={41} strokeWidth={1.2} /><h3>下一位先锋，<br />也许就是你们。</h3><p>分地图、分人数、分难度的小队竞速榜。</p><span className="nz-text-link">进入先锋榜 <ArrowRight size={17} /></span></PageLink></div></section>
      <section className="nz-home-maps" id="open-maps"><div className="nz-home-section-heading"><div><span>本期开放战场</span><h2>熟悉的挑战，新的纪录。</h2></div><span className="nz-tag"><Compass size={13} />以当前开放策略为准</span></div>{error && <Notice error>{error}</Notice>}{!loading && !portal?.maps.length && !error && <Empty title="开放地图正在准备">管理员完成配置后，这里会同步显示当前可游玩的地图。</Empty>}
      <div className="nz-map-groups">{[{ title: '僵尸猎场', description: '1–8 人合作挑战 · 任务成功后参与竞速', maps: hunt, icon: Swords, className: 'nz-hunt-maps' }, { title: '塔防战', description: '1–4 人防线协作 · 击杀 Boss 并完成结算', maps: defense, icon: ShieldCheck, className: 'nz-defense-maps' }].filter(group => group.maps.length).map(group => <article className={`nz-map-group ${group.className}`} key={group.title}><div className="nz-map-group-top"><group.icon size={28} strokeWidth={1.25} /><div><h3>{group.title}</h3><p>{group.description}</p></div></div><div className="nz-map-list">{group.maps.map(map => <div key={map.map_id}><span className="nz-map-marker"><MapGlyph defense={group.title === '塔防战'} /></span><div><h4>{map.name}</h4><span>{map.submodes.length ? `${map.submodes.length} 种开放难度` : '难度待配置'}</span></div><span className="nz-map-id">{map.map_id}</span></div>)}</div></article>)}</div></section>
      <section className="nz-join-callout"><div><span className="nz-small-symbol"><Users size={28} /></span><h2>战友的位置，给你留着。</h2><p>创建账号，完成验证，从 Launcher 开始下一次冒险。</p></div><PageLink page="guide" className="nz-button nz-button-light">查看加入指南 <ArrowRight size={17} /></PageLink></section>
    </div></>
}

/** 地图缩略符号使用可访问的装饰几何，不冒充尚未取得的实际地图截图。 */
function MapGlyph({ defense }: { defense: boolean }) { return defense ? <ShieldCheck size={22} strokeWidth={1.25} /> : <Swords size={22} strokeWidth={1.25} /> }

/** 认证屏按入口展示专用表单；管理员 MFA 和玩家邮箱认证始终分别执行。 */
function AuthPage({ kind, api, signedIn, report }: { kind: 'admin' | 'player'; api: Api; signedIn: (value: Session, identity: string) => Promise<void>; report: (value: unknown) => void }) {
  return <div className="nz-auth-page"><section className="nz-auth-scene"><div className="nz-auth-scene-art" /><div><span className="nz-brand-symbol">NZ</span><h1>{kind === 'admin' ? <>秩序就位，<br />战场就绪。</> : <>战友，<br />欢迎归队。</>}</h1><p>{kind === 'admin' ? '使用管理员账号与动态验证码，管理玩家、资产和每一期挑战。' : '登录独立账号，查看仓库与活动进度。所有游戏登录从 Launcher 开始。'}</p><PageLink page="guide" className="nz-text-link">{kind === 'admin' ? '查看平台使用指南' : '第一次加入？查看上手指南'}<ArrowRight size={16} /></PageLink></div></section><section className="nz-auth-form"><span className="nz-auth-label">{kind === 'admin' ? <ShieldCheck size={20} /> : <UserRound size={20} />}{kind === 'admin' ? '管理员登录' : '玩家账号'}</span><EmailAccess only={kind} api={api} report={report} signedIn={signedIn} /><div className="nz-auth-footnote"><ShieldCheck size={14} />登录凭据仅在当前会话中保存</div></section></div>
}

/** 玩家中心采用 Launcher 同源布局：导航轨、顶栏与中央内容舞台，移动端转换为横向导航。 */
function PlayerWorkspace({ api, portal, identity, view, setView }: { api: RuntimeApi; portal: PortalInfo | null; identity: string; view: PlayerView; setView: (view: PlayerView) => void }) {
  const navigation: { view: PlayerView; label: string; icon: typeof Home }[] = [{ view: 'overview', label: '玩家首页', icon: Home }, { view: 'inventory', label: '我的仓库', icon: Package }, { view: 'shop', label: '战备商城', icon: ShoppingBag }, { view: 'activities', label: '开服活动', icon: Gift }, { view: 'leaderboards', label: '先锋榜', icon: Trophy }]
  return <div className="nz-player-shell"><aside className="nz-player-rail"><span className="nz-rail-caption">我的战备</span><nav aria-label="玩家中心导航">{navigation.map(item => <button key={item.view} className={view === item.view ? 'is-active' : ''} onClick={() => setView(item.view)}><item.icon size={21} /><span>{item.label}</span>{view === item.view && <ChevronRight size={14} />}</button>)}</nav><div className="nz-rail-account"><span className="nz-avatar">{identity.slice(0, 1).toUpperCase()}</span><span>已验证会话<small>玩家账号</small></span></div></aside><div className="nz-player-main"><header className="nz-workspace-header"><span>{navigation.find(item => item.view === view)?.label}</span><PageLink page="guide" className="nz-text-link"><Download size={15} />Launcher 指南</PageLink></header>{view === 'inventory' ? <InventoryPage api={api} /> : view === 'shop' ? <PlayerShopPage api={api} /> : view === 'activities' ? <CampaignsPage api={api} authenticated /> : view === 'leaderboards' ? <LeaderboardsPage api={api} authenticated maps={portal?.maps || []} /> : <PlayerOverview api={api} setView={setView} />}</div></div>
}

/** 角色资料直接读取本人视图，军衔缺失时如实待接入，不按经验或通关数自行计算等级。 */
function PlayerOverview({ api, setView }: { api: RuntimeApi; setView: (view: PlayerView) => void }) {
  const me = useResource<Data>(api, '/api/views/me')
  const character = me.data?.character as Data | null
  return <section className="nz-page"><div className="nz-player-welcome"><div><span className="nz-tag"><Gamepad2 size={13} />战备就绪</span><h1>欢迎归队，{String(character?.nickname || me.data?.nickname || '战友')}。</h1><p>整备装备，领取补给，下一次挑战即将开始。</p><Button onClick={() => setView('activities')}>查看开服奖励 <Gift size={16} /></Button></div></div>{me.error && <Notice error>{me.error}</Notice>}<div className="nz-player-summary"><Panel><UserRound size={24} /><span>角色昵称</span><strong>{String(character?.nickname || '尚未创建')}</strong></Panel><Panel><AwardGlyph /><span>军衔等级</span><strong>{me.data?.level == null ? '待接入' : String(me.data.level)}</strong></Panel><Panel><ShieldCheck size={24} /><span>账号状态</span><strong>{me.data?.status === 'ACTIVE' ? '正常' : String(me.data?.status || '读取中')}</strong></Panel></div><div className="nz-player-quick-grid">{[{ view: 'inventory', title: '整理我的仓库', copy: '查看已拥有的道具和使用期限。', icon: Package }, { view: 'shop', title: '补充战备装备', copy: '浏览当前上架商品，核对报价后购买。', icon: ShoppingBag }, { view: 'leaderboards', title: '看看先锋纪录', copy: '与同样人数的小队比较通关时间。', icon: Trophy }].map(item => <button key={item.view} onClick={() => setView(item.view as PlayerView)}><item.icon size={28} strokeWidth={1.4} /><h3>{item.title}</h3><p>{item.copy}</p><ArrowRight size={17} /></button>)}</div><Panel title="游戏从 Launcher 开始"><div className="nz-launcher-callout"><Gamepad2 size={31} /><p>网页用于管理账号、查看仓库和领取奖励。Windows Launcher 完成环境检查与游戏登录，启动器登录天数仅通过该入口记录。</p><PageLink page="guide" className="nz-text-link">使用指南 <ArrowRight size={15} /></PageLink></div></Panel>{me.data && <details className="nz-details"><summary>查看账号详情</summary><Details value={me.data} /></details>}</section>
}

/** 轻量军衔符号仅用于字段标题，不代表玩家已获得任何实际等级。 */
function AwardGlyph() { return <Layers3 size={24} /> }

/** 引导只说明当前真实流程；游戏/安装包能力采用待验收描述，避免把本机验证扩大到所有玩家。 */
function GuidePage() {
  const steps = [
    { title: '创建独立账号', copy: '准备邀请信息，使用邮箱验证码完成注册。需要审核的账号，由管理员批准后登录。', icon: UserRound },
    { title: '完成角色准备', copy: '在玩家中心检查角色资料、仓库和账号状态。首次角色初始化等完整业务可通过完整工作台操作。', icon: Package },
    { title: '使用 Windows Launcher', copy: '从管理员提供的受信发布渠道获取 Launcher。安装包、客户端版本和独立机器运行范围以对应发布说明为准。', icon: Download },
    { title: '登录并加入战场', copy: '在 Launcher 内登录，等待客户端检查并启动游戏。遇到异常保留诊断编号，正常退出后等待本轮清理完成。', icon: Gamepad2 },
  ]
  return <div className="nz-content nz-guide-page"><PageHeading title="新的征程，从这里开始。" description="从账号准备到 Launcher 登录，按顺序完成四步。" /><div className="nz-guide-steps">{steps.map((step, index) => <article key={step.title}><span className="nz-step-index">{String(index + 1).padStart(2, '0')}</span><span className="nz-step-icon"><step.icon size={30} strokeWidth={1.3} /></span><div><h2>{step.title}</h2><p>{step.copy}</p></div><Check size={19} /></article>)}</div><div className="nz-guide-links"><PageLink page="player" className="nz-button">创建或登录账号 <ArrowRight size={17} /></PageLink><a href="/console" className="nz-text-link">打开完整工作台 <ArrowRight size={16} /></a></div><section className="nz-guide-faq"><h2>出发前，你可能想知道</h2>{[
    ['游戏必须从 Launcher 登录吗？', '是。统一从 Launcher 获取本次游戏凭据，完成环境检查、启动和退出清理。不要直接启动游戏程序或使用他人的客户端配置。'],
    ['为什么网页显示军衔“待接入”？', '当前尚未接入可信的原生军衔数据。平台不会用仓库、积分或场次虚构玩家等级。'],
    ['如何领取活动奖励？', '进入开服活动页，完成目标后点击领取。登录奖励按 Launcher 登录记录计算；排行奖励封榜后自动发放，每期只获得最高一档。'],
    ['目前可以游玩哪些地图和模式？', '首页展示当前开放目录。建房和开局由服务端校验，未开放的地图或模式会收到警告。真实玩法和多人通关按对应实机验收记录确认。'],
    ['Launcher 发生异常怎么办？', '保留界面上的诊断编号和错误码。退出清理未完成时，保持启动器运行，网络恢复后使用“检查并恢复”，不要手工覆盖游戏配置。'],
  ].map(([title, copy]) => <details key={title}><summary>{title}<PlusGlyph /></summary><p>{copy}</p></details>)}</section></div>
}

/** FAQ 的展开标识交给 CSS 随 open 状态旋转，不添加无意义常驻动画。 */
function PlusGlyph() { return <span aria-hidden="true">+</span> }
