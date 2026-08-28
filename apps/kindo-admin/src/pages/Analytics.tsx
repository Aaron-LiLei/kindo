import { useState } from 'react'
import { AiAdvisorPanel } from '../components/AiAdvisorPanel'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Empty,
  List,
  Progress,
  Segmented,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { useApi } from '../hooks/useApi'
import type { AnalyticsData, InterestAnalytics } from '../types/admin'
import { ErrorState } from '../components/ErrorState'
import { fmtWatchSeconds } from '../utils/format'

interface ShareRow {
  key: string
  label: string
  seconds: number
  percent: number
}

const MODALITY_LABEL: Record<string, string> = {
  VIDEO: '视频',
  AUDIO: '音频',
  AI_VOICE: 'AI 语音',
  OFFSCREEN: '离屏',
  unknown: '未知媒介',
}
const CLASS_LABEL: Record<string, string> = {
  ENTERTAINMENT: '娱乐',
  LEARNING: '学习',
  STORY: '故事',
  MUSIC: '音乐',
  OTHER: '其他',
  unknown: '未分类',
}

/** 后端维度键 → 中文标签（v0.3 正交维度展示，ANA-002） */
function labelize(map: Record<string, number>, labels: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(map)) out[labels[k] ?? k] = v
  return out
}

function toShareRows(map: Record<string, number>, total: number): ShareRow[] {
  return Object.entries(map)
    .map(([k, v]) => ({
      key: k,
      label: k,
      seconds: v,
      percent: total > 0 ? Math.round((v / total) * 100) : 0,
    }))
    .sort((a, b) => b.seconds - a.seconds)
}

function ShareBars({ title, rows }: { title: string; rows: ShareRow[] }) {
  if (rows.length === 0) return null
  return (
    <Card size="small" title={title}>
      {rows.map((r) => (
        <div key={r.key} style={{ marginBottom: 12 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 13,
              marginBottom: 2,
            }}
          >
            <span>{r.label}</span>
            <Typography.Text type="secondary">
              {fmtWatchSeconds(r.seconds)}（{r.percent}%）
            </Typography.Text>
          </div>
          <Progress percent={r.percent} showInfo={false} size="small" />
        </div>
      ))}
    </Card>
  )
}

export function AnalyticsPage() {
  return (
    <>
      <AiAdvisorPanel variant="summary" />
      <InterestPanel />
      <LegacyAnalytics />
    </>
  )
}

/** 今日 / 近7天 / 自定义日期范围（P2：统计不再只有两档）。 */
type RangeMode = 'day' | 'week' | 'custom'

function rangeQuery(mode: RangeMode, range: [Dayjs | null, Dayjs | null] | null): string {
  if (mode !== 'custom' || !range?.[0] || !range?.[1]) return `period=${mode}`
  return `period=custom&start=${range[0].format('YYYY-MM-DD')}&end=${range[1].format('YYYY-MM-DD')}`
}

function RangePickerExtra({
  mode,
  setMode,
  range,
  setRange,
}: {
  mode: RangeMode
  setMode: (m: RangeMode) => void
  range: [Dayjs | null, Dayjs | null] | null
  setRange: (r: [Dayjs | null, Dayjs | null] | null) => void
}) {
  return (
    <Space size={8}>
      <DatePicker.RangePicker
        value={range}
        onChange={(v) => {
          setRange(v as [Dayjs | null, Dayjs | null] | null)
          if (v?.[0] && v?.[1]) setMode('custom')
        }}
        allowEmpty={[false, false]}
      />
      <Segmented
        value={mode}
        onChange={(v) => setMode(v as RangeMode)}
        options={[
          { label: '今日', value: 'day' },
          { label: '近 7 天', value: 'week' },
          { label: '自定义', value: 'custom', disabled: !(range?.[0] && range?.[1]) },
        ]}
      />
    </Space>
  )
}

/** v0.3 兴趣信号与接力观测（ANA-007/008）：只读客观行为。 */
function InterestPanel() {
  const [mode, setMode] = useState<RangeMode>('week')
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const { data, error, reload } = useApi<InterestAnalytics>(
    `/api/v1/admin/analytics/interest?${rangeQuery(mode, range)}`,
  )
  return (
    <Card
      title="兴趣信号与成长接力"
      extra={
        <RangePickerExtra mode={mode} setMode={setMode} range={range} setRange={setRange} />
      }
      style={{ marginBottom: 24 }}
    >
      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? null : (
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <Space wrap size={32}>
            <Statistic title="接力发起" value={data.transition.total} />
            <Statistic title="接受" value={data.transition.accepted} />
            <Statistic title="拒绝" value={data.transition.rejected} />
            <Statistic
              title="平均互动时长"
              value={data.transition.avg_ai_voice_seconds}
              suffix="秒"
            />
          </Space>
          {data.top_topics.length > 0 && (
            <div>
              <Typography.Text strong>常接触主题</Typography.Text>
              <div style={{ marginTop: 8 }}>
                <Space wrap>
                  {data.top_topics.map((t) => (
                    <Tag key={t.topic} color="blue">
                      {t.topic} × {t.count}
                    </Tag>
                  ))}
                </Space>
              </div>
            </div>
          )}
          {data.top_entities.length > 0 && (
            <div>
              <Typography.Text strong>常接触内容</Typography.Text>
              <List
                size="small"
                dataSource={data.top_entities.slice(0, 5)}
                renderItem={(it) => (
                  <List.Item>
                    {it.title}（{it.count} 次 · 最近 {it.last_at.slice(0, 10)}）
                  </List.Item>
                )}
              />
            </div>
          )}
          <Typography.Text type="secondary">
            仅记录客观行为（接触/提问/选择/接力参与），不产生任何能力或心理推断（ANA-005）。
          </Typography.Text>
        </Space>
      )}
    </Card>
  )
}

