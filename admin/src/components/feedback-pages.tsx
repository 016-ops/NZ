/** 玩家反馈与后台处理共用工单页面；正文、回复及截图仅通过当前登录身份读取，不使用公开图片地址。 */
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, ImagePlus, LoaderCircle, MessageSquare, RefreshCw, Send, X } from 'lucide-react'
import { RetryEnvelope, type FeedbackAttachment } from '@/lib/api'
import { formatDate, useResource, type RuntimeApi } from '@/lib/platform'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import { CommandButton, Empty, Notice, PageHeading, Panel } from './platform-pages'
import { useActionFeedback } from './action-feedback'

type TicketState = 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CLOSED'
interface Ticket { id: string; title: string; state: TicketState; revision: number; created_at: number; updated_at: number; message_count: number; attachment_count: number; account_id?: string }
interface TicketDetail extends Ticket { messages: { id: string; body: string; author_kind: 'player' | 'admin'; created_at: number }[]; attachments: FeedbackAttachment[] }
interface UploadDraft { file: File; key: string; uploaded: boolean }
export const ticketStateLabels: Record<TicketState, string> = { OPEN: '待处理', IN_PROGRESS: '处理中', RESOLVED: '已解决', CLOSED: '已关闭' }

/** 前端提前限制总数、格式与单图大小；服务端仍重新解码验证，不能把扩展名作为可信图片证据。 */
export function validateFeedbackFiles(files: Pick<File, 'size' | 'type'>[], existing = 0) {
  if (files.length + existing > 3) return '每条反馈最多上传 3 张截图。'
  if (files.some(file => file.size > 5 * 1024 * 1024)) return '每张截图不能超过 5 MiB。'
  if (files.some(file => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) return '截图支持 PNG、JPEG 和 WebP 格式。'
  return null
}

/** 仅为当前选择文件创建临时预览URL；文件变化和卸载都会撤销，避免长时间浏览积累内存。 */
function DraftImage({ file }: { file: File }) {
  const [url, setUrl] = useState('')
  useEffect(() => { const next = URL.createObjectURL(file); setUrl(next); return () => URL.revokeObjectURL(next) }, [file])
  return <img src={url} alt={file.name} />
}

/** 已上传截图必须先鉴权取Blob，再在当前页面展示；点击可查看大图，关闭弹层不公开原始地址。 */
function PrivateImage({ api, attachment }: { api: RuntimeApi; attachment: FeedbackAttachment }) {
  const [url, setUrl] = useState(''), [error, setError] = useState(''), [open, setOpen] = useState(false)
  useEffect(() => {
    let active = true, objectUrl = ''
    if (!api.readFeedbackAttachment) { setError('当前启动器尚不支持读取截图。'); return }
    api.readFeedbackAttachment(attachment.id).then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) } }).catch(() => { if (active) setError('截图读取失败。') })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, attachment.id])
  return <>{error ? <p className="nz-form-error">{error}</p> : <button type="button" className="nz-feedback-image" onClick={() => setOpen(true)} disabled={!url}>{url ? <img src={url} alt="反馈截图，点击查看大图" /> : <LoaderCircle className="animate-spin" size={20} />}</button>}<Dialog open={open} onOpenChange={setOpen}><DialogContent className="nz-dialog nz-image-dialog"><DialogHeader><DialogTitle>反馈截图</DialogTitle></DialogHeader>{url && <img src={url} alt="反馈截图完整预览" />}</DialogContent></Dialog></>
}

