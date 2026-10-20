/** 活动专题外观独立于冻结奖励配置；管理员选择受控模块，不提供HTML、脚本或任意图片地址输入。 */
import { useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ImagePlus, Plus, Trash2 } from 'lucide-react'
import { RetryEnvelope, type Data } from '@/lib/api'
import type { Campaign, CampaignPresentation, RuntimeApi } from '@/lib/platform'
import { campaignCoverSource } from '@/lib/campaign-cover'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { useActionFeedback } from './action-feedback'
import { CampaignCover } from './campaign-cover'

/** 按已有展示revision保存；自定义封面只引用公共媒体ID，未上传则以所选模板预置图预览。 */
export function CampaignPresentationEditor({ api, campaign, refresh, readOnly }: { api: RuntimeApi; campaign: Campaign; refresh: () => void; readOnly: boolean }) {
  const base = campaign.presentation
  const [slug, setSlug] = useState(base?.slug || campaign.id), [title, setTitle] = useState(base?.title || campaign.title), [summary, setSummary] = useState(base?.summary || '')
  const [theme, setTheme] = useState<CampaignPresentation['theme']>(base?.theme || 'default')
  const [cover, setCover] = useState<{ id: string | null; url: string | null }>({ id: base?.cover_id || null, url: base?.cover_url || null })
  const [sections, setSections] = useState<CampaignPresentation['sections']>(base?.sections?.length ? base.sections : [{ key: 'rewards', kind: 'rewards', title: '本期奖励' }])
  const [busy, setBusy] = useState(false)
  const retry = useRef(new RetryEnvelope()), upload = useRef<{ file: File; key: string } | null>(null)
  const { notify } = useActionFeedback()
  /** 移动模块只改变展示顺序，原奖励key和资格判断都继续使用冻结定义。 */
  function move(index: number, delta: number) { setSections(previous => { const result = [...previous]; [result[index], result[index + delta]] = [result[index + delta], result[index]]; return result }) }
  return <details className="nz-presentation-editor"><summary>活动页面与模板配置</summary><form className="grid gap-4" onSubmit={async event => {
    event.preventDefault(); if (busy || readOnly) return
    setBusy(true)
    try { const payload = { campaign_id: campaign.id, expected_revision: base?.revision || 1, presentation: { slug, title, summary, theme, cover_id: cover.id, sections } } as unknown as Data; await api.command('campaign.presentation.save', payload, retry.current.prepare(payload, '管理员保存活动专题展示')); retry.current.success(); notify({ title: '活动页面已保存', message: '奖励条件与发放规则保持原冻结定义。', tone: 'success' }); refresh() }
    catch (error) { notify({ title: '页面配置未保存', message: error instanceof Error ? error.message : '请刷新后重试。', tone: 'error' }) }
    finally { setBusy(false) }
  }}><div className="nz-form-grid"><div className="nz-field"><Label htmlFor={`slug-${campaign.id}`}>活动地址名称</Label><Input id={`slug-${campaign.id}`} required pattern="[a-z0-9-]+" maxLength={96} value={slug} disabled={readOnly || busy || campaign.state !== 'DRAFT'} onChange={event => setSlug(event.target.value)} /><small>发布后地址固定，格式为小写字母、数字和连字符。</small></div><div className="nz-field"><Label htmlFor={`theme-${campaign.id}`}>页面模板</Label><select id={`theme-${campaign.id}`} disabled={readOnly || busy} value={theme} onChange={event => setTheme(event.target.value as CampaignPresentation['theme'])}><option value="default">战友集结</option><option value="tactical">战术挑战</option><option value="anniversary">纪念庆典</option></select></div><div className="nz-field nz-span-2"><Label htmlFor={`display-title-${campaign.id}`}>页面主标题</Label><Input id={`display-title-${campaign.id}`} required maxLength={120} disabled={readOnly || busy} value={title} onChange={event => setTitle(event.target.value)} /></div><div className="nz-field nz-span-2"><Label htmlFor={`summary-${campaign.id}`}>活动简介</Label><textarea id={`summary-${campaign.id}`} maxLength={500} rows={3} disabled={readOnly || busy} value={summary} onChange={event => setSummary(event.target.value.replace(/[\r\n\t]/g, ' '))} /></div></div>
    <div className="nz-field"><Label htmlFor={`cover-${campaign.id}`}>活动封面</Label><CampaignCover api={api} className="nz-presentation-cover-preview" src={campaignCoverSource({ theme, cover_url: cover.url })} alt={cover.url ? '当前自定义活动封面' : '当前模板预置封面'} /><small>{cover.url ? '当前使用自定义封面；切换模板后仍优先使用此图。' : '当前使用模板预置封面，切换页面模板即可预览不同封面。'}</small><Input id={`cover-${campaign.id}`} type="file" accept="image/png,image/jpeg,image/webp" disabled={readOnly || busy} onChange={async event => {
      const file = event.target.files?.[0]; if (!file) return
      if (file.size > 5 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { notify({ title: '封面无法上传', message: '请选择不超过 5 MiB 的 PNG、JPEG 或 WebP 图片。', tone: 'error' }); return }
      if (!api.uploadCampaignCover) { notify({ title: '当前入口不支持封面上传', tone: 'error' }); return }
      setBusy(true)
      try { if (upload.current?.file !== file) upload.current = { file, key: crypto.randomUUID() }; const result = await api.uploadCampaignCover(file, upload.current.key); setCover({ id: result.id, url: result.url }); notify({ title: '封面已上传', message: '保存页面配置后生效。', tone: 'success' }) }
      catch (error) { notify({ title: '封面上传失败', message: error instanceof Error ? error.message : '请重试。', tone: 'error' }) }
      finally { setBusy(false) }
    }} />{cover.id && !readOnly && <Button type="button" variant="ghost" disabled={busy} onClick={() => setCover({ id: null, url: null })}>使用模板预置封面</Button>}</div>
    {sections.map((section, index) => <fieldset key={section.key} className="nz-reward-editor"><legend>页面模块 {index + 1}</legend><div className="nz-form-grid"><div className="nz-field"><Label>模块类型<select disabled={readOnly || busy} value={section.kind} onChange={event => setSections(previous => previous.map((value, n) => n === index ? { ...value, kind: event.target.value as typeof section.kind } : value))}><option value="text">规则说明</option><option value="rewards">真实奖励列表</option><option value="leaderboards">真实排行榜</option></select></Label></div><div className="nz-field"><Label>模块标题<Input required maxLength={120} disabled={readOnly || busy} value={section.title} onChange={event => setSections(previous => previous.map((value, n) => n === index ? { ...value, title: event.target.value } : value))} /></Label></div>{section.kind === 'text' && <div className="nz-field nz-span-2"><Label>说明正文<textarea rows={4} maxLength={5000} disabled={readOnly || busy} value={section.body || ''} onChange={event => setSections(previous => previous.map((value, n) => n === index ? { ...value, body: event.target.value } : value))} /></Label></div>}</div>{!readOnly && <div className="nz-inline-actions"><Button type="button" size="sm" variant="ghost" aria-label={`模块 ${index + 1} 上移`} disabled={busy || !index} onClick={() => move(index, -1)}><ArrowUp size={15} />上移</Button><Button type="button" size="sm" variant="ghost" aria-label={`模块 ${index + 1} 下移`} disabled={busy || index === sections.length - 1} onClick={() => move(index, 1)}><ArrowDown size={15} />下移</Button><Button type="button" size="sm" variant="ghost" disabled={busy || sections.length === 1} onClick={() => setSections(previous => previous.filter((_, n) => n !== index))}><Trash2 size={15} />移除</Button></div>}</fieldset>)}
    {!readOnly && <div className="nz-inline-actions"><Button type="button" variant="outline" disabled={busy || sections.length >= 12} onClick={() => setSections(previous => [...previous, { key: `section-${crypto.randomUUID().slice(0, 12)}`, kind: 'text', title: '活动说明', body: '' }])}><Plus size={15} />添加模块</Button><Button disabled={busy}><ImagePlus size={15} />{busy ? '保存中…' : '保存活动页面'}</Button></div>}
  </form></details>
}
