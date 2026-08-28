/**
 * 扫描任务轮询：watch(mountId, jobId) 开始跟踪；每秒查询、页面隐藏暂停、
 * 组件卸载自动停止（定时器在 effect 中创建并清理）。任务结束后保留最终状态供展示。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { adminApi } from '../api/admin'

export interface ScanJobUiState {
  jobId: string
  state: string
  progress: number
  errorSummary: string | null
}

export function useScanJobs(onSettled?: (mountId: string, state: string) => void) {
  const [jobs, setJobs] = useState<Record<string, ScanJobUiState>>({})
  const [activeCount, setActiveCount] = useState(0)
  const jobsRef = useRef(jobs)
  useEffect(() => {
    jobsRef.current = jobs
  })
  const onSettledRef = useRef(onSettled)
  useEffect(() => {
    onSettledRef.current = onSettled
  })

  const watch = useCallback((mountId: string, jobId: string) => {
    setJobs((prev) => ({
      ...prev,
      [mountId]: { jobId, state: 'queued', progress: 0, errorSummary: null },
    }))
    setActiveCount((c) => c + 1)
  }, [])

  useEffect(() => {
    if (activeCount === 0) return
    const id = window.setInterval(async () => {
      if (document.visibilityState !== 'visible') return
      for (const [mountId, job] of Object.entries(jobsRef.current)) {
        if (job.state !== 'queued' && job.state !== 'running') continue
        try {
          const j = await adminApi.scanJob(job.jobId)
          setJobs((prev) => ({
            ...prev,
            [mountId]: {
              ...prev[mountId],
              state: j.state,
              progress: j.progress,
              errorSummary: j.error_summary,
            },
          }))
          if (j.state !== 'queued' && j.state !== 'running') {
            setActiveCount((c) => Math.max(0, c - 1))
            onSettledRef.current?.(mountId, j.state)
          }
        } catch {
          setActiveCount((c) => Math.max(0, c - 1))
        }
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [activeCount])

  return { jobs, watch }
}
