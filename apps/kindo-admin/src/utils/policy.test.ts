import { describe, expect, it } from 'vitest'
import dayjs from 'dayjs'
import {
  normalizeLimit,
  parseWindow,
  rowsToWindows,
  toLimitOrNull,
  validateWindowRow,
  windowsToRows,
} from './policy'

const t = (s: string) => dayjs(s, 'HH:mm')

describe('parseWindow', () => {
  it('合法 HH:mm 解析', () => {
    expect(parseWindow('07:30')).not.toBeNull()
    expect(parseWindow('21:00')).not.toBeNull()
  })
  it('非法格式返回 null', () => {
    expect(parseWindow('7:00')).toBeNull()
    expect(parseWindow('abc')).toBeNull()
    expect(parseWindow('25:00')).not.toBeNull() // 格式合法即解析，跨点由 validateWindowRow 兜底
  })
})

describe('windowsToRows / rowsToWindows', () => {
  it('双向转换保留多段窗口', () => {
    const windows = [
      { start: '12:00', end: '13:30' },
      { start: '19:00', end: '21:00' },
    ]
    const rows = windowsToRows(windows)
    expect(rows).toHaveLength(2)
    expect(rowsToWindows(rows)).toEqual(windows)
  })
  it('rowsToWindows 丢弃不完整行', () => {
    const rows = [
      { start: t('12:00'), end: t('13:00') },
      { start: t('18:00'), end: null },
    ]
    expect(rowsToWindows(rows)).toEqual([{ start: '12:00', end: '13:00' }])
  })
  it('windowsToRows 对空/缺省安全', () => {
    expect(windowsToRows(null)).toEqual([])
    expect(windowsToRows(undefined)).toEqual([])
    expect(windowsToRows([{ start: 'bad', end: '21:00' }])).toEqual([
      { start: t('07:00'), end: t('21:00') }, // 非法值回落默认时段，避免表单崩溃
    ])
  })
})

describe('validateWindowRow', () => {
  it('合法行返回 null', () => {
    expect(validateWindowRow({ start: t('07:00'), end: t('21:00') })).toBeNull()
  })
  it('缺端点报错', () => {
    expect(validateWindowRow({ start: null, end: t('21:00') })).toContain('补全')
  })
  it('开始不早于结束报错（含相等）', () => {
    expect(validateWindowRow({ start: t('22:00'), end: t('21:00') })).toContain('早于')
    expect(validateWindowRow({ start: t('21:00'), end: t('21:00') })).toContain('早于')
  })
})

describe('toLimitOrNull', () => {
  it('空值按不限制', () => {
    expect(toLimitOrNull(null)).toBeNull()
    expect(toLimitOrNull(undefined)).toBeNull()
    expect(toLimitOrNull('')).toBeNull()
  })
  it('正整数保留', () => {
    expect(toLimitOrNull(30)).toBe(30)
    expect(toLimitOrNull('45')).toBe(45)
  })
  it('非正数 / 非整数按不限制（由表单校验兜底）', () => {
    expect(toLimitOrNull(0)).toBeNull()
    expect(toLimitOrNull(-5)).toBeNull()
    expect(toLimitOrNull(2.5)).toBeNull()
    expect(toLimitOrNull('abc')).toBeNull()
  })
})

describe('normalizeLimit', () => {
  it('0 / 负数 / 非数字的历史值归一为不限制', () => {
    expect(normalizeLimit(0)).toBeNull()
    expect(normalizeLimit(-3)).toBeNull()
    expect(normalizeLimit('30' as unknown)).toBeNull()
    expect(normalizeLimit(null)).toBeNull()
  })
  it('正整数保留', () => {
    expect(normalizeLimit(30)).toBe(30)
  })
})
