import { useEffect, useState } from 'react'
import { App as AntApp, Button, Drawer, Empty, Popconfirm, Skeleton, Tag, Typography } from 'antd'
import { SyncOutlined } from '@ant-design/icons'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type {
  CollectionsResp,
  CourseCollection,
  MediaItem,
  SeriesCollection,
} from '../../types/admin'
import { ErrorState } from '../../components/ErrorState'
import { fmtDurationMs, fmtSizeBytes } from '../../utils/format'
import { placeholderGradient, titleInitials } from '../../utils/media'
import { MediaCard } from './MediaCard'

type AnyCollection = SeriesCollection | CourseCollection

/** 合集信息行：语言 · 年龄段 · 时长/体积 · 来源（duration 全 0 时用体积替代） */
function collectionMetaLine(c: AnyCollection): string {
  const parts: string[] = []
  if (c.language) parts.push(c.language)
  if (c.age_band) parts.push(c.age_band)
  if (c.duration_ms > 0) {
    parts.push(`共 ${fmtDurationMs(c.duration_ms)}`)
  } else {
    parts.push(`共 ${fmtSizeBytes(c.size_bytes)}`)
  }
  if (c.mounts.length > 0) parts.push(c.mounts.map((m) => m.label).join(' / '))
  return parts.join(' · ')
}

/** 合集大卡：封面（系列优先 Series poster，v0.3 MED-013）+ 条目数 + 元信息 + 匹配徽章。 */
const MATCH_BADGE: Record<string, { label: string; color: string }> = {
  confirmed: { label: '已确认', color: 'green' },
  auto: { label: '自动匹配', color: 'blue' },
  no_match: { label: '无匹配', color: 'default' },
}

function CollectionCard({
  collection,
  unit,
  onOpen,
}: {
  collection: AnyCollection
  unit: string
  onOpen: () => void
}) {
  const series = collection as SeriesCollection
  const coverSrc =
    series.entity_poster && series.entity_id
      ? adminApi.artworkImageUrl(series.entity_id, 'poster')
      : collection.cover_media_id
        ? adminApi.posterUrl(collection.cover_media_id)
        : undefined
  const badge = series.match_status ? MATCH_BADGE[series.match_status] : undefined
  return (
    <div
      className="collection-card"
      role="button"
      tabIndex={0}
      aria-label={collection.title}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen()
        }
      }}
    >
      <div className="collection-thumb" style={{ background: placeholderGradient(collection.title) }}>
        <img
          src={coverSrc}
          alt=""
          loading="lazy"
          onError={(e) => {
            e.currentTarget.style.display = 'none'
          }}
        />
        <span className="media-initials" style={{ display: 'none' }}>
          {titleInitials(collection.title)}
        </span>
        <span className="collection-count">
          {collection.count} {unit}
        </span>
      </div>
      <div className="collection-info">
        <div className="collection-title" title={collection.title}>
          {collection.title}
        </div>
        <div className="collection-meta">
          {collectionMetaLine(collection)}
          {badge && (
            <Tag color={badge.color} style={{ fontSize: 11, lineHeight: '16px', marginLeft: 6 }}>
              {badge.label}
            </Tag>
          )}
        </div>
        {series.matched_title && (
          <div className="collection-meta" style={{ fontSize: 11 }}>
            TMDB：{series.matched_title}
          </div>
        )}
        {collection.tags.length > 0 && (
          <div className="collection-tags">
            {collection.tags.slice(0, 3).map((t) => (
              <span key={t} className="collection-tag">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/** 点开合集后的抽屉：拉取该合集全部条目（series_id / course_id 精确筛选）。 */
function CollectionDrawer({
  collection,
  onClose,
  onOpenMedia,
}: {
  collection:
    { kind: 'series'; data: SeriesCollection } | { kind: 'course'; data: CourseCollection } | null
  onClose: () => void
  onOpenMedia: (m: MediaItem) => void
}) {
  const [items, setItems] = useState<MediaItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  // 初始即 loading：effect 只在异步回调里收尾，避免 effect 内同步 setState（react-hooks 规则）；
  // 切换合集由父组件 key 重挂载本组件，初始态天然正确
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!collection) return
    let cancelled = false
    const params =
      collection.kind === 'series'
        ? { series_id: collection.data.series_id, limit: 100 }
        : { course_id: collection.data.course_id, limit: 100 }
    adminApi
      .mediaList(params)
      .then((r) => {
        if (cancelled) return
        setItems(r.items)
        setCursor(r.next_cursor)
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
  }, [collection])

  const loadMore = async () => {
    if (!cursor || !collection) return
    const params =
      collection.kind === 'series'
        ? { series_id: collection.data.series_id, cursor, limit: 100 }
        : { course_id: collection.data.course_id, cursor, limit: 100 }
    try {
      const r = await adminApi.mediaList(params)
      setItems((prev) => [...prev, ...r.items])
      setCursor(r.next_cursor)
    } catch (e) {
      setError(formatApiError(e))
    }
  }

  if (!collection) return null
  const seqOf = (m: MediaItem) =>
    m.series
      ? m.series.season_no * 1000 + m.series.episode_no
      : m.course
        ? m.course.chapter_no * 1000 + m.course.lesson_no
        : 0
  const sorted = [...items].sort(
    (a, b) =>
      // 集号相同的双文件（如"精讲+原片"）按标题稳定排序
      seqOf(a) - seqOf(b) || a.title.localeCompare(b.title, 'zh-Hans-CN'),
  )

  return (
    <Drawer open onClose={onClose} width={520} title={collection.data.title}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
        {collectionMetaLine(collection.data)}
      </Typography.Paragraph>
      {error && <ErrorState error={error} onRetry={onClose} />}
      {loading ? (
        <div className="media-grid">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="media-card is-skeleton">
              <Skeleton.Node active className="media-skeleton-thumb" />
              <Skeleton active title={{ width: '80%' }} paragraph={false} />
            </div>
          ))}
        </div>
      ) : sorted.length === 0 && !error ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该合集暂无条目" />
      ) : (
        <>
          <div className="media-grid in-drawer">
            {sorted.map((m) => (
              <MediaCard key={m.media_id} media={m} onOpen={onOpenMedia} />
            ))}
          </div>
          {cursor && (
            <div className="media-grid-footer">
              <Button onClick={loadMore}>加载更多（已加载 {items.length} 条）</Button>
            </div>
          )}
        </>
      )}
    </Drawer>
  )
}

