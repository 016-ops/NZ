/** 运营摘要只展示服务返回的真实计数；空库明确为 0，客户端未接入能力不显示为在线。 */
import { Activity, ArrowUpRight, Clock3, DoorOpen, Radio, UsersRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { type Data } from '@/lib/api'

export function Overview({ data, invite }: { data: Data | null; invite?: () => void }) {
  const metrics = [
    { key: 'accounts', label: '注册账号', detail: '独立本地账号', icon: UsersRound },
    { key: 'active_sessions', label: '活跃会话', detail: '当前未过期会话', icon: Radio },
    { key: 'rooms', label: '开放房间', detail: '本地房间控制状态', icon: DoorOpen },
    { key: 'pending_sync', label: '待同步事件', detail: '等待客户端确认', icon: Clock3 },
  ]
  return <div className="overview">
    <div className="metric-strip">{metrics.map(({ key, label, detail, icon: Icon }) => <div className="metric" key={key}><div className="metric-label"><span>{label}</span><Icon size={18} /></div><strong className="metric-value">{typeof data?.[key] === 'number' ? data[key].toLocaleString('zh-CN') : '待读取'}</strong><span className="metric-detail">{detail}</span></div>)}</div>
    <div className="overview-grid"><section className="start-panel"><span className="section-caption">账号工作流</span><h3>从一份邀请开始</h3><p>创建邀请，审核玩家账号，再开放本地角色、资源和房间功能。</p><div className="workflow"><span>生成邀请</span><span>审核账号</span><span>初始化角色</span></div>{invite && <Button onClick={invite}>管理邀请<ArrowUpRight size={16} /></Button>}</section>
      <section className="capability-panel"><h3><Activity size={18} />运行边界</h3><div><span>本地控制面</span><span className="status-chip positive">可用</span></div>{['原客户端登录', '真实多人战斗', '游戏内资源同步'].map(label => <div key={label}><span>{label}</span><span className="status-chip muted">未接入</span></div>)}<p>当前工作台管理本地业务数据。</p></section>
    </div>
  </div>
}
