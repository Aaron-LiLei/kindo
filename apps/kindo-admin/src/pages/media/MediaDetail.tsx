import { Badge, Button, Descriptions, Drawer, Empty, Popconfirm, Space, Tag, Typography } from 'antd'
import { EditOutlined } from '@ant-design/icons'
import { App as AntApp } from 'antd'
import { useState } from 'react'
import { adminApi } from '../../api/admin'
import { useApi } from '../../hooks/useApi'
import { formatApiError } from '../../api/client'
import type { MediaItem } from '../../types/admin'
import { ErrorState } from '../../components/ErrorState'
import { CanonicalPanel } from './CanonicalPanel'
import { ArtworkPanel } from './ArtworkPanel'
import {
  collectionSiblings,
  compactDuration,
  mediaSeed,
  mediaTypeLabel,
  placeholderGradient,
  sequenceLabel,
  titleInitials,
} from '../../utils/media'
import { fmtDurationMs, fmtSizeBytes } from '../../utils/format'

const CLASS_LABEL: Record<string, string> = {
  ENTERTAINMENT: '娱乐',
  LEARNING: '学习',
  STORY: '故事',
  MUSIC: '音乐',
  OTHER: '其他',
}
const MODALITY_LABEL: Record<string, string> = {
  VIDEO: '视频',
  AUDIO: '音频',
  OFFSCREEN: '离屏',
}

