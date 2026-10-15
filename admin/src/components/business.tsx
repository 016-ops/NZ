/** 控制台业务组件：表单、确认与记录展示复用 shadcn/ui，不创建独立样式体系。 */
import { useId, useRef, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { RetryEnvelope, type Metadata } from '@/lib/api'

export interface Field { name: string; label: string; type?: string; optional?: boolean; options?: [string, string][] }
export type Values = Record<string, string>

/** 默认选中第一个选项；敏感输入不持久化，提交成功后重置整个表单。 */
export function BusinessForm({ title, fields, submit, report }: { title: string; fields: Field[]; submit: (values: Values, meta: Metadata) => Promise<unknown>; report: (value: unknown) => void }) {
  /** 为每个输入生成初始值，选择项采用首个允许值，其余置空；成功后也用该规则重置。 */
  const defaults = () => Object.fromEntries(fields.map(field => [field.name, field.options?.[0]?.[0] || '']))
  const [values, setValues] = useState<Values>(defaults)
  const [busy, setBusy] = useState(false)
  const retry = useRef(new RetryEnvelope())
  // 一页可能有多个用户操作表单，独立 ID 防止 Label 聚焦到另一个用户的输入框。
  const formId = useId()
  return <Card className="business-form"><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent>
    <form className="grid gap-4" onSubmit={async event => {
      event.preventDefault(); if (busy) return
      setBusy(true)
      try {
        const result = await submit(values, retry.current.prepare(values, values.reason || title))
        retry.current.success(); setValues(defaults()); if (result !== undefined) report(result)
      } catch (error) { report(error instanceof Error ? error.message : String(error)) }
      finally { setBusy(false) }
    }}>
      {fields.map(field => <div className="grid gap-2" key={field.name}><Label htmlFor={`${formId}-${field.name}`}>{field.label}{field.optional && <span className="optional-label">选填</span>}</Label>
        {field.options ? <Select name={field.name} value={values[field.name]} onValueChange={value => setValues(previous => ({ ...previous, [field.name]: value }))}>
          <SelectTrigger id={`${formId}-${field.name}`}><SelectValue /></SelectTrigger><SelectContent>{field.options.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
        </Select> : <Input id={`${formId}-${field.name}`} name={field.name} type={field.type || 'text'} required={!field.optional} value={values[field.name]} autoComplete={field.type === 'password' ? 'off' : undefined} onChange={event => setValues(previous => ({ ...previous, [field.name]: event.target.value }))} />}
      </div>)}
      <Button type="submit" className="form-submit" disabled={busy}>{busy ? '提交中…' : title === '登录' ? '登录工作台' : title === '受邀注册' ? '提交注册申请' : title === '查询用户' ? '查询用户' : title === '发放预览' ? '生成预览' : '提交'}</Button>
    </form>
  </CardContent></Card>
}

/** 按钮独立保存幂等键；确认框关闭不提交，服务确认成功后才清空重试状态。 */
export function ActionButton({ children, action, report, confirm }: { children: ReactNode; action: (meta: Metadata) => Promise<unknown>; report: (value: unknown) => void; confirm?: string }) {
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const retry = useRef(new RetryEnvelope())
  /** 禁止重复点击，失败保留幂等槽和确认框；收到成功结果后才关闭确认并重置重试状态。 */
  async function execute() {
    if (busy) return
    setBusy(true)
    try { const result = await action(retry.current.prepare({}, typeof children === 'string' ? children : '本地操作')); retry.current.success(); report(result); setOpen(false) }
    catch (error) { report(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <><Button variant="outline" disabled={busy} onClick={() => confirm ? setOpen(true) : void execute()}>{children}</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>确认操作</DialogTitle><DialogDescription>{confirm}</DialogDescription></DialogHeader>
      <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>取消</Button><Button disabled={busy} onClick={() => void execute()}>确认</Button></DialogFooter>
    </DialogContent></Dialog></>
}

/** 业务列名称集中定义；只改变展示文字，HTTP 字段、表单名称和权限语义保持不变。 */
const fieldLabels: Record<string, string> = {
  email:'邮箱', email_verified:'邮箱已验证', login:'账号', game_id:'游戏映射', status:'状态', created_at:'创建时间', last_login:'最近登录',
  character:'角色', nickname:'昵称', init_state:'初始化状态', profile:'环境', wallets:'钱包',
  currency:'币种', balance:'余额', inventory:'库存', equipment:'装备', sanctions:'制裁', room:'当前房间',
  slot:'槽位', item_key:'物品', quantity:'数量', expires_at:'有效期', bound:'绑定状态',
  max_uses:'可用次数', uses:'已使用', revoked:'撤销状态', capacity:'席位上限', map_key:'地图',
  state:'状态', type:'事件类型', attempts:'重试次数', last_error:'最近错误', time:'时间',
  action:'操作', result:'结果', reason:'原因', detail:'详情', role:'权限角色', kind:'身份',
  source:'来源', entries:'流水明细', entry_no:'序号', resource:'资源', delta:'变化',
  before_value:'变更前', after_value:'变更后', before:'操作前', after:'操作后',
  evidence:'验证范围', sync_status:'同步状态', retail_sync:'游戏内同步', retail_ready:'真实实例就绪',
  outcome:'对局结果', rewards:'奖励', logged_out:'退出结果', policy:'操作规则',
  invitation_token:'一次性邀请令牌', invitation_id:'邀请编号', operation_id:'操作编号',
  session_seconds:'会话有效秒数', permanent:'永久', frozen_targets:'目标列表', eligible:'奖励资格',
}
const valueLabels: Record<string,string> = { ACTIVE:'正常', PENDING_REVIEW:'待审核', DISABLED:'已停用',
  OPEN:'开放', CLOSED:'已关闭', ALLOCATING:'分配中', RUNNING:'运行中', STARTING:'启动中',
  READY:'就绪', STOPPED:'已停止', FAILED:'失败', COMMITTED:'已提交', REJECTED:'已拒绝',
  PENDING:'待同步', DELIVERED:'已送达', MANUAL_REVIEW:'待人工复核', UNIMPLEMENTED:'未接入',
  TEST_PROFILE:'合成测试', LOCAL_EQUIVALENT:'本地实现', owner:'超级管理员',users:'用户管理员',
  resources:'资源管理员',auditor:'审计员',player:'玩家',admin:'管理员',primary:'主武器',
  'test-rifle':'测试步枪','test-token':'测试道具','test-arena':'测试竞技场',
}
const technical = new Set(['id','account_id','subject_id','target','actor','request_id','inventory_id','match_id','instance_id','epoch','revision','version','security_version','linked_operation'])

/** 时间、枚举和空值采用用户可读格式；服务端原值在技术详情中保留，金额不凭空转换币种。 */
function DisplayValue({ name, value }: { name: string; value: unknown }) {
  if (value === null || value === undefined) return <span className="muted-value">{name === 'expires_at' ? '永久' : '暂无'}</span>
  if (Array.isArray(value)) return value.length ? <Records value={value} /> : <span className="muted-value">暂无记录</span>
  if (typeof value === 'object') return <Details value={value} />
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>
  if (typeof value === 'number') {
    if (name.endsWith('_at') || name==='time' || name==='last_login') return <span>{new Date(value*1000).toLocaleString('zh-CN',{hour12:false})}</span>
    if (name==='revoked' || name==='bound') return <span>{value ? '是' : '否'}</span>
    return <span className="numeric-value">{value.toLocaleString('zh-CN')}</span>
  }
  if (['status','state','result','sync_status'].includes(name) && valueLabels[String(value)]) return <span className={`status-chip ${['ACTIVE','COMMITTED','READY','OPEN','DELIVERED'].includes(String(value)) ? 'positive' : 'muted'}`}>{valueLabels[String(value)]}</span>
  return <span className="break-all">{valueLabels[String(value)] || String(value)}</span>
}

/** 普通详情用中文字段与分组，技术 ID 默认折叠；React 文本渲染不解析用户 HTML。 */
export function Details({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <p className="empty-state">暂无数据</p>
  if (typeof value !== 'object') return <p className="result-message">{String(value)}</p>
  if (Array.isArray(value)) return <Records value={value} />
  const entries=Object.entries(value)
  const visible=entries.filter(([key]) => !technical.has(key))
  const hidden=entries.filter(([key]) => technical.has(key))
  return <div className="record-details"><dl className="detail-grid">{visible.map(([key,item]) => <div key={key} className={typeof item==='object' && item!==null ? 'detail-group' : 'detail-item'}><dt>{fieldLabels[key] || key}</dt><dd><DisplayValue name={key} value={item} /></dd></div>)}</dl>
    {hidden.length>0 && <details className="technical-details"><summary>查看技术信息</summary><dl>{hidden.map(([key,item]) => <div key={key}><dt>{key}</dt><dd>{String(item ?? '暂无')}</dd></div>)}</dl></details>}
  </div>
}
/** 表格压缩技术列，操作/时间/结果优先；未知列仍保留，避免丢失已有业务接口信息。 */
export function Records({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return <Details value={value} />
  if (!value.length) return <div className="empty-state"><strong>暂无记录</strong><p>完成相应操作后，记录会显示在这里。</p></div>
  const objects=value.filter(row => typeof row==='object' && row!==null && !Array.isArray(row))
  if (!objects.length) return <span>{value.join('、')}</span>
  const columns = [...new Set(objects.flatMap(row => Object.keys(row)))].filter(key => !technical.has(key) && key!=='detail')
  return <Table className="business-table"><TableHeader><TableRow>{columns.map(column => <TableHead key={column}>{fieldLabels[column] || column}</TableHead>)}<TableHead>更多</TableHead></TableRow></TableHeader>
    <TableBody>{objects.map((row, index) => <TableRow key={row.id || index}>{columns.map(column => <TableCell key={column} className="max-w-72 align-top"><DisplayValue name={column} value={row[column]} /></TableCell>)}<TableCell><details className="table-details"><summary>详情</summary><Details value={row} /></details></TableCell></TableRow>)}</TableBody>
  </Table>
}
