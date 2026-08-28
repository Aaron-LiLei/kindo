/**
 * AI 分析任务钩子（家长 AI 助手，技术方案 §19.5）：取最近任务 → 运行中轮询到终态
 * → start() 显式触发（无定时任务）。多个页面入口共用同一 job_type 的最近结果。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { adminApi } from '../api/admin'
import { formatApiError } from '../api/client'
import type { AiJobRow } from '../types/admin'

const POLL_MS = 1500
const ACTIVE_STATES = ['queued', 'running']

export type AiJobType = 'CATALOG_AUDIT' | 'USAGE_SUMMARY' | 'CONTENT_COVERAGE'

const emptyJob = (jobType: AiJobType, jobId: string): AiJobRow => ({
  job_id: jobId,
  job_type: jobType,
  state: 'queued',
  progress: 0,
  result_summary: null,
  error_summary: null,
  created_at: null,
  started_at: null,
  finished_at: null,
})

export function useAiJob(jobType: AiJobType, enabled = true) {
  const [job, setJob] = useState<AiJobRow | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const jobRef = useRef<AiJobRow | null>(null)

  useEffect(() => {
    jobRef.current = job
  }, [job])

  const loadLatest = useCallback(() => {
    adminApi
      .aiJobs({ job_type: jobType, limit: 1 })
      .then((r) => {
        setJob(r.items[0] ?? null)
        setError('')
      })
      .catch((e) => setError(formatApiError(e)))
  }, [jobType])

  useEffect(() => {
    if (enabled) loadLatest()
  }, [enabled, loadLatest])

  useEffect(() => {
    if (!enabled || !job?.job_id) return
    const jobId = job.job_id
    const h = window.setInterval(() => {
      const cur = jobRef.current
      if (cur && !ACTIVE_STATES.includes(cur.state)) {
        window.clearInterval(h) // 终态即停（评审 L-3：不再空转轮询）
        return
      }
      adminApi
        .aiJob(jobId)
        .then((j) => setJob((prev) => (prev && prev.job_id === j.job_id ? j : prev)))
        .catch(() => {})
    }, POLL_MS)
    return () => window.clearInterval(h)
  }, [enabled, job?.job_id])

  const start = useCallback(() => {
    setStarting(true)
    adminApi
      .aiJobCreate(jobType)
      .then((r) => setJob(emptyJob(jobType, r.job_id)))
      .catch((e) => setError(formatApiError(e)))
      .finally(() => setStarting(false))
  }, [jobType])

  return { job, starting, start, error, loadLatest }
}
