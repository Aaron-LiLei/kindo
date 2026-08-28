import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, Collapse, Drawer, Input, Segmented, Select, Space, Typography } from 'antd'
import {
  AppstoreOutlined,
  BarsOutlined,
  FolderOpenOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type { CollectionsResp, MediaItem } from '../../types/admin'
import { useApi } from '../../hooks/useApi'
import { SourcesManager } from './SourcesManager'
import { CollectionsView } from './CollectionsView'
import { MediaDetail } from './MediaDetail'
import { MediaEditModal } from './MediaEditModal'
import { MediaGrid } from './MediaGrid'
import { MediaListView } from './MediaListView'
import { AiCurateDrawer } from './AiCurateDrawer'
import { AiAdvisorPanel } from '../../components/AiAdvisorPanel'
import { filterMedia, mediaTypeLabel } from '../../utils/media'
import { fmtDurationMs } from '../../utils/format'

type ViewMode = 'wall' | 'collections' | 'table'

/** 类型筛选按库内实际内容派生（空类型不显示，避免点了没结果） */
const TYPE_LABELS: Record<string, string> = {
  episode: '剧集',
  movie: '电影',
  lesson: '课程',
  story: '故事',
  song: '儿歌',
}

function fmtCount(n: number): string {
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万` : String(n)
}

function totalLabel(counts: Record<string, number>): string {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return fmtCount(total)
}

/** 合集（=系列/课程）× 类型筛选的一致映射：
 * 剧集→系列合集、课程→课程合集；电影/儿歌/故事没有合集形态 → 置空并提示。 */
function filterCollectionsForType(data: CollectionsResp | null, type?: string) {
  if (!data) return data
  if (!type) return data
  if (type === 'episode') return { ...data, courses: [] }
  if (type === 'lesson') return { ...data, series: [] }
  return { ...data, series: [], courses: [] }
}

const VIEW_OPTIONS = [
  { label: '海报墙', value: 'wall', icon: <AppstoreOutlined /> },
  { label: '按合集', value: 'collections', icon: <FolderOpenOutlined /> },
  { label: '列表', value: 'table', icon: <BarsOutlined /> },
]

/** 媒体列表：服务端 cursor 分页（每页 100，上限即后端约束）+ type/language 服务端筛选 */
function useMediaList() {
  const [type, setTypeRaw] = useState<string | undefined>()
  const [language, setLanguage] = useState<string | undefined>()
  const [sort, setSort] = useState<'added' | 'title'>('added')
  const [items, setItems] = useState<MediaItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [typeCounts, setTypeCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  const hasDataRef = useRef(false)

  // 切筛选/排序 = 换数据集：清空已加载内容再取（否则旧条目残留到新结果前）
  const setType = (v?: string) => {
    setTypeRaw(v)
    hasDataRef.current = false
    setItems([])
  }
  const changeSort = (v: 'added' | 'title') => {
    setSort(v)
    hasDataRef.current = false
    setItems([])
  }

  useEffect(() => {
    let cancelled = false
    if (!hasDataRef.current) setLoading(true)
    adminApi
      .mediaList({ type, language, sort, limit: 100 })
      .then((r) => {
        if (cancelled) return
        hasDataRef.current = true
        setItems(r.items)
        setCursor(r.next_cursor)
        setTypeCounts(r.type_counts ?? {})
        setError('')
        setLoading(false)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(formatApiError(e))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [type, language, sort, version])

  const loadMore = async () => {
    if (!cursor) return
    setLoadingMore(true)
    try {
      const r = await adminApi.mediaList({ type, language, sort, cursor, limit: 100 })
      setItems((prev) => [...prev, ...r.items])
      setCursor(r.next_cursor)
    } catch (e) {
      setError(formatApiError(e))
    } finally {
      setLoadingMore(false)
    }
  }

  const reload = () => setVersion((v) => v + 1)
  return {
    items,
    cursor,
    loading,
    loadingMore,
    error,
    type,
    setType,
    language,
    setLanguage,
    sort,
    changeSort,
    typeCounts,
    loadMore,
    reload,
  }
}

/** 搜索防抖（300ms）：搜索在已加载条目内做客户端匹配 */
function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export function MediaPage() {
  const list = useMediaList()
  const [searchInput, setSearchInput] = useState('')
  const search = useDebounced(searchInput)
  const [view, setView] = useState<ViewMode>('wall')
  const [detailId, setDetailId] = useState<string | null>(null)
  const [editing, setEditing] = useState<MediaItem | null>(null)
  const [aiOpen, setAiOpen] = useState(false)
  const [coverageOpen, setCoverageOpen] = useState(false)

  const {
    data: collections,
    error: collectionsError,
    reload: reloadCollections,
  } = useApi<CollectionsResp>('/api/v1/admin/collections')

  const reloadAll = () => {
    list.reload()
    reloadCollections()
  }

  // 筛选选项从已加载条目派生（并并入当前选中值，避免选定后从下拉中消失）
  const langOptions = useMemo(() => {
    const s = new Set(list.items.map((m) => m.language).filter((l): l is string => l !== null))
    if (list.language) s.add(list.language)
    return [...s].map((v) => ({ value: v, label: v }))
  }, [list.items, list.language])

  const shown = useMemo(() => filterMedia(list.items, search), [list.items, search])
  const detail = useMemo(
    () => list.items.find((m) => m.media_id === detailId) ?? null,
    [list.items, detailId],
  )

  // 统计头：条目数/合集数/总时长（已加载口径）+ 类型分布
  const stats = useMemo(() => {
    const byType = new Map<string, number>()
    let totalMs = 0
    for (const m of list.items) {
      byType.set(m.media_type, (byType.get(m.media_type) ?? 0) + 1)
      totalMs += m.duration_ms
    }
    const collectionCount = collections ? collections.series.length + collections.courses.length : 0
    return { byType, totalMs, collectionCount }
  }, [list.items, collections])

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Collapse
        items={[
          {
            key: 'mounts',
            label: '媒体来源与扫描',
            children: (
              <>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                  扫描把文件入库：自动补齐时长/兼容性与本地图源海报（sidecar 声明{' '}
                  <Typography.Text code>poster:</Typography.Text> → 同名图片{' '}
                  <Typography.Text code>视频名.jpg</Typography.Text> → 自动截取画面），
                  并按目录结构自动归组合集；未变化的目录整棵跳过，重复扫描很快。
                  身份匹配与 TMDB 海报在「刮削与匹配」页独立运行。
                </Typography.Paragraph>
                <SourcesManager onScanSettled={reloadAll} />
              </>
            ),
          },
        ]}
      />

      <Card size="small" className="media-shell">
        <div className="media-header">
          <div className="media-stats" aria-label="媒体库统计">
            <div className="media-stat">
              <span className="media-stat-num">
                {list.items.length}
                {list.cursor ? '+' : ''}
              </span>
              <span className="media-stat-label">内容{list.cursor ? '（已加载）' : ''}</span>
            </div>
            <div className="media-stat">
              <span className="media-stat-num">{stats.collectionCount}</span>
              <span className="media-stat-label">系列 / 课程合集</span>
            </div>
            <div className="media-stat">
              <span className="media-stat-num">{fmtDurationMs(stats.totalMs)}</span>
              <span className="media-stat-label">总时长（已加载）</span>
            </div>
            <div className="media-stat-wide">
              {[...stats.byType.entries()].map(([t, n]) => (
                <span key={t} className="media-stat-chip">
                  <span className={`media-type-dot t-${t}`} aria-hidden />
                  {mediaTypeLabel(t)} {n}
                </span>
              ))}
            </div>
          </div>
          <div className="media-toolbar">
            <Segmented
              value={list.type ?? ''}
              options={[
                { label: `全部 ${totalLabel(list.typeCounts)}`, value: '' },
                ...Object.entries(list.typeCounts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([t, n]) => ({
                    label: `${TYPE_LABELS[t] ?? t} ${fmtCount(n)}`,
                    value: t,
                  })),
              ]}
              onChange={(v) => list.setType(v === '' ? undefined : v)}
            />
            <Segmented
              value={list.sort}
              size="small"
              options={[
                { label: '最新添加', value: 'added' },
                { label: '标题 A-Z', value: 'title' },
              ]}
              onChange={(v) => list.changeSort(v as 'added' | 'title')}
            />
            <Select
              allowClear
              placeholder="语言"
              style={{ width: 110 }}
              value={list.language}
              options={langOptions}
              onChange={(v) => list.setLanguage(v)}
            />
            <Input
              allowClear
              placeholder="搜索标题 / 角色 / 主题"
              prefix={<SearchOutlined style={{ color: '#b7bcc4' }} />}
              style={{ width: 220 }}
              value={searchInput}
              onChange={(e) => {
                setSearchInput(e.target.value)
                if (e.target.value && view === 'collections') setView('wall')
              }}
            />
            <Segmented
              value={view}
              options={VIEW_OPTIONS}
              onChange={(v) => setView(v as ViewMode)}
            />
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => setAiOpen(true)}
              aria-label="AI 帮我整理"
            >
              AI 帮我整理
            </Button>
            <Button
              size="small"
              onClick={() => setCoverageOpen(true)}
              aria-label="看看还缺什么类型的内容"
            >
              看看还缺什么
            </Button>
          </div>
        </div>

        {view === 'wall' && (
          <MediaGrid
            items={shown}
            loading={list.loading}
            loadingMore={list.loadingMore}
            hasMore={!!list.cursor}
            loadedCount={list.items.length}
            search={search}
            error={list.error}
            onRetry={list.reload}
            onLoadMore={list.loadMore}
            onOpen={(m) => setDetailId(m.media_id)}
          />
        )}
        {view === 'collections' && (
          <CollectionsView
            data={filterCollectionsForType(collections, list.type)}
            error={collectionsError}
            onRetry={reloadCollections}
            onOpenMedia={(m) => setDetailId(m.media_id)}
            onRefresh={reloadAll}
          />
        )}
        {view === 'table' && (
          <MediaListView
            items={shown}
            loading={list.loading}
            onOpen={(m) => setDetailId(m.media_id)}
            onEdit={setEditing}
          />
        )}
        {view === 'table' && list.cursor && (
          <div className="media-grid-footer">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              仅展示已加载的 {list.items.length} 条，切换到海报墙可加载更多
            </Typography.Text>
          </div>
        )}
      </Card>

      <MediaDetail
        media={detail}
        items={list.items}
        error={list.error}
        onClose={() => setDetailId(null)}
        onEdit={setEditing}
        onOpenMedia={(m) => setDetailId(m.media_id)}
      />
      {editing && (
        <MediaEditModal
          media={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            reloadAll()
          }}
        />
      )}
      <AiCurateDrawer open={aiOpen} onClose={() => setAiOpen(false)} onChanged={reloadAll} />
      <Drawer
        title="看看还缺什么类型的内容"
        open={coverageOpen}
        onClose={() => setCoverageOpen(false)}
        width={480}
        destroyOnClose
      >
        <AiAdvisorPanel variant="coverage" />
      </Drawer>
    </Space>
  )
}
