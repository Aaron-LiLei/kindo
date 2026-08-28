import { useState } from 'react'
import { App as AntApp, Button, Card, Popconfirm, Space, Tag, Typography } from 'antd'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import { useApi } from '../hooks/useApi'

/** 语音识别热词（ASR-005）：从媒体库自动构建，提升片名/角色识别。 */
export function HotwordsCard() {
  const { data, error, loading, reload } = useApi<{
    path: string
    exists: boolean
    count?: number
    sample?: string[]
    updated_at?: number
    note?: string
  }>('/api/v1/admin/asr/hotwords')
  const [busy, setBusy] = useState(false)
  const { message } = AntApp.useApp()

  const rebuild = async () => {
    setBusy(true)
    try {
      const r = await adminApi.hotwordsRebuild()
      message.success(`已从媒体库重建热词表（${r.count} 词${r.manual_count ? `，保留 ${r.manual_count} 条手工词` : ''}）`)
      reload()
    } catch (e) {
      message.error(formatApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="语音识别热词（孩子点播更准）"
      size="small"
      extra={
        <Popconfirm
          title="从媒体库重建热词表？"
          description="系列名/角色/主题将重新收集；文件中手工补写的词条会保留。重建后需重启 kindo-asr 容器生效。"
          onConfirm={rebuild}
          okText="重建"
          cancelText="取消"
        >
          <Button size="small" loading={busy}>
            从媒体库重建
          </Button>
        </Popconfirm>
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        热词帮助语音识别优先命中家里的内容名（系列、角色、主题）。首次启动会自动生成；
        添加新内容后可在这里重建。
      </Typography.Paragraph>
      {error ? (
        <Typography.Text type="danger">{formatApiError(error)}</Typography.Text>
      ) : loading ? (
        <Card loading style={{ border: 'none' }} />
      ) : data?.exists ? (
        <Space direction="vertical" size={4}>
          <Space size={8} wrap>
            <Tag color="green">已生成 {data.count} 词</Tag>
            {data.updated_at && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                更新于 {new Date(data.updated_at * 1000).toLocaleString()}
              </Typography.Text>
            )}
          </Space>
          {data.sample && data.sample.length > 0 && (
            <Space size={4} wrap>
              {data.sample.slice(0, 10).map((w) => (
                <Tag key={w}>{w}</Tag>
              ))}
              {(data.count ?? 0) > 10 && <Tag bordered={false}>…</Tag>}
            </Space>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {data.note}（文件：{data.path}）
          </Typography.Text>
        </Space>
      ) : (
        <Typography.Text type="secondary">尚未生成——点"从媒体库重建"创建。</Typography.Text>
      )}
    </Card>
  )
}
