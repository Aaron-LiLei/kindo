/** 全站统一的时长 / 日期 / 标签格式化（替代各页面各自实现的三种风格）。 */
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

export function fmtDurationMs(ms: number): string {
  if (ms <= 0) return '—'
  if (ms < 60000) return `${Math.round(ms / 1000)} 秒`
  const minutes = Math.round(ms / 60000)
  if (minutes < 60) return `${minutes} 分钟`
  const h = Math.floor(minutes / 60)
  const r = minutes % 60
  return r > 0 ? `${h} 小时 ${r} 分钟` : `${h} 小时`
}

export function fmtWatchSeconds(seconds: number): string {
  return fmtDurationMs(seconds * 1000)
}

/** 紧凑体积（媒体卡片角标用：246 MB / 1.8 GB；无效值返回 null） */
export function compactSize(bytes: number): string | null {
  if (!Number.isFinite(bytes) || bytes <= 0) return null
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(0)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

/** 体积全称（合集卡/详情用：1.8 GB / 246 MB） */
export function fmtSizeBytes(bytes: number): string {
  return compactSize(bytes) ?? '—'
}

export function fmtDateTime(input: string | Date | null | undefined): string {
  if (!input) return '—'
  const d = dayjs(input)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm') : String(input)
}

export function fromNow(input: string | Date | null | undefined): string {
  if (!input) return '—'
  const d = dayjs(input)
  return d.isValid() ? d.fromNow() : String(input)
}

/** 把顿号/逗号/空格分隔的输入拆成标签数组 */
export function parseTagList(s: string): string[] {
  return s
    .split(/[、,，\s]+/)
    .map((x) => x.trim())
    .filter(Boolean)
}
