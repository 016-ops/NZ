/** 官网直接读取同源公共封面，Launcher通过固定媒体ID取Blob；两端共享外观和生命周期。 */
import { useEffect, useState, type ImgHTMLAttributes } from 'react'
import type { RuntimeApi } from '@/lib/platform'
import { campaignCoverMediaId, campaignCoverPresetSource } from '@/lib/campaign-cover'

/** 切换封面、API或卸载时销毁Blob URL，迟到响应不能将旧活动图片放回新页面。 */
export function CampaignCover({ api, src, ...props }: Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> & { api: RuntimeApi; src: string }) {
  const [loaded, setLoaded] = useState<{ source: string; url: string; api: RuntimeApi } | null>(null)
  useEffect(() => {
    setLoaded(null)
    const mediaId = campaignCoverMediaId(src), read = api.readCampaignCover
    if (!read || !mediaId) return
    let active = true, objectUrl = ''
    // 只有已登记ID进入本机桥；失败保留页面原背景，不把未知地址交给浏览器直连。
    void Promise.resolve().then(() => read.call(api, mediaId)).then(blob => {
      if (!active || blob.type !== 'image/webp' || !blob.size || blob.size > 5 * 1024 * 1024) return
      objectUrl = URL.createObjectURL(blob); setLoaded({ source: src, url: objectUrl, api })
    }).catch(() => undefined)
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, src])
  // 预置图是随应用分发的三个静态资源，不需要cap或业务登录；其他地址仍走固定媒体ID校验。
  const display = campaignCoverPresetSource(src) || (api.readCampaignCover ? (loaded?.source === src && loaded.api === api ? loaded.url : null) : src)
  return display ? <img {...props} src={display} /> : null
}