/** 合集浏览视图：系列网格 + 课程网格，点开看全集；头部提供自动归组重建入口。 */
export function CollectionsView({
  data,
  error,
  onRetry,
  onOpenMedia,
  onRefresh,
}: {
  data: CollectionsResp | null
  error: string
  onRetry: () => void
  onOpenMedia: (m: MediaItem) => void
  onRefresh: () => void
}) {
  const { message } = AntApp.useApp()
  const [drawer, setDrawer] = useState<
    { kind: 'series'; data: SeriesCollection } | { kind: 'course'; data: CourseCollection } | null
  >(null)
  const [rebuilding, setRebuilding] = useState(false)

  const rebuild = async () => {
    setRebuilding(true)
    try {
      const r = await adminApi.autoGroupRebuild()
      const grouped = (r.grouped ?? 0) + (r.rebound ?? 0)
      const released = r.released ?? 0
      message.success(
        `重算完成：新归组 ${grouped} 条${released > 0 ? `，解除 ${released} 条` : ''}（共 ${r.processed ?? 0} 条）`,
      )
      onRefresh()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setRebuilding(false)
    }
  }

  if (error && !data) return <ErrorState error={error} onRetry={onRetry} />
  if (!data) {
    return (
      <div className="collection-grid" aria-busy>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="collection-card is-skeleton">
            <Skeleton.Node active className="media-skeleton-thumb" />
            <Skeleton active title={{ width: '60%' }} paragraph={false} />
          </div>
        ))}
      </div>
    )
  }
  if (data.series.length === 0 && data.courses.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <span>
            当前筛选下没有合集
            <br />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              合集 = 系列 / 课程；电影、儿歌、故事是单曲/单片形态——请切换到「海报墙」或「列表」查看。
              扫描时会把同一目录下的多条视频自动归为一个系列。
            </Typography.Text>
          </span>
        }
      />
    )
  }

  return (
    <>
      {data.series.length > 0 && (
        <section className="collection-section">
          <div className="collection-section-head">
            <Typography.Title level={5} className="collection-section-title">
              系列（{data.series.length}）
            </Typography.Title>
            <Popconfirm
              title="按目录结构重算自动归组？"
              description="只处理未声明归组的条目（sidecar / 家长修正不受影响），不访问存储源"
              onConfirm={rebuild}
            >
              <Button size="small" icon={<SyncOutlined />} loading={rebuilding}>
                重算自动归组
              </Button>
            </Popconfirm>
          </div>
          <div className="collection-grid">
            {data.series.map((s) => (
              <CollectionCard
                key={s.series_id}
                collection={s}
                unit="集"
                onOpen={() => setDrawer({ kind: 'series', data: s })}
              />
            ))}
          </div>
        </section>
      )}
      {data.courses.length > 0 && (
        <section className="collection-section">
          <div className="collection-section-head">
            <Typography.Title level={5} className="collection-section-title">
              课程（{data.courses.length}）
            </Typography.Title>
          </div>
          <div className="collection-grid">
            {data.courses.map((c) => (
              <CollectionCard
                key={c.course_id}
                collection={c}
                unit="课"
                onOpen={() => setDrawer({ kind: 'course', data: c })}
              />
            ))}
          </div>
        </section>
      )}
      <CollectionDrawer
        key={
          drawer
            ? drawer.kind === 'series'
              ? `s-${drawer.data.series_id}`
              : `c-${drawer.data.course_id}`
            : 'none'
        }
        collection={drawer}
        onClose={() => setDrawer(null)}
        onOpenMedia={(m) => {
          setDrawer(null) // 点开单条详情时收起合集抽屉，避免双层叠加
          onOpenMedia(m)
        }}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
        合集由扫描时按目录结构自动归组（同名目录 ≥2 条视频成系列）；在条目详情里修正归组后以家长修正为准。
      </Typography.Text>
    </>
  )
}
