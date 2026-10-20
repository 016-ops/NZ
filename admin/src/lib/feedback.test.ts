/** 反馈截图和活动深链接边界；防止客户端误收超限图片或把无效路径当作有效活动。 */
import { describe, expect, it } from 'vitest'
import { validateFeedbackFiles } from '@/components/feedback-pages'
import { activitySlugFromPath } from './platform'
import { ApiError } from './api'
import { stepUpDescription } from '@/components/admin-step-up'

describe('反馈截图限制', () => {
  it('按整条工单的已有附件数限制三张，并接受5MiB边界', () => {
    expect(validateFeedbackFiles([{ size: 5 * 1024 * 1024, type: 'image/png' }], 2)).toBeNull()
    expect(validateFeedbackFiles([{ size: 1, type: 'image/jpeg' }, { size: 1, type: 'image/webp' }], 2)).toContain('3 张')
  })
  it('拒绝超限图片与不支持的SVG格式', () => {
    expect(validateFeedbackFiles([{ size: 5 * 1024 * 1024 + 1, type: 'image/png' }])).toContain('5 MiB')
    expect(validateFeedbackFiles([{ size: 100, type: 'image/svg+xml' }])).toContain('PNG')
  })
})
describe('活动路由与错误展示', () => {
  it('识别真实活动slug，不吞并多层路径', () => {
    expect(activitySlugFromPath('/activities/open-season-1')).toBe('open-season-1')
    expect(activitySlugFromPath('/activities/open-season-1/')).toBe('open-season-1')
    expect(activitySlugFromPath('/activities/one/another')).toBeNull()
    expect(activitySlugFromPath('/activities')).toBeNull()
  })
  it('保留错误码供程序判断，界面不直接展示英文后端正文', () => {
    const error = new ApiError('ADMIN_EMAIL_CODE', 'Email code expired', 403)
    expect(error.code).toBe('ADMIN_EMAIL_CODE')
    expect(error.message).toContain('验证码')
    expect(error.message).not.toContain('expired')
  })
})

/** 再验证弹窗只展示白名单摘要，敏感口令与任意payload不能混入用户确认内容。 */
describe('Owner确认摘要', () => {
  it('显示中文动作和短目标，排除密码与完整正文', () => {
    const value = stepUpDescription('admin.password.reset', { admin_id: '0123456789abcdef', new_password: 'do-not-show-secret', current_password: 'also-private', body: 'private-payload' })
    expect(value.action).toBe('重置管理员密码')
    expect(value.target).toContain('89abcdef')
    expect(JSON.stringify(value)).not.toContain('secret')
    expect(JSON.stringify(value)).not.toContain('private')
  })
})
