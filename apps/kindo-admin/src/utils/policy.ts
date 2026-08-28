/** Family Policy 表单与契约之间的纯转换 / 校验（便于单元测试）。 */
import dayjs, { type Dayjs } from 'dayjs'
import customParseFormat from 'dayjs/plugin/customParseFormat'

dayjs.extend(customParseFormat)

export interface WindowRow {
  start: Dayjs | null
  end: Dayjs | null
}

export function parseWindow(s: string): Dayjs | null {
  return /^\d{2}:\d{2}$/.test(s) ? dayjs(s, 'HH:mm') : null
}

export function windowsToRows(
  windows: { start: string; end: string }[] | null | undefined,
): WindowRow[] {
  return (windows ?? []).map((w) => ({
    start: parseWindow(w.start) ?? dayjs('07:00', 'HH:mm'),
    end: parseWindow(w.end) ?? dayjs('21:00', 'HH:mm'),
  }))
}

export function rowsToWindows(rows: WindowRow[]): { start: string; end: string }[] {
  return rows
    .filter((r) => r.start && r.end)
    .map((r) => ({ start: r.start!.format('HH:mm'), end: r.end!.format('HH:mm') }))
}

/** 返回 null 表示该行合法，否则返回中文错误文案 */
export function validateWindowRow(row: WindowRow): string | null {
  if (!row.start || !row.end) return '请补全开始与结束时间'
  if (!row.start.isBefore(row.end)) return '开始时间需早于结束时间'
  return null
}

/** InputNumber 的值 → 契约的 minutes/集数；空或非法按"不限制"（null）处理 */
export function toLimitOrNull(v: number | string | null | undefined): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isInteger(n) && n > 0 ? n : null
}

/** 服务端历史数据可能存的 0（语义上等同不限）归一为 null，避免表单显示 0 且被 min 校验拦截 */
export function normalizeLimit(v: unknown): number | null {
  return typeof v === 'number' && v > 0 ? v : null
}
