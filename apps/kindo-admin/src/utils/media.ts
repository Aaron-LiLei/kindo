/** 媒体列表的纯筛选逻辑（标题 / 角色 / 主题，在已加载条目内匹配）。 */
import type { MediaItem } from '../types/admin'

export function filterMedia(items: MediaItem[], q: string): MediaItem[] {
  const s = q.trim()
  if (!s) return items
  return items.filter(
    (m) =>
      m.title.includes(s) ||
      (m.tags.characters ?? []).some((c) => c.includes(s)) ||
      (m.tags.themes ?? []).some((t) => t.includes(s)),
  )
}

/** 挂载"位置"列的展示文本（local: root/sub；smb: host/share/path；webdav: url/path） */
export function mountLocation(m: {
  mount_type: string
  root_id: string
  sub_path: string
  path?: string | null
  config?: Record<string, unknown>
}): string {
  const cfg = m.config ?? {}
  if (m.mount_type === 'local') return m.path || `${m.root_id}/${m.sub_path}`
  if (m.mount_type === 'smb') {
    const base = `${String(cfg.host ?? '')}/${String(cfg.share ?? '')}`
    return cfg.path ? `${base}/${String(cfg.path)}` : base
  }
  const base = String(cfg.url ?? '')
  return cfg.path ? `${base}/${String(cfg.path)}` : base
}

// ---------- 扫描任务状态机（后端统一 queued/running/done/failed/interrupted） ----------

export const SCAN_JOB_STATES = ['queued', 'running', 'done', 'failed', 'interrupted'] as const

export function scanJobMeta(state: string): {
  text: string
  color: string
  badge: 'success' | 'error' | 'processing' | 'warning' | 'default'
} {
  switch (state) {
    case 'done':
      return { text: '完成', color: 'success', badge: 'success' }
    case 'failed':
      return { text: '失败', color: 'error', badge: 'error' }
    case 'running':
      return { text: '进行中', color: 'processing', badge: 'processing' }
    case 'queued':
      return { text: '排队中', color: 'default', badge: 'default' }
    case 'interrupted':
      return { text: '已中断', color: 'warning', badge: 'warning' }
    default:
      return { text: state, color: 'default', badge: 'default' }
  }
}

// ---------- 媒体库展示（2026-08-20 重构：海报卡片墙 / 合集浏览） ----------

export const MEDIA_TYPE_LABELS: Record<string, string> = {
  episode: '剧集',
  movie: '电影',
  lesson: '课程',
}

export function mediaTypeLabel(t: string): string {
  return MEDIA_TYPE_LABELS[t] ?? t
}

/** 集号 / 章节徽章文案（S1·E2 / 第1章·第2课） */
export function sequenceLabel(m: MediaItem): string | null {
  if (m.series) return `S${m.series.season_no}·E${m.series.episode_no}`
  if (m.course) return `第${m.course.chapter_no}章·第${m.course.lesson_no}课`
  return null
}

/** 无海报时的占位缩写（标题去空白后的前 2 字） */
export function titleInitials(title: string): string {
  const t = title.replace(/\s+/g, '')
  return [...t].slice(0, 2).join('')
}

const PLACEHOLDER_GRADIENTS = [
  ['#ffb37e', '#ff8a5c'],
  ['#8ec9ff', '#5ba8f5'],
  ['#b7e3a8', '#7ec36a'],
  ['#d4b8f0', '#a98be0'],
  ['#ffd98e', '#f5b95c'],
  ['#9fd8cf', '#5db8aa'],
  ['#f5a8c0', '#e075a0'],
  ['#c0c8d8', '#8894b0'],
]

/** 确定性占位渐变（同一合集/标题颜色稳定，视觉分组自然形成） */
export function placeholderGradient(seed: string): string {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0
  const [from, to] = PLACEHOLDER_GRADIENTS[h % PLACEHOLDER_GRADIENTS.length]
  return `linear-gradient(135deg, ${from}, ${to})`
}

/** 卡片占位渐变的种子：系列优先（同系列同色），其次标题 */
export function mediaSeed(m: MediaItem): string {
  return m.series?.title ?? m.course?.title ?? m.title
}

/** 紧凑时长徽章（卡片角标用：12:34 / 1:05:00） */
export function compactDuration(ms: number): string | null {
  if (ms <= 0) return null
  const s = Math.round(ms / 1000)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
  return `${m}:${String(r).padStart(2, '0')}`
}

/** 已加载条目内的合集分组（海报墙的"系列分节"渲染用） */
export function groupByCollection(
  items: MediaItem[],
): { key: string; title: string; items: MediaItem[] }[] {
  const groups = new Map<string, { key: string; title: string; items: MediaItem[] }>()
  for (const m of items) {
    const key = m.series?.series_id ?? m.course?.course_id ?? null
    if (!key) continue
    const title = m.series?.title ?? m.course?.title ?? ''
    const g = groups.get(key) ?? { key, title, items: [] }
    g.items.push(m)
    groups.set(key, g)
  }
  return [...groups.values()].sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'))
}

/** 同合集的其他集（详情抽屉"系列内其他内容"） */
export function collectionSiblings(items: MediaItem[], m: MediaItem): MediaItem[] {
  if (m.series) {
    return items
      .filter((x) => x.series?.series_id === m.series!.series_id && x.media_id !== m.media_id)
      .sort(
        (a, b) =>
          a.series!.season_no - b.series!.season_no || a.series!.episode_no - b.series!.episode_no,
      )
  }
  if (m.course) {
    return items
      .filter((x) => x.course?.course_id === m.course!.course_id && x.media_id !== m.media_id)
      .sort(
        (a, b) =>
          a.course!.chapter_no - b.course!.chapter_no || a.course!.lesson_no - b.course!.lesson_no,
      )
  }
  return []
}
