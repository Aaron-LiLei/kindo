/**
 * AI 任务历史（记录可见性，产品反馈 2026-08-27）：按入口展示最近 10 次 AI
 * 任务（状态/时间/结果概要/失败原因）。数据来自 GET /ai/jobs——ai_job 表
 * 即每次 AI 调用任务的持久化记录；不新建页面（交互 §8.2 不设独立中心）。
 */
import { Badge, Collapse, Typography } from 'antd'
import { useApi } from '../hooks/useApi'
import type { AiJobRow } from '../types/admin'
import type { AiJobType } from '../hooks/useAiJob'

const STATE_META: Record<string, { label: string; status: 'success' | 'error' | 'warning' | 'processing' | 'default' }> = {
  done: { label: '成功', status: 'success' },
  failed: { label: '失败', status: 'error' },
  interrupted: { label: '中断', status: 'warning' },
  running: { label: '进行中', status: 'processing' },
  queued: { label: '排队中', status: 'default' },
}

const fmtTime = (iso: string | null) =>
  iso ? iso.replace('T', ' ').slice(0, 19) : ''

const JOB_LABEL: Record<string, string> = {
  CATALOG_AUDIT: '整理',
  USAGE_SUMMARY: '使用摘要',
  CONTENT_COVERAGE: '内容缺口',
}

export function AiJobHistory({ jobType }: { jobType: AiJobType }) {
  const { data } = useApi<{ items: AiJobRow[] }>(
    `/api/v1/admin/ai/jobs?job_type=${jobType}&limit=10`,
  )
  const items = data?.items ?? []
  if (!items.length) return null

  return (
    <Collapse
      size="small"
      items={items.map((j) => {
        const meta = STATE_META[j.state] ?? { label: j.state, status: 'default' as const }
        const counts = j.result_summary?.counts ?? {}
        const created = (counts.created ?? 0) + (counts.created_high ?? 0) + (counts.policy_created ?? 0) + (counts.gap_created ?? 0)
        return {
          key: j.job_id,
          label: (
            <span>
              <Badge status={meta.status} text={meta.label} />{' '}
              {fmtTime(j.created_at)} · {JOB_LABEL[j.job_type] ?? j.job_type}
              {created ? ` · 生成 ${created} 条建议` : ''}
            </span>
          ),
          children: (
            <div>
              {j.result_summary?.headlines?.length ? (
                <ul className="ai-findings-list">
                  {j.result_summary.headlines.slice(0, 5).map((h, i) => (
                    <li key={i}>
                      <Typography.Text style={{ fontSize: 12 }}>{h}</Typography.Text>
                    </li>
                  ))}
                </ul>
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  无结果摘要
                </Typography.Text>
              )}
              {j.error_summary ? (
                <Typography.Text type="danger" style={{ fontSize: 12 }}>
                  {j.error_summary}
                </Typography.Text>
              ) : null}
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
                用时：{j.started_at && j.finished_at
                  ? `${Math.max(1, Math.round((new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000))} 秒`
                  : '—'}
              </Typography.Paragraph>
            </div>
          ),
        }
      })}
    />
  )
}