/** 图片选择保留每张稳定上传键；上传失败时重试同一文件，不会产生重复附件。 */
function ScreenshotPicker({ files, setFiles, existing = 0, disabled }: { files: UploadDraft[]; setFiles: (files: UploadDraft[]) => void; existing?: number; disabled?: boolean }) {
  const { notify } = useActionFeedback()
  return <div className="nz-feedback-upload"><Label>问题截图（选填，每条反馈共 3 张，每张不超过 5 MiB）</Label><Input type="file" accept="image/png,image/jpeg,image/webp" multiple disabled={disabled || existing + files.length >= 3} onChange={event => {
    const added = Array.from(event.target.files || [])
    const error = validateFeedbackFiles(added, existing + files.length)
    if (error) notify({ title: '无法添加截图', message: error, tone: 'error' })
    else setFiles([...files, ...added.map(file => ({ file, key: crypto.randomUUID(), uploaded: false }))])
    event.target.value = ''
  }} /><div className="nz-feedback-drafts">{files.map((item, index) => <div key={item.key}><DraftImage file={item.file} /><small>{item.uploaded ? '已上传' : item.file.name}</small><Button type="button" size="icon" variant="ghost" aria-label={`移除截图 ${index + 1}`} disabled={disabled || item.uploaded} onClick={() => setFiles(files.filter((_, n) => n !== index))}><X size={14} /></Button></div>)}</div></div>
}

/** 新反馈先创建正文，再独立上传截图；正文已成功时的再次提交只补传失败图片。 */
function CreateTicket({ api, onCreated }: { api: RuntimeApi; onCreated: (id: string) => void }) {
  const [title, setTitle] = useState(''), [body, setBody] = useState(''), [files, setFiles] = useState<UploadDraft[]>([])
  const [created, setCreated] = useState(''), [busy, setBusy] = useState(false)
  const retry = useRef(new RetryEnvelope())
  const { notify } = useActionFeedback()
  /** 正文确认成功后冻结工单ID；截图失败不能使下一次重试重新创建正文。 */
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    setBusy(true)
    let ticketId = created
    try {
      if (!ticketId) { const payload = { title: title.trim(), body: body.trim() }; const value = await api.command('feedback.create', payload, retry.current.prepare(payload, '玩家提交问题反馈')) as { ticket_id: string }; ticketId = value.ticket_id; setCreated(ticketId); retry.current.success() }
      for (const item of files.filter(value => !value.uploaded)) {
        if (!api.uploadFeedbackAttachment) throw new Error('正文已保存，当前启动器尚不支持截图上传，请稍后补充。')
        await api.uploadFeedbackAttachment(ticketId, item.file, item.key)
        setFiles(previous => previous.map(value => value.key === item.key ? { ...value, uploaded: true } : value))
      }
      notify({ title: '反馈已提交', message: '处理进度与回复会在反馈中心更新。', tone: 'success' }); onCreated(ticketId)
    } catch (error) { notify({ title: ticketId ? '正文已保存，截图尚未全部上传' : '反馈未提交', message: error instanceof Error ? error.message : '请稍后重试。', tone: 'error' }) }
    finally { setBusy(false) }
  }
  return <Panel title="告诉我们发生了什么"><form className="nz-feedback-form grid gap-4" onSubmit={submit}><Label htmlFor="feedback-title">问题标题</Label><Input id="feedback-title" required maxLength={120} value={title} disabled={busy || !!created} onChange={event => setTitle(event.target.value)} placeholder="简要描述遇到的问题" /><Label htmlFor="feedback-body">详细说明</Label><textarea id="feedback-body" required minLength={1} maxLength={5000} rows={6} value={body} disabled={busy || !!created} onChange={event => setBody(event.target.value)} placeholder="请说明发生时间、操作步骤，以及你看到的现象。" /><ScreenshotPicker files={files} setFiles={setFiles} disabled={busy} /><div className="nz-inline-actions"><Button disabled={busy}><Send size={15} />{busy ? '提交中…' : created ? '重试上传剩余截图' : '提交反馈'}</Button>{created && <Button type="button" variant="outline" disabled={busy} onClick={() => onCreated(created)}>稍后补图，先查看反馈</Button>}</div></form></Panel>
}

