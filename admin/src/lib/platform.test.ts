/** 运营展示回归：跨时区活动时间、毫秒成绩及玩法人数边界，避免误导玩家的业务显示。 */
import { describe, expect, it } from 'vitest'
import { formatDate, formatDuration, type PlayMap } from './platform'
import { mapCapacity } from '@/components/platform-pages'

describe('运营平台展示边界', () => {
  /** 管理员身处海外时仍明确显示活动北京时间，不能把前一天的当地时间标作北京时间。 */
  it('活动时间使用显式北京时间而不是执行机器时区', () => {
    const instant = Date.parse('2026-10-20T00:00:00+08:00') / 1000
    expect(formatDate(instant, '暂无', 'Asia/Shanghai')).toContain('2026/10/20')
    expect(formatDate(instant, '暂无', 'America/Los_Angeles')).toContain('2026/10/19')
  })
  /** 同一秒内的成绩仍需展示毫秒差异，避免让不同纪录看起来完全相同。 */
  it('通关成绩保留毫秒精度', () => {
    expect(formatDuration(62501)).toBe('01:02.501')
    expect(formatDuration(62599)).toBe('01:02.599')
  })
  /** 猎场八人榜与塔防四人榜分别建组，塔防不能出现五至八人的选择入口。 */
  it('人数分组符合两种首发玩法', () => {
    expect(mapCapacity({ kind: 'hunt' } as PlayMap)).toBe(8)
    expect(mapCapacity({ kind: 'tower_defense' } as PlayMap)).toBe(4)
  })
})
