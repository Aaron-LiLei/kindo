import { Badge, Button, Card, Descriptions, Progress, Space, Table, Tag, Typography } from 'antd'
import { CheckCircleFilled, ReloadOutlined, RightOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import { useApi } from '../hooks/useApi'
import type { HealthData } from '../types/admin'
import { ErrorState } from '../components/ErrorState'
import { KindoAiCard } from '../components/KindoAiCard'
import { fmtDateTime, fromNow } from '../utils/format'
import { scanJobMeta } from '../utils/media'

interface SetupItem {
  key: string
  done: boolean
  title: string
  hintDone: string
  hintTodo: string
  to: string
}

/**
 * 首跑引导检查清单：把"从零到孩子能看片"的六步链路（来源→扫描→匹配→
 * 电视配对→模型→规则）钉在概览页——此前六步分散在 5 个页面无任何指引，
 * 首跑家长最可能卡在"不知道下一步去哪"。
 */
function SetupChecklist({ data }: { data: HealthData }) {
  const navigate = useNavigate()
  const hasMedia = (data.media.total ?? 0) > 0
  const items: SetupItem[] = [
    {
      key: 'mounts',
      done: data.media.mounts.length > 0,
      title: '1. 添加媒体来源',
      hintDone: `已添加 ${data.media.mounts.length} 个来源`,
      hintTodo: '在「媒体库 → 媒体来源与扫描」里添加 NAS 目录或本地路径',
      to: '/media',
    },
    {
      key: 'scan',
      done: hasMedia,
      title: '2. 扫描入库',
      hintDone: `库内已有 ${data.media.total} 个内容`,
      hintTodo: '添加来源后点该行的「扫描」（支持增量）',
      to: '/media',
    },
    {
      key: 'pairing',
      done: data.devices.length > 0,
      title: '3. 配对电视',
      hintDone: `已登记 ${data.devices.length} 台设备`,
      hintTodo: '电视端首次连接显示 6 位数字，到「设备」页输入并批准',
      to: '/devices',
    },
    {
      key: 'llm',
      done: data.llm_providers.length > 0 && data.active_model.provider_id != null,
      title: '4. 配置 AI 模型',
      hintDone: data.llm_providers.find(
        (p) => p.provider_id === data.active_model.provider_id,
      )?.display_name ?? '已配置',
      hintTodo: '在「AI 模型」添加 Provider（Base URL + API Key）并设为当前使用',
      to: '/models',
    },
    {
      key: 'match',
      done: (data.media.match_pending ?? 0) === 0,
      title: '5. 确认 TMDB 匹配（可选）',
      hintDone: '没有待确认的匹配',
      hintTodo: `有 ${data.media.match_pending ?? '？'} 个待确认——确认后可获得中文简介与海报`,
      to: '/pipeline',
    },
    {
      key: 'asr',
      done: data.asr.ready,
      title: '6. 检查语音识别（可选）',
      hintDone: `ASR 就绪（${data.asr.model ?? ''}）`,
      hintTodo: 'ASR 未就绪：语音对话不可用（浏览播放不受影响），请检查 kindo-asr 容器',
      to: '/models',
    },
  ]
  const doneCount = items.filter((i) => i.done).length
  if (doneCount === items.length) return null

  return (
    <Card
      size="small"
      title={`开始使用 Kindo（已完成 ${doneCount}/${items.length}）`}
      extra={<Progress type="circle" size={36} percent={Math.round((doneCount / items.length) * 100)} />}
    >
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        {items.map((it) => (
          <button
            key={it.key}
            type="button"
            className="setup-checklist-row"
            onClick={() => navigate(it.to)}
            style={{
              display: 'flex',
              width: '100%',
              alignItems: 'center',
              gap: 10,
              padding: '8px 10px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            {it.done ? (
              <CheckCircleFilled style={{ color: '#52c41a', fontSize: 18 }} />
            ) : (
              <Badge status="default" />
            )}
            <Typography.Text strong={!it.done} delete={false} style={{ fontSize: 13 }}>
              {it.title}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
              {it.done ? it.hintDone : it.hintTodo}
            </Typography.Text>
            <RightOutlined style={{ color: '#999', fontSize: 12 }} />
          </button>
        ))}
      </Space>
    </Card>
  )
}

const jobColumns: ColumnsType<HealthData['media']['latest_jobs'][number]> = [
  {
    title: '来源',
    key: 'mount',
    render: (_, j) => <span title={j.mount_id}>{j.label || j.mount_id}</span>,
  },
  {
    title: '状态',
    dataIndex: 'state',
    key: 'state',
    render: (s: string) => {
      const m = scanJobMeta(s)
      return <Tag color={m.color}>{m.text}</Tag>
    },
  },
  {
    title: '进度',
    dataIndex: 'progress',
    key: 'progress',
    width: 160,
    render: (p: number) => <Progress percent={Math.round(p * 100)} size="small" />,
  },
  {
    title: '失败原因',
    dataIndex: 'error_summary',
    key: 'error_summary',
    render: (s: string | null) => (s ? <Typography.Text type="danger">{s}</Typography.Text> : '—'),
  },
  {
    title: '结束时间',
    dataIndex: 'finished_at',
    key: 'finished_at',
    render: (s: string | null) => fmtDateTime(s),
  },
]

const mountColumns: ColumnsType<HealthData['media']['mounts'][number]> = [
  {
    title: '来源',
    key: 'mount',
    render: (_, m) => <span title={m.mount_id}>{m.label || m.mount_id}</span>,
  },
  {
    title: '健康',
    dataIndex: 'healthy',
    key: 'healthy',
    render: (h: boolean) => <Badge status={h ? 'success' : 'error'} text={h ? '可读' : '不可读'} />,
  },
  {
    title: '只读',
    dataIndex: 'read_only',
    key: 'read_only',
    render: (r: boolean) => (r ? '是' : '否'),
  },
]

const deviceColumns: ColumnsType<HealthData['devices'][number]> = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  {
    title: '在线',
    dataIndex: 'online',
    key: 'online',
    render: (o: boolean) => <Badge status={o ? 'success' : 'default'} text={o ? '在线' : '离线'} />,
  },
  {
    title: '最近活跃',
    dataIndex: 'last_seen_at',
    key: 'last_seen_at',
    render: (s: string | null) => (
      <Typography.Text title={fmtDateTime(s)}>{fromNow(s)}</Typography.Text>
    ),
  },
]

