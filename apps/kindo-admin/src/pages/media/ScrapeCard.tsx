import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Popconfirm, Progress, Space, Typography, message } from 'antd'
import { CloudDownloadOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { adminApi } from '../../api/admin'
import { formatApiError } from '../../api/client'
import type { ScrapeConfigResp, ScrapeStatusResp } from '../../types/admin'

/**
 * 海报刮削（2026-08-21 PRD 修订）：TMDB 检索 → 海报，按系列聚合。
 * 配置（API Key / 地址）在「设置」页维护（2026-08-26 聚合）。
 */
export function ScrapeCard({ onFinished }: { onFinished?: () => void }) {
  const [config, setConfig] = useState<ScrapeConfigResp | null>(null)
  const [status, setStatus] = useState<ScrapeStatusResp | null>(null)
  const timerRef = useRef<number | null>(null)
  const finishedRef = useRef(false)
  const onFinishedRef = useRef(onFinished)
  useEffect(() => {
    onFinishedRef.current = onFinished
  }, [onFinished])

  const refresh = async () => {
    const [c, s] = await Promise.all([
      adminApi.scrapeConfig(),
      adminApi.scrapeStatus(),
    ])
    setConfig(c)
    setStatus(s)
    return s
  }

  useEffect(() => {
    const t = window.setTimeout(() => refresh().catch(() => {}), 0)
    return () => {
      window.clearTimeout(t)
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  // running 时轮询进度
  useEffect(() => {
    if (status?.state === 'running') {
      finishedRef.current = false
      timerRef.current = window.setInterval(() => {
        adminApi
          .scrapeStatus()
          .then((s) => {
            setStatus(s)
            if (s.state !== 'running') {
              if (timerRef.current) window.clearInterval(timerRef.current)
              if (!finishedRef.current) {
                finishedRef.current = true
                onFinishedRef.current?.()
              }
            }
          })
          .catch(() => {})
      }, 1500)
    } else if (timerRef.current) {
      window.clearInterval(timerRef.current)
    }
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [status?.state])

  const run = async () => {
    try {
      await adminApi.scrapeRun()
      setStatus((s) => (s ? { ...s, state: 'running' } : s))
    } catch (e) {
      void message.error(formatApiError(e))
    }
  }

  const percent =
    status && status.total > 0 ? Math.round((status.done / status.total) * 100) : 0

  return (
    <div className="scrape-card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Space wrap>
        <Typography.Text strong>
          <CloudDownloadOutlined /> 海报刮削（TMDB）
        </Typography.Text>
        {!config?.api_key_configured ? (
          <Typography.Text type="warning">
            未配置 API Key——到<Link to="/settings">「设置」</Link>页填写
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">
            已配置（<Link to="/settings">设置</Link>）
          </Typography.Text>
        )}
        <Popconfirm
          title="开始刮削缺海报的内容？"
          description="按系列聚合到 TMDB 检索海报（已刮削过或已有海报的跳过）"
          onConfirm={run}
          disabled={!config?.api_key_configured || status?.state === 'running'}
        >
          <Button
            size="small"
            type="primary"
            loading={status?.state === 'running'}
            disabled={!config?.api_key_configured}
          >
            开始刮削
          </Button>
        </Popconfirm>
      </Space>

      {status && status.state === 'running' && (
        <div>
          <Progress percent={percent} size="small" />
          <Typography.Text type="secondary">
            {status.done}/{status.total} · 正在处理：{status.current || '…'}
          </Typography.Text>
        </div>
      )}

      {status && (status.state === 'done' || status.state === 'failed') && status.total > 0 && (
        <Alert
          type={status.state === 'failed' ? 'error' : 'success'}
          showIcon
          message={
            status.state === 'failed'
              ? '刮削任务失败'
              : `刮削完成：命中 ${status.matched}，未命中 ${status.no_hit}，失败 ${status.failed}`
          }
          description={
            status.log_tail.length > 0 ? (
              <span style={{ fontSize: 12 }}>
                {status.log_tail.slice(-3).join('；')}
              </span>
            ) : undefined
          }
        />
      )}
    </div>
  )
}
