/** 公共封面只代理固定ID；浏览器解码前后分别检查编码预算、MIME及真实字节数。 */
import { describe, expect, it } from 'vitest'
import { campaignCoverMediaId, campaignCoverPresetSource, campaignCoverSource, decodeCampaignCoverImage } from './campaign-cover'

describe('活动模板预置封面', () => {
  /** 列表、详情和后台预览共用同一来源规则；模板变化只影响没有自定义封面的活动。 */
  it('三种主题动态切换预置图，缺省或未知主题回退默认，自定义封面优先', () => {
    expect(campaignCoverSource()).toBe('/assets/hero-duo-v3.webp')
    expect(campaignCoverSource({ theme: 'default' })).toBe('/assets/hero-duo-v3.webp')
    expect(campaignCoverSource({ theme: 'tactical', cover_url: null })).toBe('/assets/items/primary-category-v3.webp')
    expect(campaignCoverSource({ theme: 'anniversary', cover_url: '' })).toBe('/assets/items/equipment-category-v3.webp')
    expect(campaignCoverSource({ theme: 'unknown' })).toBe('/assets/hero-duo-v3.webp')
    const custom = '/api/media/' + 'a'.repeat(32)
    expect(campaignCoverSource({ theme: 'anniversary', cover_url: custom })).toBe(custom)
  })
  /** 静态资源例外必须严格列举，不能把同目录其他文件或拼接地址提升为可直显资源。 */
  it('仅三张固定本地预置图可在Launcher直显，拒绝未知地址和路径变体', () => {
    for (const theme of ['default', 'tactical', 'anniversary']) {
      const source = campaignCoverSource({ theme })
      expect(campaignCoverPresetSource(source)).toBe(source)
      expect(campaignCoverMediaId(source)).toBeNull()
      for (const suffix of ['?x=1', '#preview', '/other']) expect(campaignCoverPresetSource(source + suffix)).toBeNull()
    }
    for (const value of [null, '/assets/items/unknown.webp', '/assets/../hero-duo-v3.webp', '/assets/%68ero-duo-v3.webp',
      'https://example.invalid/assets/hero-duo-v3.webp', '//example.invalid/assets/hero-duo-v3.webp', '/api/media/' + 'a'.repeat(32)]) {
      expect(campaignCoverPresetSource(value)).toBeNull()
    }
  })
})

describe('活动封面固定路径', () => {
  it('只接受服务端同源公共媒体路径', () => {
    const id = 'a'.repeat(32)
    expect(campaignCoverMediaId('/api/media/' + id)).toBe(id)
    for (const value of ['https://other.invalid/api/media/' + id, '//other.invalid/api/media/' + id,
      '/api/media/' + id + '?x=1', '/api/media/' + id + '#x', '/api/feedback/attachments/' + id,
      '/api/media/../secret', '/api/media/' + 'A'.repeat(32), null]) expect(campaignCoverMediaId(value)).toBeNull()
  })
})

describe('本机公共封面信封', () => {
  it('有界WebP转换为Blob，不携带地址或会话信息', async () => {
    const blob = decodeCampaignCoverImage({ content_type: 'image/webp', content_base64: btoa('webp-bytes') })
    expect(blob.type).toBe('image/webp')
    expect(await blob.text()).toBe('webp-bytes')
  })
  it('拒绝未知MIME、损坏编码及编码前后的超限内容', () => {
    for (const value of [null, { content_type: 'image/svg+xml', content_base64: btoa('svg') },
      { content_type: 'image/webp', content_base64: '%%==' }, { content_type: 'image/webp', content_base64: '' },
      { content_type: 'image/webp', content_base64: 'a'.repeat(6990512) }]) expect(() => decodeCampaignCoverImage(value)).toThrow()
    // 5MiB+1与5MiB的base64长度相同，必须在解码后再次检查，不能只依赖编码字符数。
    expect(() => decodeCampaignCoverImage({ content_type: 'image/webp', content_base64: btoa('x'.repeat(5*1024*1024+1)) })).toThrow('大小')
  })
})