/** 媒体详情抽屉：完整元数据 + 标签 + 同合集其他集 + 修正入口。 */
export function MediaDetail({
  media,
  items,
  error,
  onClose,
  onEdit,
  onOpenMedia,
}: {
  media: MediaItem | null
  items: MediaItem[]
  error: string
  onClose: () => void
  onEdit: (m: MediaItem) => void
  onOpenMedia: (m: MediaItem) => void
}) {
  if (!media) return null
  const siblings = collectionSiblings(items, media)
  const seq = sequenceLabel(media)

  return (
    <Drawer
      open
      onClose={onClose}
      width={440}
      title={
        <Space size={8} wrap>
          <span>{media.title}</span>
          {seq && <Tag color="orange">{seq}</Tag>}
        </Space>
      }
    >
      {error && <ErrorState error={error} />}
      <div
        className="media-detail-thumb"
        style={{ background: placeholderGradient(mediaSeed(media)) }}
      >
        {media.has_poster ? (
          <img
            src={adminApi.posterUrl(media.media_id)}
            alt=""
            onError={(e) => {
              e.currentTarget.style.display = 'none'
            }}
          />
        ) : (
          <span className="media-initials">{titleInitials(mediaSeed(media))}</span>
        )}
        {compactDuration(media.duration_ms) && (
          <span className="media-duration">{compactDuration(media.duration_ms)}</span>
        )}
      </div>

      <Button
        type="primary"
        icon={<EditOutlined />}
        block
        style={{ marginTop: 16 }}
        onClick={() => onEdit(media)}
      >
        修正元数据
      </Button>

      <div style={{ marginTop: 20 }}>
        <CanonicalPanel mediaId={media.media_id} />
      </div>
      <div style={{ marginTop: 20 }}>
        <ArtworkPanel mediaId={media.media_id} />
      </div>
      {media.entity_id && <AssetVersionsPanel entityId={media.entity_id} />}

      <Descriptions
        column={1}
        size="small"
        bordered
        style={{ marginTop: 16 }}
        items={[
          { key: 'type', label: '类型', children: mediaTypeLabel(media.media_type) },
          ...(media.content_class
            ? [
                {
                  key: 'class',
                  label: '内容分类',
                  children: (
                    <Tag color={media.content_class === 'LEARNING' ? 'green' : 'orange'}>
                      {CLASS_LABEL[media.content_class] ?? media.content_class}
                    </Tag>
                  ),
                },
              ]
            : []),
          ...(media.modality
            ? [
                {
                  key: 'modality',
                  label: '媒介',
                  children: (
                    <Tag color={media.modality === 'AUDIO' ? 'purple' : 'blue'}>
                      {MODALITY_LABEL[media.modality] ?? media.modality}
                    </Tag>
                  ),
                },
              ]
            : []),
          { key: 'lang', label: '语言', children: media.language ?? '—' },
          { key: 'age', label: '年龄段', children: media.age_band ?? '—' },
          { key: 'dur', label: '时长', children: fmtDurationMs(media.duration_ms) },
          { key: 'size', label: '体积', children: fmtSizeBytes(media.size_bytes) },
          ...(media.series
            ? [
                {
                  key: 'series',
                  label: '所属系列',
                  children: (
                    <Space size={6} wrap>
                      <span>{media.series.title}</span>
                      {media.auto_grouped && (
                        <Tag bordered={false} color="processing">
                          自动归组
                        </Tag>
                      )}
                    </Space>
                  ),
                },
              ]
            : []),
          ...(media.course
            ? [{ key: 'course', label: '所属课程', children: media.course.title }]
            : []),
          {
            key: 'status',
            label: '状态',
            children: media.missing ? (
              <Badge status="warning" text="文件缺失" />
            ) : media.playable ? (
              <Badge status="success" text="可播放" />
            ) : (
              <Badge status="error" text="不兼容" />
            ),
          },
          ...(media.mount_label
            ? [{ key: 'mount', label: '来源', children: media.mount_label }]
            : []),
          {
            key: 'path',
            label: '库内路径',
            children: (
              <Typography.Text code style={{ wordBreak: 'break-all', fontSize: 12 }}>
                {media.path_key}
              </Typography.Text>
            ),
          },
        ]}
      />

      {media.auto_grouped && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
          该合集由目录结构自动归组；在此修正归组后以家长修正为准，重扫不会被覆盖。
        </Typography.Paragraph>
      )}

      {(media.tags.characters?.length || media.tags.themes?.length || media.tags.tags?.length) && (
        <>
          <Typography.Title level={5} style={{ marginTop: 20 }}>
            角色与主题
          </Typography.Title>
          <Space wrap size={4}>
            {(media.tags.characters ?? []).map((c) => (
              <Tag key={`c-${c}`} color="blue">
                {c}
              </Tag>
            ))}
            {(media.tags.themes ?? []).map((t) => (
              <Tag key={`t-${t}`} color="green">
                {t}
              </Tag>
            ))}
            {(media.tags.tags ?? []).map((t) => (
              <Tag key={`g-${t}`}>{t}</Tag>
            ))}
          </Space>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
            这些标签供 AI 语义检索（如“天天”“海洋”），修正后不会被重新扫描覆盖。
          </Typography.Paragraph>
        </>
      )}

      {siblings.length > 0 && (
        <>
          <Typography.Title level={5} style={{ marginTop: 24 }}>
            {media.series ? '本系列其他集' : '本课程其他课'}（{siblings.length}）
          </Typography.Title>
          <div className="media-sibling-list">
            {siblings.map((s) => (
              <button
                key={s.media_id}
                type="button"
                className="media-sibling-row"
                onClick={() => onOpenMedia(s)}
              >
                <span className="media-seq">{sequenceLabel(s) ?? '—'}</span>
                <span className="media-sibling-title">{s.title}</span>
                <span className="media-sibling-duration">
                  {compactDuration(s.duration_ms) ?? '—'}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
      {siblings.length === 0 && (media.series || media.course) && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="同合集其他集尚未加载（超过已加载数量）"
          style={{ marginTop: 24 }}
        />
      )}
    </Drawer>
  )
}

/** 文件版本（PLY-009）：同一集存在多个文件版本时选择首选版本。
 *  非首选版本在 TV 端浏览/检索中隐藏，默认播放首选。 */
function AssetVersionsPanel({ entityId }: { entityId: string }) {
  const { data, error, reload } = useApi<{
    entity_id: string
    entity_title: string
    assets: import('../../types/admin').EntityAssetRow[]
  }>(`/api/v1/admin/content/${entityId}/assets`)
  const [busy, setBusy] = useState<string | null>(null)
  const { message } = AntApp.useApp()

  if (error) return null
  const assets = data?.assets
  if (!assets || assets.length <= 1) return null  // 单版本不展示（绝大多数内容）

  const setPreferred = async (assetId: string) => {
    setBusy(assetId)
    try {
      await adminApi.setPreferredAsset(entityId, assetId)
      message.success('已设为首选版本（TV 端浏览与播放默认使用）')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <Typography.Title level={5} style={{ marginTop: 20 }}>
        文件版本（{assets.length}）
      </Typography.Title>
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        {assets.map((a) => (
          <div
            key={a.asset_id}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              border: '1px solid #f0f0f0', borderRadius: 6, padding: '6px 10px',
            }}
          >
            {a.role === 'PRIMARY_VIDEO' ? (
              <Tag color="green">首选</Tag>
            ) : (
              <Tag>备选</Tag>
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <Typography.Text ellipsis={{ tooltip: a.path_key }} style={{ fontSize: 12 }}>
                {a.path_key}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                {(a.size_bytes / 1024 / 1024).toFixed(0)} MB{a.missing ? ' · 文件缺失' : ''}
              </Typography.Text>
            </div>
            {a.role !== 'PRIMARY_VIDEO' && (
              <Popconfirm
                title="设为首选版本？"
                description="TV 端将默认浏览与播放该版本。"
                okText="设为首选"
                cancelText="取消"
                onConfirm={() => setPreferred(a.asset_id)}
              >
                <Button size="small" loading={busy === a.asset_id}>
                  设为首选
                </Button>
              </Popconfirm>
            )}
          </div>
        ))}
      </Space>
    </>
  )
}
