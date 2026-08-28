import { Badge, Button, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { adminApi } from '../../api/admin'
import type { MediaItem } from '../../types/admin'
import { fmtDurationMs, fmtSizeBytes } from '../../utils/format'
import {
  mediaSeed,
  mediaTypeLabel,
  placeholderGradient,
  sequenceLabel,
  titleInitials,
} from '../../utils/media'

/** 列表视图：信息密度高，适合逐条核对元数据。 */
export function MediaListView({
  items,
  loading,
  onOpen,
  onEdit,
}: {
  items: MediaItem[]
  loading: boolean
  onOpen: (m: MediaItem) => void
  onEdit: (m: MediaItem) => void
}) {
  const columns: ColumnsType<MediaItem> = [
    {
      title: '',
      key: 'thumb',
      width: 72,
      render: (_, m) => (
        <div
          className="media-row-thumb"
          style={{ background: placeholderGradient(mediaSeed(m)) }}
          aria-hidden
        >
          {m.has_poster ? (
            <img
              src={adminApi.posterUrl(m.media_id)}
              alt=""
              loading="lazy"
              onError={(e) => {
                e.currentTarget.style.display = 'none'
              }}
            />
          ) : (
            <span>{titleInitials(mediaSeed(m))}</span>
          )}
        </div>
      ),
    },
    {
      title: '来源',
      dataIndex: 'mount_label',
      key: 'mount_label',
      width: 130,
      ellipsis: true,
      render: (_, m) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
          {m.mount_label ?? m.mount_id.slice(0, 12)}
        </Typography.Text>
      ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      render: (_, m) => (
        <Space size={6} wrap>
          <button type="button" className="media-row-title" onClick={() => onOpen(m)}>
            {m.title}
          </button>
          {sequenceLabel(m) && <Tag>{sequenceLabel(m)}</Tag>}
          {m.parent_edited && <Tag color="orange">已修正 v{m.metadata_version}</Tag>}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'media_type',
      key: 'media_type',
      width: 90,
      render: (t: string) => <Tag>{mediaTypeLabel(t)}</Tag>,
    },
    {
      title: '所属合集',
      key: 'collection',
      width: 150,
      ellipsis: true,
      render: (_, m) =>
        (m.series?.title ?? m.course?.title ?? '—') +
        (m.auto_grouped ? '（自动）' : ''),
    },
    {
      title: '语言',
      dataIndex: 'language',
      key: 'language',
      width: 90,
      render: (l: string | null) => l ?? '—',
    },
    {
      title: '时长',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 110,
      render: (ms: number) => fmtDurationMs(ms),
    },
    {
      title: '体积',
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      width: 90,
      render: (b: number) => fmtSizeBytes(b),
    },
    {
      title: '角色 / 主题',
      key: 'tags',
      render: (_, m) => (
        <Space wrap size={4}>
          {(m.tags.characters ?? []).map((c) => (
            <Tag key={`c-${c}`} color="blue">
              {c}
            </Tag>
          ))}
          {(m.tags.themes ?? []).map((t) => (
            <Tag key={`t-${t}`} color="green">
              {t}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: (_, m) =>
        m.missing ? (
          <Badge status="warning" text="文件缺失" />
        ) : m.playable ? (
          <Badge status="success" text="可播放" />
        ) : (
          <Badge status="error" text="不兼容" />
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_, m) => (
        <Button size="small" onClick={() => onEdit(m)}>
          修正元数据
        </Button>
      ),
    },
  ]

  return (
    <>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        目录级 <Typography.Text code>kindo.yaml</Typography.Text> 与文件级{' '}
        <Typography.Text code>视频名.kindo.yaml</Typography.Text>{' '}
        为元数据来源；家长修正过的字段不会被重新扫描覆盖。
      </Typography.Paragraph>
      <Table
        rowKey="media_id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={false}
        size="small"
        scroll={{ x: 1160 }}
        locale={{ emptyText: '暂无内容。请先添加挂载并触发扫描。' }}
      />
    </>
  )
}
