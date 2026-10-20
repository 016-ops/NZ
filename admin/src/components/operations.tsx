/** 管理员、持久批次、复核与 PvE 观察入口；复用现有表单/确认组件及服务端权限。 */
import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ActionButton, BusinessForm, Details, Records, type Field } from '@/components/business'
import { Api, type Catalog, type Data, type Json } from '@/lib/api'

const reason: Field = { name: 'reason', label: '操作原因' }
// 邮箱再认证由 Api 的统一弹窗执行，业务表单不再收集或拼接动态口令。
const reauth: Field[] = []
const roles: [string, string][] = [['resources', '资源管理员'], ['users', '用户管理员'], ['auditor', '审计只读'], ['owner', 'Owner']]
type Props = { api: Api; records: Data[]; report: (value: unknown) => void; refresh: () => Promise<void>; owner: boolean }

/** 后端已经确认提交时，查询失败只提示刷新问题，禁止把已提交操作误判为失败重发。 */
async function committed(result: Json, refresh: () => Promise<void>, report: Props['report']) {
  try { await refresh() } catch { report('操作已提交，读取最新状态失败，请刷新。') }
  return result
}

/** 管理员生命周期要求 Owner 再认证；邮箱挑战只用于当前操作，不持久化到浏览器。 */
export function Administrators({ api, records, report, refresh }: Props) {
  return <div className="space-y-5"><p>管理员登录与高风险操作均使用密码和邮箱验证码。</p>
    <BusinessForm title="创建管理员" fields={[{ name: 'login', label: '管理员账号' }, { name: 'new_password', label: '独立密码（至少 12 字符）', type: 'password' }, { name: 'role', label: '角色', options: roles }, ...reauth, reason]} report={report} submit={async (values, meta) => committed(await api.command('admin.create', values, meta), refresh, report)} />
    {records.map(admin => <Card key={String(admin.id)}><CardHeader><CardTitle>{String(admin.login)}</CardTitle></CardHeader><CardContent className="space-y-4"><Details value={admin} /><details><summary>管理操作</summary><div className="grid gap-4 pt-4 lg:grid-cols-2">
      <BusinessForm title="调整角色与状态" fields={[{ name: 'role', label: '角色', options: [...roles.filter(([value]) => value === admin.role), ...roles.filter(([value]) => value !== admin.role)] }, { name: 'active', label: '账号状态', options: admin.active ? [['true', '启用'], ['false', '停用']] : [['false', '停用'], ['true', '启用']] }, ...reauth, reason]} report={report} submit={async (values, meta) => committed(await api.command('admin.update', { ...values, admin_id: admin.id, active: values.active === 'true' }, meta), refresh, report)} />
      <BusinessForm title="重置管理员密码" fields={[{ name: 'new_password', label: '新密码（至少 12 字符）', type: 'password' }, ...reauth, reason]} report={report} submit={async (values, meta) => committed(await api.command('admin.password.reset', { ...values, admin_id: admin.id }, meta), refresh, report)} />
    </div></details></CardContent></Card>)}
  </div>
}