export function HealthPage() {
  const { data, error, loading, loadedAt, reload } = useApi<HealthData>('/api/v1/admin/health', {
    pollMs: 10000,
  })

  if (error && !data) {
    return (
      <Card>
        <ErrorState error={error} onRetry={reload} />
      </Card>
    )
  }
  if (!data) return <Card loading title="服务状态" />

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <SetupChecklist data={data} />
      <KindoAiCard />
      <Card
        title="服务状态"
        extra={
          <Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {loadedAt ? `更新于 ${fmtDateTime(loadedAt)}` : ''}
            </Typography.Text>
            <Button size="small" icon={<ReloadOutlined />} onClick={reload} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      >
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Hub 版本">{data.hub.version}</Descriptions.Item>
          <Descriptions.Item label="数据库">
            <Badge
              status={data.database.ready ? 'success' : 'error'}
              text={data.database.ready ? '正常' : '异常'}
            />
          </Descriptions.Item>
          <Descriptions.Item label="ASR 语音识别">
            <Badge
              status={data.asr.ready ? 'success' : 'warning'}
              text={data.asr.ready ? `就绪（${data.asr.model ?? ''}）` : '降级'}
            />
          </Descriptions.Item>
          <Descriptions.Item label="LLM Provider">
            {data.llm_providers.length === 0 ? (
              <Tag>未配置</Tag>
            ) : (
              <Space wrap size={4}>
                {data.llm_providers.map((p) => (
                  <Tag
                    key={p.provider_id}
                    color={p.provider_id === data.active_model.provider_id ? 'orange' : 'default'}
                  >
                    {p.display_name} · {p.model}
                    {p.provider_id === data.active_model.provider_id ? '（当前）' : ''}
                  </Tag>
                ))}
              </Space>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="最近扫描任务" size="small">
        <Table
          rowKey="id"
          columns={jobColumns}
          dataSource={data.media.latest_jobs}
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无扫描任务' }}
        />
      </Card>

      <Card title="媒体挂载" size="small">
        <Table
          rowKey="mount_id"
          columns={mountColumns}
          dataSource={data.media.mounts}
          pagination={false}
          size="small"
        />
      </Card>

      <Card title="设备" size="small">
        <Table
          rowKey="device_id"
          columns={deviceColumns}
          dataSource={data.devices}
          pagination={{ pageSize: 8, hideOnSinglePage: true, showSizeChanger: false, size: 'small' }}
          size="small"
        />
      </Card>
    </Space>
  )
}
