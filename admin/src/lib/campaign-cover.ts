/** 活动公共封面的固定ID与回环图片信封校验；不把服务端URL变成任意地址代理。 */
const MAX_COVER_BYTES = 5 * 1024 * 1024
const MAX_COVER_BASE64 = 4 * Math.ceil(MAX_COVER_BYTES / 3)
/** 三种受控模板使用随前端打包的固定图片；不允许由配置扩展成本地任意路径。 */
const PRESET_COVERS = {
  default: '/assets/hero-duo-v3.webp',
  tactical: '/assets/items/primary-category-v3.webp',
  anniversary: '/assets/items/equipment-category-v3.webp',
} as const

/** 自定义公共封面优先；未上传时根据当前主题即时选用预置图，未知主题回退默认模板。 */
export function campaignCoverSource(presentation?: { theme?: string; cover_url?: string | null } | null): string {
  if (presentation?.cover_url) return presentation.cover_url
  const theme = presentation?.theme
  return theme === 'tactical' || theme === 'anniversary' ? PRESET_COVERS[theme] : PRESET_COVERS.default
}

/** Launcher只可直显三张完整匹配的打包预置图；不接受查询参数、外站、目录穿越或其他资源。 */
export function campaignCoverPresetSource(value: unknown): string | null {
  return typeof value === 'string' && Object.values(PRESET_COVERS).some(source => source === value) ? value : null
}

/** 只识别后台生成的同源公共媒体路径；私图、查询参数、绝对地址和多层路径均拒绝。 */
export function campaignCoverMediaId(value: unknown): string | null {
  return typeof value === 'string' ? /^\/api\/media\/([a-f0-9]{32})$/.exec(value)?.[1] || null : null
}

/** 本机桥输出必须为有界WebP/base64；先限制编码大小，再解码，防止意外分配无限内存。 */
export function decodeCampaignCoverImage(value: unknown): Blob {
  const image = value as { content_base64?: unknown; content_type?: unknown } | null
  const encoded = image?.content_base64
  if (image?.content_type !== 'image/webp' || typeof encoded !== 'string' || !encoded.length
      || encoded.length > MAX_COVER_BASE64 || encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) {
    throw new Error('活动封面内容无法读取。')
  }
  let binary: string
  try { binary = atob(encoded) } catch { throw new Error('活动封面内容无法读取。') }
  if (!binary.length || binary.length > MAX_COVER_BYTES) throw new Error('活动封面超过允许大小。')
  return new Blob([Uint8Array.from(binary, character => character.charCodeAt(0))], { type: 'image/webp' })
}
