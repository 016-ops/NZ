/** 道具选择器把中文显示名与稳定资源键分开；同名目录条目用版本序号区分，提交仍使用真实key。 */
import { useEffect, useMemo, useState } from 'react'
import type { Data } from '@/lib/api'
import { Input } from './ui/input'
import { itemName } from './item-artwork'

/** 输入可搜索中文名称，完整选择后才交给父组件真实资源键；未选中时清空旧键防止误发。 */
export function CatalogItemPicker({ items, value, onChange, id, required = true, disabled = false }: {
  items: Record<string, Data>; value: string; onChange: (key: string) => void; id: string; required?: boolean; disabled?: boolean
}) {
  const options = useMemo(() => {
    const rows = Object.entries(items).map(([key, row]) => ({ key, name: itemName(key, String(row.name || row.label || '')) }))
    const counts = new Map<string, number>(), seen = new Map<string, number>()
    rows.forEach(row => counts.set(row.name, (counts.get(row.name) || 0) + 1))
    return rows.map(row => { const position = (seen.get(row.name) || 0) + 1; seen.set(row.name, position); return { key: row.key, label: counts.get(row.name)! > 1 ? `${row.name}（版本 ${position}）` : row.name } })
  }, [items])
  const [text, setText] = useState('')
  useEffect(() => { if (value) setText(options.find(row => row.key === value)?.label || '') }, [value, options])
  return <><Input id={id} list={`${id}-choices`} required={required} disabled={disabled} value={text} placeholder="输入中文名称并选择道具" onChange={event => { const next = event.target.value; setText(next); onChange(options.find(row => row.label === next)?.key || '') }} aria-invalid={!!text && !value} /><datalist id={`${id}-choices`}>{options.map(row => <option value={row.label} key={row.key} />)}</datalist>{!!text && !value && <small className="nz-form-hint">请从当前目录中选择一个完整名称。</small>}</>
}