/** 批次预览先冻结全部目标和变化，确认时仅补充再认证；部分失败只重试失败项。 */
export function Batches({ api, records, report, refresh, owner }: Props) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [preview, setPreview] = useState<{ data: Data; result: Json; reason: string } | null>(null)
  useEffect(() => { let active = true; api.request<Catalog>('/api/views/catalog').then(value => { if (active) setCatalog(value) }).catch(error => report(error.message)); return () => { active = false } }, [api, report])
  return <div className="space-y-5"><p>目标列表在创建时固定；维护进程逐目标执行，服务重启会继续未完成项。预览不保证提交时余额仍未变化。</p>
    {owner && catalog && Object.keys(catalog.currencies).length > 0 && <BusinessForm title="批量发放预览" fields={[{ name: 'targets', label: '目标账号 ID（逗号或空格分隔，最多 500 个）' }, { name: 'type', label: '资源类型', options: [['currency', '币种'], ['item', '物品']] }, { name: 'resource', label: '币种代码或物品 key' }, { name: 'amount', label: '增量 / 数量', type: 'number' }, reason]} report={report} submit={async (values, meta) => {
      const data: Data = { target_account_ids: values.targets.split(/[,，\s]+/).filter(Boolean), catalog_version: catalog.id, changes: [values.type === 'currency' ? { type: 'currency', code: values.resource, delta: Number(values.amount) } : { type: 'item', item_key: values.resource, quantity: Number(values.amount) }] }
      const result = await api.command('asset.batch.preview', data, meta)
      setPreview({ data, result, reason: meta.reason }); return '预览已生成，请核对目标和差额后确认。'
    }} />}
    {preview && <Card><CardHeader><CardTitle>冻结的批次预览</CardTitle></CardHeader><CardContent><Details value={preview.result} /><BusinessForm title="确认创建持久批次" fields={reauth} report={report} submit={async (values, meta) => { const result = await api.command('asset.batch.enqueue', { ...preview.data, ...values }, { ...meta, reason: preview.reason }); setPreview(null); return committed(result, refresh, report) }} /></CardContent></Card>}
    {records.map(batch => <Card key={String(batch.id)}><CardHeader><CardTitle>批次 {String(batch.id)} · {String(batch.status)}</CardTitle></CardHeader><CardContent><Details value={batch} />{owner && ['PARTIAL', 'REJECTED'].includes(String(batch.status)) && <BusinessForm title="仅重试此批次失败目标" fields={[...reauth, reason]} report={report} submit={async (values, meta) => committed(await api.command('asset.batch.retry', { ...values, batch_id: batch.id }, meta), refresh, report)} />}</CardContent></Card>)}
  </div>
}

/** 人工复核只追加结案决定；不提供删除历史流水、强制负余额或无来源回收入口。 */
export function Reviews({ api, records, report, refresh, owner }: Props) {
  return <div className="space-y-5"><p>结案仅记录处置决定，不自动改变资产。额外补偿需通过关联的新资产操作完成。</p>{records.map(review => <Card key={String(review.id)}><CardContent className="space-y-4 pt-6"><Details value={review} />{owner && !review.decision && <BusinessForm title="记录复核决定" fields={[{ name: 'resolution', label: '处置', options: [['KEEP_ASSETS', '保留现有资产'], ['EXTERNAL_RESOLUTION', '已另行完成处置（原因中说明依据）']] }, ...reauth, reason]} report={report} submit={async (values, meta) => committed(await api.command('asset.review.resolve', { ...values, operation_id: review.id }, meta), refresh, report)} />}</CardContent></Card>)}{records.length === 0 && <p>当前没有人工复核记录。</p>}</div>
}

/** PvE 页面仅观察合成规则和持久结果；玩家只能申请自己的续局，不能提交胜利或奖励。 */
export function PveRuns({ api, records, report, refresh, player }: Props & { player: boolean }) {
  const [items, setItems] = useState<Data[]>([])
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  useEffect(() => { let active = true; Promise.all([api.request<Catalog>('/api/views/catalog'), player ? api.request<Data>('/api/views/me') : Promise.resolve(null)]).then(([next, me]) => { if (active) { setCatalog(next); setItems(Array.isArray(me?.inventory) ? me.inventory as Data[] : []) } }).catch(error => report(error.message)); return () => { active = false } }, [api, player, report])
  return <div className="space-y-5"><p>TEST_PROFILE：这是合成规则诊断，未接入原客户端画面、碰撞或原生导航。</p>{records.map(run => {
    const state = run.state as Data
    const tokens = items.filter(item => item.item_key === 'test-token')
    return <Card key={String(run.match_id)}><CardHeader><CardTitle>{String(state.phase)} · {String(state.stage_id)}</CardTitle></CardHeader><CardContent className="space-y-4"><Records value={run} />{player && state.phase === 'FAILED' && tokens.length > 0 && catalog && <BusinessForm title="使用一个复活道具申请续局" fields={[{ name: 'instance_id', label: '自己的复活道具', options: tokens.map(item => [String(item.id), `${String(item.id)} / ${String(item.quantity)} 个`]) }, reason]} report={report} submit={async (values, meta) => committed(await api.command('pve.revive', { ...values, match_id: run.match_id, stage_id: state.stage_id, attempt_id: state.attempt_id, catalog_version: catalog.id }, meta), refresh, report)} />}</CardContent></Card>
  })}{records.length === 0 && <p>当前没有可见的 PvE 运行记录。</p>}</div>
}