function LegacyAnalytics() {
  const [mode, setMode] = useState<RangeMode>('day')
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const { data, error, loading, reload } = useApi<AnalyticsData>(
    `/api/v1/admin/analytics?${rangeQuery(mode, range)}`,
  )

  if (error && !data) {
    return (
      <Card title="观看统计">
        <ErrorState error={error} onRetry={reload} />
      </Card>
    )
  }
  if (!data) return <Card loading title="观看统计" />

  const typeRows = toShareRows(data.by_media_type, data.total_watched_seconds)
  const langRows = toShareRows(data.by_language, data.total_watched_seconds)
  const modalityRows = toShareRows(
    labelize(data.by_modality ?? {}, MODALITY_LABEL),
    data.total_watched_seconds,
  )
  const classRows = toShareRows(
    labelize(data.by_content_class ?? {}, CLASS_LABEL),
    data.total_watched_seconds,
  )

  const topMediaColumns: ColumnsType<AnalyticsData['top_media'][number]> = [
    { title: '标题', dataIndex: 'title', key: 'title' },
    {
      title: '类型',
      dataIndex: 'media_type',
      key: 'media_type',
      render: (t: string) => <Tag>{t}</Tag>,
    },
    {
      title: '时长',
      dataIndex: 'watched_seconds',
      key: 'watched_seconds',
      render: (s: number) => fmtWatchSeconds(s),
    },
  ]

  const recordColumns: ColumnsType<NonNullable<AnalyticsData['recent_records']>[number]> = [
    {
      title: '内容',
      dataIndex: 'title',
      key: 'title',
      render: (t: string, r) => (
        <Space size={6} wrap>
          <span>{t}</span>
          <Tag>{MODALITY_LABEL[r.modality ?? ''] ?? r.modality ?? r.media_type}</Tag>
          {r.content_class && <Tag color="green">{CLASS_LABEL[r.content_class] ?? r.content_class}</Tag>}
        </Space>
      ),
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      render: (s: string) => new Date(s).toLocaleString('zh-CN', { hour12: false }),
    },
    {
      title: '实际观看',
      dataIndex: 'watched_seconds',
      key: 'watched_seconds',
      render: (s: number) => fmtWatchSeconds(s),
    },
    {
      title: '完成',
      dataIndex: 'completed',
      key: 'completed',
      render: (c: boolean) => (c ? <Tag color="green">看完</Tag> : <Tag>部分</Tag>),
    },
  ]

  /** 观看记录导出 CSV（BOM 头保证 Excel 中文不乱码）。 */
  const exportCsv = () => {
    const rows = data?.recent_records ?? []
    if (rows.length === 0) return
    const header = '内容,媒介,内容分类,开始时间,实际观看秒,是否看完'
    const lines = rows.map((r) => [
      `"${r.title.replace(/"/g, '""')}"`,
      r.modality ?? r.media_type,
      r.content_class ?? '',
      r.started_at,
      String(r.watched_seconds),
      r.completed ? '是' : '否',
    ].join(','))
    const blob = new Blob(['\uFEFF' + header + '\n' + lines.join('\n')], {
      type: 'text/csv;charset=utf-8',
    })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `kindo-观看记录-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="观看统计"
        extra={
          <RangePickerExtra mode={mode} setMode={setMode} range={range} setRange={setRange} />
        }
      >
        {data.total_watched_seconds === 0 ? (
          <Empty description={mode === 'day' ? '今天还没有观看记录' : '所选范围还没有观看记录'} />
        ) : (
          <Statistic title="总观看时长" value={fmtWatchSeconds(data.total_watched_seconds)} />
        )}
      </Card>

      {data.total_watched_seconds > 0 && (
        <>
          <ShareBars title="内容结构" rows={typeRows} />
          <ShareBars title="按媒介（视频 / 音频）" rows={modalityRows} />
          <ShareBars title="按内容分类（娱乐 / 学习）" rows={classRows} />
          <ShareBars title="语言比例" rows={langRows} />

          <Card title="常看内容" size="small">
            <Table
              rowKey="title"
              columns={topMediaColumns}
              dataSource={data.top_media}
              pagination={false}
              size="small"
              loading={loading}
              locale={{ emptyText: '暂无记录' }}
            />
          </Card>

          {data.recent_records && data.recent_records.length > 0 && (
            <Card
              title="观看记录明细"
              size="small"
              extra={
                <Button size="small" icon={<DownloadOutlined />} onClick={exportCsv}>
                  导出 CSV
                </Button>
              }
            >
              <Table
                rowKey={(r) => `${r.title}-${r.started_at}`}
                columns={recordColumns}
                dataSource={data.recent_records}
                pagination={false}
                size="small"
                locale={{ emptyText: '暂无记录' }}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                只按 TV 已确认的实际播放区间累计（Seek / 重连不误计）；展示最近 20 条。
              </Typography.Text>
            </Card>
          )}

          {data.top_series.length > 0 && (
            <Card title="常看系列" size="small">
              <List
                size="small"
                dataSource={data.top_series}
                renderItem={(s) => (
                  <List.Item
                    extra={
                      <Typography.Text type="secondary">
                        {fmtWatchSeconds(s.watched_seconds)}
                      </Typography.Text>
                    }
                  >
                    {s.title}
                  </List.Item>
                )}
              />
            </Card>
          )}
        </>
      )}

      {data.note && <Alert type="info" showIcon message={data.note} />}
    </Space>
  )
}
