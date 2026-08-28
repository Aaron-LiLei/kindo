/**
 * 概览页「Kindo AI」摘要卡（交互 §8.2；AIA-003）。
 * 只展示值得家长关注的信息（USAGE_SUMMARY headlines）+「查看详细情况」展开
 * 摘要全文；可重新生成。AI 不可用时仅本卡降级，不影响页面其余功能（§10）。
 */
import { Button, Card, Progress, Space, Typography } from 'antd'
import { useState } from 'react'
import { useAiJob } from '../hooks/useAiJob'

export function KindoAiCard() {
  const { job, starting, start, error } = useAiJob('USAGE_SUMMARY')
  const [expanded, setExpanded] = useState(false)
  const active = job && ['queued', 'running'].includes(job.state)
  const done = job?.state === 'done'
  const failed = job && (job.state === 'failed' || job.state === 'interrupted')
  const headlines = job?.result_summary?.headlines ?? []
  const summaryText = job?.result_summary?.summary_text ?? []

  return (
    <Card
      size="small"
      title="Kindo AI"
      extra={
        <Button
          size="small"
          loading={starting || !!active}
          disabled={!!active}
          onClick={start}
          aria-label={done ? '重新生成使用摘要' : '生成使用摘要'}
        >
          {done || failed ? '重新总结' : '生成摘要'}
        </Button>
      }
      aria-label="Kindo AI 摘要卡"
    >
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        {error ? (
          <Typography.Text type="danger">{error}</Typography.Text>
        ) : null}
        {active ? (
          <div aria-label="ai-summary-progress">
            <Typography.Text type="secondary">
              {job?.result_summary?.stage_note ?? '正在总结最近的使用情况…'}
            </Typography.Text>
            <Progress percent={Math.round((job?.progress ?? 0) * 100)} size="small" />
          </div>
        ) : null}
        {failed ? (
          <Typography.Text type="warning">
            上次总结未完成{job?.error_summary ? `：${job.error_summary}` : ''}，可重新生成。
          </Typography.Text>
        ) : null}
        {done && headlines.length > 0 ? (
          <>
            <ul className="ai-findings-list">
              {headlines.map((h, i) => (
                <li key={i}>
                  <Typography.Text style={{ fontSize: 12 }}>{h}</Typography.Text>
                </li>
              ))}
            </ul>
            <Button
              size="small"
              type="link"
              onClick={() => setExpanded((v) => !v)}
              aria-label={expanded ? '收起详细情况' : '查看详细情况'}
            >
              {expanded ? '收起' : '查看详细情况'}
            </Button>
            {expanded && summaryText.length > 0 ? (
              <div className="ai-suggest-summary" aria-label="ai-summary-detail">
                {summaryText.map((t, i) => (
                  <div key={i}>{t}</div>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
        {!job && !error ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            AI 可以总结孩子最近的使用情况（看什么、多久、规则是否合适），只在家庭网络内运行。
          </Typography.Text>
        ) : null}
      </Space>
    </Card>
  )
}