/** 工单详情以revision防止覆盖管理员并发回复；关闭后只读，已解决工单回复后由后端重开。 */
function TicketConversation({ api, ticketId, management, readOnly, close }: { api: RuntimeApi; ticketId: string; management: boolean; readOnly: boolean; close: () => void }) {
  const detail = useResource<TicketDetail>(api, `/api/views/feedback-detail?ticket_id=${encodeURIComponent(ticketId)}`)
  const [body, setBody] = useState(''), [state, setState] = useState<TicketState>('OPEN'), [files, setFiles] = useState<UploadDraft[]>([]), [busy, setBusy] = useState(false)
  const retry = useRef(new RetryEnvelope())
  const { notify } = useActionFeedback()
  const ticket = detail.data
  useEffect(() => { if (ticket) setState(ticket.state) }, [ticket?.revision])
  /** 回复携带刚读取的revision；冲突时保留输入内容供刷新后再次确认。 */
  async function reply(event: FormEvent) {
    event.preventDefault(); if (!ticket || busy) return
    setBusy(true)
    try { const payload = { ticket_id: ticketId, expected_revision: ticket.revision, body: body.trim() }; await api.command('feedback.reply', payload, retry.current.prepare(payload, management ? '管理员回复玩家反馈' : '玩家补充问题反馈')); retry.current.success(); setBody(''); notify({ title: '回复已发送', tone: 'success' }); detail.refresh() }
    catch (error) { notify({ title: '回复未发送', message: error instanceof Error ? error.message : '请刷新后重试。', tone: 'error' }) }
    finally { setBusy(false) }
  }
  /** 分图顺序上传，失败只保留未完成文件；回读最新工单revision供后续回复使用。 */
  async function upload() {
    if (!ticket || busy || !api.uploadFeedbackAttachment) return
    setBusy(true)
    try { for (const item of files.filter(value => !value.uploaded)) { await api.uploadFeedbackAttachment(ticketId, item.file, item.key); setFiles(previous => previous.map(value => value.key === item.key ? { ...value, uploaded: true } : value)) } setFiles([]); notify({ title: '截图已补充', tone: 'success' }); detail.refresh() }
    catch (error) { setFiles(previous => previous.filter(item => !item.uploaded)); detail.refresh(); notify({ title: '部分截图尚未上传', message: error instanceof Error ? error.message : '请重试。', tone: 'error' }) }
    finally { setBusy(false) }
  }
  return <section className="nz-page"><Button variant="ghost" onClick={close}><ArrowLeft size={16} />返回反馈列表</Button>{detail.error && <Notice error>{detail.error}</Notice>}{detail.loading && <p className="nz-loading">正在读取反馈…</p>}{ticket && <><PageHeading title={ticket.title} description={`提交于 ${formatDate(ticket.created_at)} · ${ticketStateLabels[ticket.state]}`} action={<Button variant="outline" onClick={detail.refresh}><RefreshCw size={15} />刷新</Button>} />
    {management && !readOnly && <Panel title="处理状态"><div className="nz-inline-actions"><select aria-label="工单处理状态" disabled={ticket.state === 'CLOSED'} value={state} onChange={event => setState(event.target.value as TicketState)}>{Object.entries(ticketStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><CommandButton api={api} action="feedback.status" data={{ ticket_id: ticketId, expected_revision: ticket.revision, state }} reason="管理员更新反馈处理状态" disabled={state === ticket.state || ticket.state === 'CLOSED'} confirm={state === 'CLOSED' ? '关闭后将不能继续回复或补图。确认关闭？' : undefined} onDone={detail.refresh}>保存处理状态</CommandButton></div></Panel>}
    <div className="nz-feedback-conversation">{ticket.messages.map(message => <article key={message.id} className={`nz-feedback-message is-${message.author_kind}`}><header><strong>{message.author_kind === 'admin' ? '管理员回复' : '玩家反馈'}</strong><time>{formatDate(message.created_at)}</time></header><p className="nz-preline">{message.body}</p></article>)}</div>
    {!!ticket.attachments.length && <Panel title={`问题截图（${ticket.attachments.length}/3）`}><div className="nz-feedback-attachments">{ticket.attachments.map(attachment => <PrivateImage api={api} attachment={attachment} key={attachment.id} />)}</div></Panel>}
    {ticket.state === 'CLOSED' ? <Notice>此反馈已关闭，当前记录可继续查看。遇到新的问题请新建反馈。</Notice> : !readOnly && <><Panel title={management ? '回复玩家' : '补充说明'}>{ticket.state === 'RESOLVED' && <p className="nz-form-hint">回复后反馈会重新进入待处理状态。</p>}<form className="grid gap-4" onSubmit={reply}><Label htmlFor="feedback-reply">回复内容</Label><textarea id="feedback-reply" required maxLength={5000} rows={4} value={body} disabled={busy} onChange={event => setBody(event.target.value)} /><Button disabled={busy || !body.trim()}><Send size={15} />发送回复</Button></form></Panel>{ticket.attachments.length < 3 && <Panel title="补充问题截图"><ScreenshotPicker files={files} setFiles={setFiles} existing={ticket.attachments.length} disabled={busy} /><Button disabled={busy || !files.length} onClick={() => void upload()}><ImagePlus size={15} />{busy ? '上传中…' : '上传截图'}</Button></Panel>}</>}
  </>}</section>
}

/** 玩家只读本人列表；后台仅用户管理员/Owner可以回复和改状态，审计员保持只读。 */
export function FeedbackPage({ api, management = false, readOnly = false }: { api: RuntimeApi; management?: boolean; readOnly?: boolean }) {
  const [selected, setSelected] = useState<string | null>(null), [creating, setCreating] = useState(false), [offset, setOffset] = useState(0), [state, setState] = useState('')
  const tickets = useResource<{ tickets: Ticket[]; total: number }>(api, `/api/views/${management ? 'feedback-admin' : 'feedback'}?offset=${offset}&limit=20${state ? `&state=${state}` : ''}`)
  if (selected) return <TicketConversation key={selected} api={api} ticketId={selected} management={management} readOnly={readOnly} close={() => { setSelected(null); tickets.refresh() }} />
  return <section className="nz-page"><PageHeading title={management ? '玩家反馈' : '反馈中心'} description={management ? '查看问题、回复玩家并跟进处理进度。' : '遇到问题或有新的想法，在这里告诉我们。'} action={!management ? <Button onClick={() => setCreating(value => !value)}><MessageSquare size={16} />{creating ? '收起新反馈' : '提交新反馈'}</Button> : undefined} />{creating && <CreateTicket api={api} onCreated={id => { setCreating(false); setSelected(id); tickets.refresh() }} />}<div className="nz-inline-actions"><Label htmlFor="feedback-filter">处理状态</Label><select id="feedback-filter" value={state} onChange={event => { setState(event.target.value); setOffset(0) }}><option value="">全部反馈</option>{Object.entries(ticketStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><Button variant="ghost" onClick={tickets.refresh}><RefreshCw size={15} />刷新</Button></div>{tickets.error && <Notice error>{tickets.error}</Notice>}{tickets.loading ? <p className="nz-loading">正在读取反馈…</p> : tickets.data?.tickets.length ? <div className="nz-feedback-list">{tickets.data.tickets.map(ticket => <button key={ticket.id} onClick={() => setSelected(ticket.id)}><MessageSquare size={22} /><div><strong>{ticket.title}</strong><small>{formatDate(ticket.updated_at)} · {ticket.message_count} 条消息 · {ticket.attachment_count} 张截图</small></div><span className={`nz-tag is-${ticket.state.toLowerCase()}`}>{ticketStateLabels[ticket.state]}</span><ArrowRight size={16} /></button>)}</div> : !tickets.error && <Empty title="暂时没有反馈" icon={<MessageSquare size={34} />}>{management ? '玩家提交问题后，会在这里显示。' : '提交第一条反馈，我们会在这里与你沟通。'}</Empty>}{!!tickets.data?.total && <div className="nz-pagination"><Button variant="outline" disabled={!offset || tickets.loading} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</Button><span>第 {Math.floor(offset / 20) + 1} 页 · 共 {tickets.data.total} 条</span><Button variant="outline" disabled={tickets.loading || offset + 20 >= tickets.data.total} onClick={() => setOffset(value => value + 20)}>下一页</Button></div>}</section>
}
