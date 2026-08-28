import { useRef, useState } from 'react'
import { App as AntApp, Button, Popconfirm, Space, Switch, Tag, Typography } from 'antd'
import { DeleteOutlined, UploadOutlined } from '@ant-design/icons'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import type { ArtworkItem } from '../../types/admin'

/** Artwork 管理面板（v0.3 ADM-013 / 决策八）：按 kind 上传/换图/锁定/删除。
 * 家长上传的图永不被刮削刷新覆盖；锁定后任何来源都不覆盖。 */
const KIND_META: Record<string, { label: string; hint: string }> = {
  poster: { label: '海报 poster', hint: '系列卡使用 Series poster（首页/合集）' },
  backdrop: { label: '背景图 backdrop', hint: '详情页大图' },
  thumbnail: { label: '缩略图 thumbnail', hint: '剧集列表的集卡图' },
  logo: { label: '标徽 logo', hint: '标题标识图' },
}

export function ArtworkPanel({ mediaId }: { mediaId: string }) {
  const entityQ = useApi<{ entity: { entity_id: string } | null }>(
    `/api/v1/admin/content/by-media/${mediaId}`,
  )
  const entityId = entityQ.data?.entity?.entity_id ?? null
  const { data, error, reload } = useApi<{ items: ArtworkItem[] }>(
    entityId ? `/api/v1/admin/content/${entityId}/artwork` : null,
  )
  const { message } = AntApp.useApp()
  const [busyKind, setBusyKind] = useState<string | null>(null)

  if (entityQ.data && !entityId) return null
  if (!entityId || entityQ.error) return null

  const onUpload = async (kind: string, file: File) => {
    setBusyKind(kind)
    try {
      await adminApi.artworkUpload(entityId, kind, file)
      message.success(`${KIND_META[kind]?.label ?? kind} 已更新（家长级，刷新不覆盖）`)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyKind(null)
    }
  }
  const onLock = async (kind: string, locked: boolean) => {
    setBusyKind(kind)
    try {
      await adminApi.artworkLock(entityId, kind, locked)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyKind(null)
    }
  }
  const onDelete = async (kind: string) => {
    setBusyKind(kind)
    try {
      await adminApi.artworkDelete(entityId, kind)
      message.success('已删除')
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusyKind(null)
    }
  }

  return (
    <div>
      <Typography.Title level={5} style={{ marginTop: 4 }}>
        Artwork（poster / backdrop / thumbnail / logo）
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        家长上传的图永远优先；「锁定」后连刮削刷新也不会碰。仅支持 jpg / png / webp（≤8MB）。
      </Typography.Paragraph>
      {error ? (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          加载失败：{error}
        </Typography.Text>
      ) : (
        <Space wrap size={16}>
          {(data?.items ?? []).map((it) => (
            <ArtworkCard
              key={it.kind}
              item={it}
              entityId={entityId}
              busy={busyKind === it.kind}
              onUpload={(f) => onUpload(it.kind, f)}
              onLock={(l) => onLock(it.kind, l)}
              onDelete={() => onDelete(it.kind)}
            />
          ))}
        </Space>
      )}
    </div>
  )
}

function ArtworkCard({
  item,
  entityId,
  busy,
  onUpload,
  onLock,
  onDelete,
}: {
  item: ArtworkItem
  entityId: string
  busy: boolean
  onUpload: (f: File) => void
  onLock: (locked: boolean) => void
  onDelete: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const meta = KIND_META[item.kind] ?? { label: item.kind, hint: '' }
  return (
    <div
      style={{
        width: 150,
        border: '1px solid #e5e7eb',
        borderRadius: 8,
        padding: 8,
        textAlign: 'center',
      }}
    >
      <div
        style={{
          width: '100%',
          height: 120,
          background: '#f3f4f6',
          borderRadius: 6,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
          marginBottom: 6,
        }}
      >
        {item.exists ? (
          <img
            src={adminApi.artworkImageUrl(entityId, item.kind)}
            alt={meta.label}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            无图
          </Typography.Text>
        )}
      </div>
      <Typography.Text strong style={{ fontSize: 12 }}>
        {meta.label}
      </Typography.Text>
      <div style={{ fontSize: 11, color: '#888', margin: '2px 0 6px' }}>{meta.hint}</div>
      {item.exists && item.source && (
        <Tag style={{ fontSize: 11, marginBottom: 6 }}>
          {item.source === 'parent' ? '家长' : item.source === 'provider' ? 'Provider' : item.source}
        </Tag>
      )}
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Button
          size="small"
          block
          icon={<UploadOutlined />}
          loading={busy}
          onClick={() => inputRef.current?.click()}
        >
          {item.exists ? '换图' : '上传'}
        </Button>
        {item.exists && (
          <Space size={4} style={{ justifyContent: 'space-between', width: '100%' }}>
            <span style={{ fontSize: 12 }}>锁定</span>
            <Switch size="small" checked={item.locked} onChange={onLock} />
            <Popconfirm title="删除该图？" onConfirm={onDelete}>
              <Button size="small" type="text" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        )}
      </Space>
      {/* 隐藏的原生文件选择（Upload.Dragger 过重，卡片场景够用） */}
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onUpload(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}
