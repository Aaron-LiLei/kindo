/**
 * Advisor 场景面板（交互 §8.2；AIA-003/004/005）三变体共用：
 * - summary（观看统计）：触发 USAGE_SUMMARY 并呈现摘要全文（无副作用，无确认）；
 * - policy（屏幕时间）：呈现 USAGE_SUMMARY 产出的规则建议（HIGH 单决策卡）；
 * - coverage（内容列表）：触发 CONTENT_COVERAGE，方向性建议仅可忽略。
 * 与媒体库整理共用 AiSuggestCard 基座，不另写卡片。
 */
import { Button, Card, Progress, Space, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import type { AiProposalRow } from '../types/admin'
import { AiSuggestCard } from './AiSuggestCard'
import { AiJobHistory } from './AiJobHistory'
import { useAiJob } from '../hooks/useAiJob'

export type AiAdvisorVariant = 'summary' | 'policy' | 'coverage'

const VARIANT_CONFIG: Record<
  AiAdvisorVariant,
  { jobType: 'USAGE_SUMMARY' | 'CONTENT_COVERAGE'; title: string; action: string; proposalType?: string }
> = {
  summary: {
    jobType: 'USAGE_SUMMARY',
    title: 'AI 使用摘要',
    action: '总结最近的使用情况',
  },
  policy: {
    jobType: 'USAGE_SUMMARY',
    title: 'AI 规则建议',
    action: '帮我看看这套规则合不合适',
    proposalType: 'POLICY',
  },
  coverage: {
    jobType: 'CONTENT_COVERAGE',
    title: 'AI 内容缺口',
    action: '看看还缺什么类型的内容',
    proposalType: 'CONTENT_GAP',
  },
}

export function AiAdvisorPanel({
  variant,
  onChanged,
}: {
  variant: AiAdvisorVariant
  onChanged?: () => void
}) {
  const config = VARIANT_CONFIG[variant]
  const { job, starting, start, error: jobError } = useAiJob(config.jobType)
  const [proposals, setProposals] = useState<AiProposalRow[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadProposals = useCallback(() => {
    if (!config.proposalType) return
    adminApi
      .aiProposals({ status: 'PENDING', proposal_type: config.proposalType })
      .then((r) => setProposals(r.items))
      .catch(() => {})
  }, [config.proposalType])

  useEffect(() => {
    loadProposals()
  }, [loadProposals])

  // 任务到达终态时刷新建议（POLICY 建议随 USAGE_SUMMARY 产出）
  useEffect(() => {
    if (job && !['queued', 'running'].includes(job.state)) loadProposals()
  }, [job?.state, loadProposals]) // eslint-disable-line react-hooks/exhaustive-deps

  const refreshAfterAction = () => {
    loadProposals()
    onChanged?.()
  }

  const applyOne = (id: string) => {
    setBusy(true)
    adminApi
      .aiProposalApply(id)
      .then(refreshAfterAction)
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setBusy(false))
  }

  const applyBatch = (ids: string[]) => {
    setBusy(true)
    adminApi
      .aiProposalsBatchApply(ids)
      .then(refreshAfterAction)
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setBusy(false))
  }

  const applyMany = (ids: string[]) => {
    setBusy(true)
    adminApi
      .aiProposalsBatchApply(ids, true)
      .then(refreshAfterAction)
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setBusy(false))
  }

  const reject = (id: string) => {
    setBusy(true)
    adminApi
      .aiProposalReject(id)
      .then(refreshAfterAction)
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setBusy(false))
  }

  const active = job && ['queued', 'running'].includes(job.state)
  const done = job?.state === 'done'
  const failed = job && (job.state === 'failed' || job.state === 'interrupted')
  const headlines = job?.result_summary?.headlines ?? []
  const summaryText = job?.result_summary?.summary_text ?? []

  return (
    <Card
      size="small"
      title={config.title}
      extra={
        <Button
          size="small"
          loading={starting || !!active}
          disabled={!!active}
          onClick={start}
        >
          {config.action}
        </Button>
      }
      aria-label={`${config.title}面板`}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {error || jobError ? (
          <Typography.Text type="danger" aria-label="ai-advisor-error">
            {error || jobError}
          </Typography.Text>
        ) : null}
        {active ? (
          <div aria-label="ai-advisor-progress">
            <Typography.Text type="secondary">
              {job?.result_summary?.stage_note ?? 'AI 正在分析…'}
            </Typography.Text>
            <Progress percent={Math.round((job?.progress ?? 0) * 100)} size="small" />
          </div>
        ) : null}
        {failed ? (
          <Typography.Text type="warning">
            上次分析未完成{job?.error_summary ? `：${job.error_summary}` : ''}，可重新发起。
          </Typography.Text>
        ) : null}
        {done && headlines.length > 0 ? (
          <ul className="ai-findings-list">
            {headlines.map((h, i) => (
              <li key={i}>
                <Typography.Text style={{ fontSize: 12 }}>{h}</Typography.Text>
              </li>
            ))}
          </ul>
        ) : null}
        {variant === 'summary' && summaryText.length > 0 ? (
          <div className="ai-suggest-summary" aria-label="ai-summary-text">
            {summaryText.map((t, i) => (
              <div key={i}>{t}</div>
            ))}
          </div>
        ) : null}
        {config.proposalType ? (
          <AiSuggestCard
            proposals={proposals}
            busy={busy}
            onApplyOne={applyOne}
            onApplyBatch={applyBatch}
            onApplyMany={applyMany}
            onReject={reject}
          />
        ) : null}
        <div>
          <Typography.Title level={5} style={{ marginBottom: 4 }}>
            历史记录
          </Typography.Title>
          <AiJobHistory jobType={config.jobType} />
        </div>

        {!job && !jobError ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {variant === 'policy'
              ? 'AI 结合最近的使用数据评估当前规则，只提建议，调整需你确认。'
              : variant === 'coverage'
                ? 'AI 对比孩子的兴趣与家庭内容，指出可以补充的内容方向。'
                : 'AI 总结最近一段时间的使用情况，只描述可观察行为。'}
          </Typography.Text>
        ) : null}
      </Space>
    </Card>
  )
}
