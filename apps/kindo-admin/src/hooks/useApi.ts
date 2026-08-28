/**
 * GET 数据获取 Hook：竞态防护（过期响应丢弃）、可选轮询（页面隐藏自动暂停）、卸载安全。
 * path 为 null 时不发请求；path 变化时保留旧数据直接换新（无闪烁）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatApiError } from '../api/client'

export interface UseApiResult<T> {
  data: T | null
  error: string
  loading: boolean
  loadedAt: Date | null
  reload: () => void
}

export function useApi<T>(path: string | null, opts: { pollMs?: number } = {}): UseApiResult<T> {
  const pollMs = opts.pollMs ?? 0
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const [tick, setTick] = useState(0)
  const hasDataRef = useRef(false)

  useEffect(() => {
    if (!path) return
    let cancelled = false
    if (!hasDataRef.current) setLoading(true)
    api
      .get(path)
      .then((d) => {
        if (cancelled) return
        hasDataRef.current = true
        setData(d as T)
        setError('')
        setLoading(false)
        setLoadedAt(new Date())
      })
      .catch((e) => {
        if (cancelled) return
        setError(formatApiError(e))
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [path, tick])

  useEffect(() => {
    if (!path || !pollMs) return
    const fire = () => {
      if (document.visibilityState === 'visible') setTick((t) => t + 1)
    }
    const id = window.setInterval(fire, pollMs)
    document.addEventListener('visibilitychange', fire)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', fire)
    }
  }, [path, pollMs])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, loadedAt, reload }
}
