import { describe, expect, it } from 'vitest'
import { compactSize, fmtDurationMs, fmtSizeBytes, fmtWatchSeconds, fromNow, parseTagList } from './format'

describe('fmtDurationMs', () => {
  it('非正值显示占位符', () => {
    expect(fmtDurationMs(0)).toBe('—')
    expect(fmtDurationMs(-5)).toBe('—')
  })
  it('不足一分钟按秒', () => {
    expect(fmtDurationMs(20000)).toBe('20 秒')
  })
  it('分钟', () => {
    expect(fmtDurationMs(15 * 60000)).toBe('15 分钟')
  })
  it('小时 + 分钟', () => {
    expect(fmtDurationMs(90 * 60000)).toBe('1 小时 30 分钟')
    expect(fmtDurationMs(60 * 60000)).toBe('1 小时')
  })
})

describe('fmtWatchSeconds', () => {
  it('秒转时长文案', () => {
    expect(fmtWatchSeconds(3700)).toBe('1 小时 2 分钟')
    expect(fmtWatchSeconds(0)).toBe('—')
  })
})

describe('compactSize / fmtSizeBytes', () => {
  it('KB / MB / GB 阶梯', () => {
    expect(compactSize(500 * 1024)).toBe('500 KB')
    expect(compactSize(258 * 1024 * 1024)).toBe('258 MB')
    expect(compactSize(1.84 * 1024 * 1024 * 1024)).toBe('1.8 GB')
  })
  it('无效值 compact 返回 null、全称返回占位符', () => {
    expect(compactSize(0)).toBeNull()
    expect(compactSize(Number.NaN)).toBeNull()
    expect(fmtSizeBytes(0)).toBe('—')
  })
})

describe('parseTagList', () => {
  it('顿号/逗号/空格混合分隔', () => {
    expect(parseTagList('天天、 佩奇,乔治，苏西')).toEqual(['天天', '佩奇', '乔治', '苏西'])
  })
  it('空输入返回空数组', () => {
    expect(parseTagList('')).toEqual([])
    expect(parseTagList('  、， ')).toEqual([])
  })
})

describe('fromNow', () => {
  it('空值显示占位符', () => {
    expect(fromNow(null)).toBe('—')
    expect(fromNow(undefined)).toBe('—')
  })
})
