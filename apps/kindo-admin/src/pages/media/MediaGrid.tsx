import { Button, Empty, Skeleton, Typography } from 'antd'
import type { MediaItem } from '../../types/admin'
import { ErrorState } from '../../components/ErrorState'
import { MediaCard } from './MediaCard'

/** 响应式海报卡片墙：骨架屏、空态引导、cursor 加载更多。 */
export function MediaGrid({
  items,
  loading,
  loadingMore,
  hasMore,
  loadedCount,
  search,
  error,
  onRetry,
  onLoadMore,
  onOpen,
}: {
  items: MediaItem[]
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  loadedCount: number
  search: string
  error: string
  onRetry: () => void
  onLoadMore: () => void
  onOpen: (m: MediaItem) => void
}) {
  if (error && items.length === 0) {
    return <ErrorState error={error} onRetry={onRetry} />
  }
  if (loading && items.length === 0) {
    return (
      <div className="media-grid" aria-busy>
        {Array.from({ length: 10 }, (_, i) => (
          <div key={i} className="media-card is-skeleton">
            <Skeleton.Node active className="media-skeleton-thumb" />
            <Skeleton active title={{ width: '80%' }} paragraph={false} />
          </div>
        ))}
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          search ? (
            <span>
              没有匹配「{search}」的内容
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                搜索范围是已加载的条目，可点击下方“加载更多”扩大范围
              </Typography.Text>
            </span>
          ) : (
            <span>
              媒体库还是空的
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                先在“挂载与扫描”里添加媒体目录并触发扫描
              </Typography.Text>
            </span>
          )
        }
      />
    )
  }
  return (
    <>
      <div className="media-grid">
        {items.map((m) => (
          <MediaCard key={m.media_id} media={m} onOpen={onOpen} />
        ))}
      </div>
      <div className="media-grid-footer">
        {hasMore ? (
          <Button loading={loadingMore} onClick={onLoadMore}>
            加载更多（已加载 {loadedCount} 条）
          </Button>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            共 {loadedCount} 条{search && `，匹配 ${items.length} 条`}
          </Typography.Text>
        )}
      </div>
    </>
  )
}
