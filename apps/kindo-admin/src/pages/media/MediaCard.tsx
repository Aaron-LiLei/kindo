import { memo } from 'react'
import { adminApi } from '../../api/admin'
import type { MediaItem } from '../../types/admin'
import { compactSize } from '../../utils/format'
import {
  compactDuration,
  mediaSeed,
  mediaTypeLabel,
  placeholderGradient,
  sequenceLabel,
  titleInitials,
} from '../../utils/media'

/**
 * 海报卡片：缩略图（海报 / 确定性渐变占位）+ 状态角标 + 时长/集号徽章。
 * 信息区三行：标题 / 所属合集（无则并入类型行）/ 类型·语言·体积。
 * 点击整卡打开详情抽屉；键盘可达（role=button + Enter/Space）。
 */
export const MediaCard = memo(function MediaCard({
  media,
  onOpen,
}: {
  media: MediaItem
  onOpen: (m: MediaItem) => void
}) {
  const seq = sequenceLabel(media)
  const duration = compactDuration(media.duration_ms)
  const size = compactSize(media.size_bytes)
  const collectionTitle = media.series?.title ?? media.course?.title ?? null
  return (
    <div
      className="media-card"
      role="button"
      tabIndex={0}
      aria-label={media.title}
      onClick={() => onOpen(media)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(media)
        }
      }}
    >
      <div className="media-thumb" style={{ background: placeholderGradient(mediaSeed(media)) }}>
        <span className="media-initials" style={{ display: 'none' }}>
          {titleInitials(mediaSeed(media))}
        </span>
        <img
          src={adminApi.posterUrl(media.media_id)}
          alt=""
          loading="lazy"
          onError={(e) => {
            e.currentTarget.style.display = 'none'
            const el = e.currentTarget.previousElementSibling as HTMLElement | null
            if (el) el.style.display = ''
          }}
        />
        {media.missing && <span className="media-flag is-missing">文件缺失</span>}
        {!media.missing && !media.playable && (
          <span className="media-flag is-incompatible">不兼容</span>
        )}
        {duration ? (
          <span className="media-duration">{duration}</span>
        ) : (
          size && <span className="media-duration">{size}</span>
        )}
        {seq && <span className="media-seq">{seq}</span>}
      </div>
      <div className="media-info">
        <div className="media-title" title={media.title}>
          {media.title}
        </div>
        {collectionTitle && (
          <div className="media-series" title={collectionTitle}>
            {collectionTitle}
          </div>
        )}
        <div className="media-meta">
          <span className={`media-type-dot t-${media.media_type}`} aria-hidden />
          {mediaTypeLabel(media.media_type)}
          {media.language ? ` · ${media.language}` : ''}
          {media.parent_edited && <span className="media-edited">已修正</span>}
        </div>
      </div>
    </div>
  )
})
