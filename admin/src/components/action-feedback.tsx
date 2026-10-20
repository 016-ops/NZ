/** 全局操作反馈保存在应用外壳；页面刷新和按钮卸载不会抹掉已完成的业务结果。 */
import { createContext, useContext, useState, type ReactNode } from 'react'
import { CheckCircle2, CircleAlert, Gift, Info, X } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog'
import { Button } from './ui/button'
import { ItemArtwork, itemName } from './item-artwork'

type Feedback = { title: string; message?: string; tone?: 'success' | 'error' | 'info'; data?: unknown }
type FeedbackActions = { notify: (value: Feedback) => void; showResult: (value: Feedback) => void }
const Context = createContext<FeedbackActions>({ notify: () => undefined, showResult: () => undefined })

/** 只提取明确的道具与数量字段；技术回执和凭据不能被直接序列化到结果弹窗。 */
function rewardLines(data: unknown): { id: string; quantity: number }[] {
  if (!data || typeof data !== 'object') return []
  const row = data as Record<string, unknown>
  const changes = Array.isArray(row.changes) ? row.changes : Array.isArray(row.rewards) ? row.rewards : []
  return changes.flatMap(value => {
    if (!value || typeof value !== 'object') return []
    const change = value as Record<string, unknown>
    const id = change.item_key ?? change.item_id
    return (typeof id === 'string' || typeof id === 'number') && typeof change.quantity === 'number'
      ? [{ id: String(id), quantity: change.quantity }] : []
  })
}

/** 使用shadcn Dialog处理焦点锁定、Esc关闭及焦点恢复；轻提示由用户关闭，不因定时器丢失。 */
export function ActionFeedbackProvider({ children }: { children: ReactNode }) {
  const [notification, setNotification] = useState<Feedback | null>(null)
  const [result, setResult] = useState<Feedback | null>(null)
  const lines = rewardLines(result?.data)
  const Icon = result?.tone === 'error' ? CircleAlert : result?.tone === 'info' ? Info : Gift
  return <Context.Provider value={{ notify: setNotification, showResult: setResult }}>{children}
    {notification && <aside className={`nz-action-toast is-${notification.tone || 'success'}`} role={notification.tone === 'error' ? 'alert' : 'status'}>
      {notification.tone === 'error' ? <CircleAlert size={22} /> : <CheckCircle2 size={22} />}<div><strong>{notification.title}</strong>{notification.message && <p>{notification.message}</p>}</div>
      <Button variant="ghost" size="icon" aria-label="关闭操作提示" onClick={() => setNotification(null)}><X size={16} /></Button>
    </aside>}
    <Dialog open={Boolean(result)} onOpenChange={open => { if (!open) setResult(null) }}><DialogContent className={`nz-dialog nz-result-dialog is-${result?.tone || 'success'}`}>
      <div className="nz-result-symbol"><Icon size={38} strokeWidth={1.4} /></div><DialogHeader><DialogTitle>{result?.title}</DialogTitle><DialogDescription>{result?.message || '操作结果已保存，可以继续查看活动与仓库。'}</DialogDescription></DialogHeader>
      {lines.length > 0 && <div className="nz-result-rewards">{lines.map((line, index) => <div key={`${line.id}-${index}`}><ItemArtwork itemKey={line.id} /><span>{itemName(line.id)}</span><strong>× {line.quantity}</strong></div>)}</div>}
      <Button onClick={() => setResult(null)}>知道了</Button>
    </DialogContent></Dialog>
  </Context.Provider>
}

/** 两端共享反馈入口；调用者仅提交已脱敏的中文文案与明确奖励字段。 */
export function useActionFeedback() { return useContext(Context) }
