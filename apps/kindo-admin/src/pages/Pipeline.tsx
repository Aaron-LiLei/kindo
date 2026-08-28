import { useState } from 'react'
import { Space, Typography } from 'antd'
import { ScrapeCard } from './media/ScrapeCard'
import { MatchManager } from './media/MatchManager'

/**
 * 刮削与匹配（2026-08-25 从“媒体来源与扫描”独立为一级入口）：
 * 识别管线两阶段——TMDB 身份与元数据（Parser→Matcher→Decision→Normalizer）
 * 与海报 Artwork。扫描入库与识别解耦：扫描只负责“把文件收进来”。
 */
export function PipelinePage() {
  // 刮削完成 → 重挂载匹配列表立即取最新（MatchManager 自身 10s 轮询兜底）
  const [matchTick, setMatchTick] = useState(0)
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        识别管线独立于扫描：扫描负责把文件入库；这里负责让内容“有身份、有海报”——
        TMDB 检索匹配（exact 自动应用 / 低置信待确认 / 家长确认永不被覆盖）与
        海报/背景图/缩略图的获取。
      </Typography.Paragraph>
      <ScrapeCard onFinished={() => setMatchTick((t) => t + 1)} />
      <MatchManager key={matchTick} />
    </Space>
  )
}
