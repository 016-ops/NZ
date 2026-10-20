/** 展示目录独立于冻结游戏规则：名称与图片可更新，item_id、协议和资源哈希保持稳定。 */
import { useState } from 'react'
import registry from '@/lib/item-presentation.json'

type Entry = { name: string; image_url: string; image_status: string; source: string }
const entries = registry.items as Record<string, Entry>
const fallback = '/assets/items/equipment-category-v3.webp'

/** 只规范化界面索引，不转换或改写提交给后端的资源键。未知资源不猜测名称。 */
function presentationId(value?: string | number) { return String(value ?? '').replace(/^cn2020:/, '') }

/** 优先使用已核实名称；旧合成夹具使用明确中文标签，其余保留“待确认”提示。 */
export function itemName(value?: string | number, supplied?: string): string {
  const id = presentationId(value)
  return entries[id]?.name || ({ 'test-rifle': '测试步枪', 'test-token': '测试补给券' } as Record<string, string>)[id]
    || (supplied && supplied !== String(value) ? supplied : '未收录道具（名称待确认）')
}

/** 固定比例和懒加载避免目录级请求；真实配图失败退回示意图并明确标注，不能冒充原道具立绘。 */
export function ItemArtwork({ itemId, itemKey, name, className = '' }: { itemId?: string | number; itemKey?: string; name?: string; className?: string }) {
  const row = entries[presentationId(itemId ?? itemKey)]
  const [failed, setFailed] = useState(false)
  const exact = row?.image_status === 'exact' && !failed
  const label = itemName(itemId ?? itemKey, name)
  return <figure className={`nz-item-artwork ${exact ? 'is-exact' : 'is-illustration'} ${className}`}>
    <img src={failed ? fallback : row?.image_url || fallback} alt={exact ? `${label}图鉴配图` : `${label}分类示意图`} loading="lazy" decoding="async" onError={() => { if (!failed) setFailed(true) }} />
    <figcaption>{exact ? '图鉴配图' : '分类示意'}</figcaption>
  </figure>
}
