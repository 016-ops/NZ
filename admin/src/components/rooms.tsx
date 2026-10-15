/** 房间控制面表单：实际目录选择、访问规则、定向邀请和分页过滤，不伪装游戏画面。 */
import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BusinessForm, ActionButton, Details, type Field, type Values } from '@/components/business'
import { Api, type Action, type Catalog, type Data, type Json, type Metadata, type Room, type Session } from '@/lib/api'

type Props = { api: Api; rooms: Room[]; session: Session; report: (value: unknown) => void; command: (action: Action, values: Data, meta: Metadata) => Promise<Json> }

/** 清除空白选填字段，保持后端“不提供即保留”的配置语义，密码只出现在当前请求。 */
function options(values: Values): Data {
  const result: Data = { invite_only: values.invite_only === 'true' }
  for (const key of ['map_key', 'difficulty', 'channel', 'password']) if (values[key]) result[key] = values[key]
  if (values.clear_password === 'true') result.password = ''
  return result
}

/** 从服务目录获取允许地图；无已验证目录时拒绝建房，只显示实际状态。 */
export function RoomWorkspace({ api, rooms, session, report, command }: Props) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [filtered, setFiltered] = useState<Room[] | null>(null)
  useEffect(() => { let active = true; api.request<Catalog>('/api/views/catalog').then(value => { if (active) setCatalog(value) }).catch(error => report(error.message)); return () => { active = false } }, [api, report])
  useEffect(() => { setFiltered(null) }, [rooms])
  const maps: [string, string][] = Object.keys(catalog?.maps || {}).map(key => [key, `${key}${catalog?.id.startsWith('test-') ? ' / 合成测试' : ''}`])
  const common: Field[] = [{ name: 'difficulty', label: '难度 key（留空使用默认值）', optional: true }, { name: 'channel', label: '频道 key（留空使用默认值）', optional: true }, { name: 'password', label: '房间口令（选填，至少 12 字符）', type: 'password', optional: true }]
  return <div className="space-y-5"><BusinessForm title="筛选房间" fields={[{ name: 'map_key', label: '地图 key（留空为全部）', optional: true }, { name: 'channel', label: '频道（选填）', optional: true }, { name: 'difficulty', label: '难度（选填）', optional: true }, { name: 'offset', label: '跳过数量（选填）', type: 'number', optional: true }]} report={report} submit={async values => {
    const filters = new URLSearchParams(Object.entries(values).filter(([, value]) => value !== ''))
    const next = await api.request<Room[]>(`/api/views/rooms?${filters}`); setFiltered(next); return `读取 ${next.length} 个房间`
  }} />
  {session.kind === 'player' && (maps.length > 0 ? <BusinessForm title="创建房间" fields={[{ name: 'map_key', label: '当前环境允许的地图', options: maps }, ...common, { name: 'invite_only', label: '访问方式', options: [['false', '公开 / 可选口令'], ['true', '仅定向邀请']] }]} report={report} submit={(values, meta) => command('room.create', options(values), meta)} /> : <p>当前运行目录没有已验证可用地图，建房入口未开放。</p>)}
  {(filtered || rooms).map(room => {
    const owner = session.kind === 'player' && room.owner === session.subject_id
    const member = room.members?.some(value => value.account_id === session.subject_id)
    return <Card key={room.id}><CardHeader><CardTitle>{room.map_key} · {room.state}</CardTitle></CardHeader><CardContent className="space-y-4"><Details value={room} />{session.kind === 'player' && <>
      {!member && room.state === 'OPEN' && <BusinessForm title="加入房间" fields={[{ name: 'password', label: '房间口令（如需要）', type: 'password', optional: true }, { name: 'invitation_token', label: '发送给自己的邀请令牌（如需要）', optional: true }]} report={report} submit={(values, meta) => command('room.join', { ...values, room_id: room.id }, meta)} />}
      {member && <div className="flex flex-wrap gap-2">{room.state === 'OPEN' && [true, false].map(value => <ActionButton key={String(value)} report={report} action={meta => command('room.ready', { room_id: room.id, ready: value }, meta)}>{value ? '准备' : '取消准备'}</ActionButton>)}<ActionButton confirm="退出将失去当前局奖励资格，确认退出？" report={report} action={meta => command('room.leave', { room_id: room.id }, meta)}>退出房间</ActionButton></div>}
      {owner && room.state === 'OPEN' && <><ActionButton report={report} action={meta => command('room.start', { room_id: room.id, expected_revision: room.revision }, meta)}>申请开始</ActionButton><details><summary>房主配置与邀请</summary><div className="grid gap-4 pt-4 lg:grid-cols-2">
        <BusinessForm key={room.revision} title="保存房间配置" fields={[{ name: 'map_key', label: '地图', options: [...maps.filter(([key]) => key === room.map_key), ...maps.filter(([key]) => key !== room.map_key)] }, ...common, { name: 'clear_password', label: '现有口令', options: [['false', '保留 / 使用新口令'], ['true', '明确清除口令']] }, { name: 'invite_only', label: '访问方式', options: room.invite_only ? [['true', '仅定向邀请'], ['false', '公开 / 可选口令']] : [['false', '公开 / 可选口令'], ['true', '仅定向邀请']] }]} report={report} submit={(values, meta) => command('room.configure', { ...options(values), room_id: room.id, expected_revision: room.revision }, meta)} />
        <BusinessForm title="邀请指定账号" fields={[{ name: 'target_account_id', label: '被邀请账号 ID' }]} report={report} submit={(values, meta) => command('room.invite', { ...values, room_id: room.id, expires_at: Math.floor(Date.now() / 1000) + 600 }, meta)} />
      </div></details></>}
      {owner && ['ALLOCATING', 'LOADING'].includes(room.state) && <ActionButton report={report} action={meta => command('room.cancel', { room_id: room.id }, meta)}>取消分配</ActionButton>}
    </>}</CardContent></Card>
  })}</div>
}
