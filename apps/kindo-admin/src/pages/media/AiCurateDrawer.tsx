/**
 * 媒体库"AI 帮我整理"抽屉（交互设计 §8.2；AIA-001/002，实施计划 S1）。
 * 触发 CATALOG_AUDIT → 轮询真实进度 → 无副作用发现直接呈现（不要求确认）
 * + 待处理建议经 AiSuggestCard 呈现（LOW 批量 / HIGH 单决策）。
 * AI 不可用时仅本入口降级提示，不影响页面其余功能（交互 §10）。
 */
import { App as AntApp, Button, Drawer, Progress, Popconfirm, Space, Typography } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useRef, useState } from 'react'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type { AiJobRow, AiProposalRow } from '../../types/admin'
import { AiSuggestCard } from '../../components/AiSuggestCard'
import { AiJobHistory } from '../../components/AiJobHistory'

const POLL_MS = 1500
const ACTIVE_STATES = ['queued', 'running']

export function AiCurateDrawer({
  open,
  onClose,
  onChanged,
}: {
  open: boolean
  onClose: () => void
  onChanged?: () => void
}) {
  const [job, setJob] = useState<AiJobRow | null>(null)
  const [proposals, setProposals] = useState<AiProposalRow[]>([])
  const [pendingTotal, setPendingTotal] = useState(0)
  const [starting, setStarting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [error, setError] = useState('')
  const jobRef = useRef<AiJobRow | null>(null)
  const { message } = AntApp.useApp()

  useEffect(() => {
    jobRef.current = job
  }, [job])

  const loadProposals = useCallback(() => {
    adminApi
      .aiProposals({ status: 'PENDING' })
      .then((r) => {
        setProposals(
          r.items.filter((p) => p.proposal_type === 'METADATA' || p.proposal_type === 'ARTWORK'),
        )
        setPendingTotal(r.total)
      })
      .catch(() => {})
  }, [])

  // 打开时取最近一次任务；运行中轮询到终态
  useEffect(() => {
    if (!open) return
    adminApi
      .aiJobs({ job_type: 'CATALOG_AUDIT', limit: 1 })
      .then((r) => {
        setJob(r.items[0] ?? null)
        setError('')
      })
      .catch((e) => setError(formatApiError(e)))
    loadProposals()
  }, [open, loadProposals])

  useEffect(() => {
    if (!open || !job?.job_id) return
    const jobId = job.job_id
    const h = window.setInterval(() => {
      const cur = jobRef.current
      if (cur && !ACTIVE_STATES.includes(cur.state)) return
      adminApi
        .aiJob(jobId)
        .then((j) => {
          const prev = jobRef.current
          setJob(j)
          if (prev && ACTIVE_STATES.includes(prev.state) && !ACTIVE_STATES.includes(j.state)) {
            loadProposals()
          }
        })
        .catch(() => {})
      // 建议逐条流入（产品反馈 2026-08-27）：运行中随轮询拉取当前任务
      // 已生成的建议并实时呈现（只读；完成后 loadProposals 接管全量）
      adminApi
        .aiProposals({ status: 'PENDING', job_id: jobId })
        .then((r) => {
          setProposals(
            r.items.filter(
              (p) => p.proposal_type === 'METADATA' || p.proposal_type === 'ARTWORK',
            ),
          )
        })
        .catch(() => {})
    }, POLL_MS)
    return () => window.clearInterval(h)
  }, [open, job?.job_id, loadProposals])

  const start = () => {
    setStarting(true)
    setError('')
    adminApi
      .aiJobCreate('CATALOG_AUDIT')
      .then((r) => setJob({ job_id: r.job_id, job_type: 'CATALOG_AUDIT', state: 'queued', progress: 0, result_summary: null, error_summary: null, created_at: null, started_at: null, finished_at: null }))
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setStarting(false))
  }

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

  const dismissAll = () => {
    setClearing(true)
    adminApi
      .aiProposalsDismissAll()
      .then((r) => {
        message.success(`已清空 ${r.cleared} 条待处理建议`)
        refreshAfterAction()
      })
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setClearing(false))
  }

  const active = job && ACTIVE_STATES.includes(job.state)
  const done = job?.state === 'done'
  const failed = job && (job.state === 'failed' || job.state === 'interrupted')
  const headlines = job?.result_summary?.headlines ?? []
  const live = active ? job?.result_summary : null
  const liveCounts = live?.counts ?? {}
  const liveCreated = (liveCounts.created ?? 0) + (liveCounts.created_high ?? 0)
  const finalCounts = done ? job?.result_summary?.counts ?? {} : {}
  const createdTotal = (finalCounts.created ?? 0) + (finalCounts.created_high ?? 0)

  return (
    <Drawer
      title="AI 帮我整理"
      open={open}
      onClose={onClose}
      width={480}
      destroyOnClose
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {error ? (
          <Typography.Text type="danger" aria-label="ai-error">
            {error}
          </Typography.Text>
        ) : null}

        {!job && !starting && (
          <div>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              AI 会逐批检查内容目录里每个内容的<b>标题、分类、适龄、主题、角色和图片</b>信息，
              发现缺失或异常后生成整理建议——<b>只读取内容资料，不读取观看历史和文件路径</b>。
              低影响修改一次应用；涉及分类/适龄的调整会单独请你确认。
            </Typography.Paragraph>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={starting}
              onClick={start}
            >
              开始整理
            </Button>
          </div>
        )}

        {active ? (
          <div aria-label="ai-progress">
            <Typography.Text type="secondary">
              {live?.stage_note ?? '正在整理媒体库…'}
            </Typography.Text>
            {typeof live?.processed === 'number' && typeof live?.total === 'number' && live.total > 0 ? (
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
                已检查 {live.processed}/{live.total} 个内容
                {liveCreated > 0
                  ? `；已生成 ${liveCreated} 条建议${
                      liveCounts.created_high ? `（${liveCounts.created_high} 条需单独确认）` : ''
                    }`
                  : ''}
              </Typography.Paragraph>
            ) : null}
            <Progress percent={Math.round((job?.progress ?? 0) * 100)} size="small" />
          </div>
        ) : null}

        {failed ? (
          <Typography.Text type="warning">
            上次整理未完成{job?.error_summary ? `：${job.error_summary}` : ''}，可重新发起。
          </Typography.Text>
        ) : null}

        {done && headlines.length > 0 ? (
          <div aria-label="ai-findings">
            <Typography.Title level={5} style={{ marginBottom: 4 }}>
              整理发现
            </Typography.Title>
            <ul className="ai-findings-list">
              {headlines.map((h, i) => (
                <li key={i}>
                  <Typography.Text style={{ fontSize: 12 }}>{h}</Typography.Text>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {done ? (
          <div aria-label="ai-audit-basis">
            <Typography.Title level={5} style={{ marginBottom: 4 }}>
              本次整理依据
            </Typography.Title>
            <div className="ai-suggest-summary">
              <div>
                共检查 {finalCounts.audited ?? 0} 个内容的标题、分类、适龄、主题、角色与图片信息；
                生成 {createdTotal} 条建议
                {finalCounts.created_high ? `（${finalCounts.created_high} 条需单独确认）` : ''}。
              </div>
              {finalCounts.skipped_locked ? (
                <div>{finalCounts.skipped_locked} 条因家长已锁定字段未生成建议（锁定不会被 AI 触碰）。</div>
              ) : null}
              {finalCounts.skipped_duplicate ? (
                <div>{finalCounts.skipped_duplicate} 条与已有或已拒绝的建议重复，未重复提醒。</div>
              ) : null}
              <div>全程未读取观看历史与文件路径；每条建议都附有"为什么 / 改什么 / 影响"说明。</div>
            </div>
          </div>
        ) : null}

        {done && proposals.length === 0 && headlines.length === 0 ? (
          <Typography.Text type="secondary">没有待处理的建议。</Typography.Text>
        ) : null}

        {!active && job ? (
          <Button loading={starting} onClick={start} size="small">
            重新整理
          </Button>
        ) : null}

        {active && proposals.length > 0 ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            以下建议已生成，整理完成后即可应用：
          </Typography.Text>
        ) : null}

        <AiSuggestCard
          proposals={proposals}
          busy={busy || !!active}
          onApplyOne={applyOne}
          onApplyBatch={applyBatch}
          onApplyMany={applyMany}
          onReject={reject}
        />

        {!active && pendingTotal > 0 ? (
          <div aria-label="ai-dismiss-all">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
              当前共有 {pendingTotal} 条待处理建议。如果积压太多处理不完，可以全部忽略、
              清掉重来——已应用的不受影响，下次整理会重新生成。
            </Typography.Paragraph>
            <Popconfirm
              title="清空全部待处理建议？"
              description={`将忽略 ${pendingTotal} 条建议（含屏幕时间建议），下次整理重新生成。`}
              okText="全部忽略"
              cancelText="先不了"
              okButtonProps={{ danger: true }}
              onConfirm={dismissAll}
            >
              <Button loading={clearing} danger size="small">
                全部忽略，清掉重来
              </Button>
            </Popconfirm>
          </div>
        ) : null}

        <div>
          <Typography.Title level={5} style={{ marginBottom: 4 }}>
            历史记录
          </Typography.Title>
          <AiJobHistory jobType="CATALOG_AUDIT" />
        </div>
      </Space>
    </Drawer>
  )
}
