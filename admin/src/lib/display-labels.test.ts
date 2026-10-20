/** 技术资源码与默认界面文字分离，保留真实值供表单和折叠诊断使用。 */
import { describe, expect, it } from 'vitest'
import { accountStatusLabel, assetCodeLabel, difficultyChoices, inventoryStateLabel, mapKindLabel } from './platform'

describe('未核实资源显示', () => {
  /** 仅给选项编号，不把4097等未知协议值猜成正式难度名；原数组和值都不改变。 */
  it('难度选项保留接口值并使用中性中文标签', () => {
    const values = Object.freeze([4097, 8192])
    expect(difficultyChoices(values)).toEqual([{ value: 4097, label: '待确认难度 · 选项 1' }, { value: 8192, label: '待确认难度 · 选项 2' }])
    expect(values).toEqual([4097, 8192])
    expect(difficultyChoices([])).toEqual([])
  })
  /** 已有库存卡不会直接回显未知原生状态；账号未知枚举也明确待确认。 */
  it('未知库存和账号状态不回显原始枚举', () => {
    expect(inventoryStateLabel('NATIVE_SLOT_4097')).toBe('')
    expect(inventoryStateLabel('EXPIRED')).toBe('已过期')
    expect(accountStatusLabel('UNRECOGNIZED')).toBe('状态待确认')
    expect(mapKindLabel('MODE_4097')).toBe('模式待确认')
    expect(assetCodeLabel('currency', 'cn2020:GP')).toBe('币种名称待确认')
    expect(assetCodeLabel('slot', 'NATIVE_SLOT_4097')).toBe('装备槽位待确认')
    expect(assetCodeLabel('equipment_slot', 'primary')).toBe('主武器')
  })
})
