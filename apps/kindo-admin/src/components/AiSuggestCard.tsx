/**
 * 家长 AI 建议卡基座（交互 §8.2.1；AIA-002/007/008；v0.3.3 呈现定版）。
 * 按资源分组：同一内容的多条建议合并展示（数据层仍按 资源×修改类型 分条存储，
 * 保障去重/独立忽略/影响等级互不混批）。
 * - LOW：批量卡（勾选后一次"应用安全修改"，不逐项审批）；
 * - HIGH：每个资源一张卡=一次决策（卡内列出该资源全部高影响项与前后值）；
 *   多条高影响建议提供"一键确认全部"清单式确认弹窗（完整列出每条变化，一次确认）；
 * - CONTENT_GAP：方向性信息卡（仅忽略）。
 * 界面只出现"AI 建议"文案，不暴露内部字段/JSON/Agent 术语。
 */
import { Button, Card, Checkbox, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import type { AiProposalRow } from '../types/admin'

const KIND_LABELS: Record<string, string> = {
  poster: '海报',
  backdrop: '背景图',
  thumbnail: '缩略图',
  logo: '标志图',
}

const BUDGET_LABELS: Record<string, string> = {
  screen_total_minutes: '总屏幕时间',
  audio_minutes: '音频时间',
  ai_voice_minutes: 'AI 语音时间',
}
const CLASS_LABELS: Record<string, string> = {
  ENTERTAINMENT: '动画（娱乐）时间',
  LEARNING: '学习视频时间',
}

/** 规则补丁的家长可读短语（不暴露键名） */
function policyPatchLabel(patch: Record<string, unknown>): string {
  const parts: string[] = []
  const budgets = patch.budgets as Record<string, unknown> | undefined
  if (budgets && typeof budgets === 'object') {
    for (const [k, v] of Object.entries(budgets)) {
      if (v === undefined || v === null) continue
      if (k === 'video_by_class' && v && typeof v === 'object') {
        for (const [ck] of Object.entries(v as Record<string, unknown>)) {
          parts.push(CLASS_LABELS[ck] ?? `${ck} 视频时间`)
        }
      } else {
        parts.push(BUDGET_LABELS[k] ?? k)
      }
    }
  }
  if (patch.offscreen) parts.push('离屏活动设置')
  if (patch.transition_policy) parts.push('成长接力设置')
  if (patch.allowed_windows) parts.push('可观看时间段')
  if (typeof patch.autoplay === 'boolean') parts.push('自动连播')
  if (typeof patch.daily_episode_limit === 'number') parts.push('每日集数上限')
  if (patch.course_counts_as_entertainment !== undefined) parts.push('课程计入方式')
  return parts.length ? `调整${parts.join('、')}` : '调整屏幕时间规则'
}

/** 家长可读的"将修改什么"短语（changes 的自然语言化，不暴露字段名） */
export function changeLabel(p: AiProposalRow): string {
  const c = p.changes as Record<string, unknown>
  if (p.proposal_type === 'POLICY' && c.rules_patch && typeof c.rules_patch === 'object') {
    return policyPatchLabel(c.rules_patch as Record<string, unknown>)
  }
  if (p.proposal_type === 'CONTENT_GAP') {
    const modality = c.modality === 'AUDIO' ? '音频' : '视频'
    return typeof c.topic === 'string' ? `补充「${c.topic}」相关${modality}内容` : `补充相关${modality}内容`
  }
  if (Array.isArray(c.topics_add)) return `补充主题（${(c.topics_add as string[]).join('、')}）`
  if (Array.isArray(c.characters_add)) return `补充角色（${(c.characters_add as string[]).join('、')}）`
  if (c.fields && typeof c.fields === 'object') {
    const f = c.fields as Record<string, unknown>
    if ('overview' in f) return '补充简介'
    if ('language' in f) return '完善语言'
    if ('content_class' in f) return '调整内容分类'
    if ('age_min' in f || 'age_max' in f) return '调整适龄范围'
  }
  if (typeof c.kind === 'string') return `补充${KIND_LABELS[c.kind] ?? '图片'}`
  return '完善内容资料'
}

function SummaryLines({ p }: { p: AiProposalRow }) {
  const s = p.summary_parts || {}
  return (
    <div className="ai-suggest-summary">
      {s.why ? <div>为什么：{s.why}</div> : null}
      {s.what ? <div>将修改：{s.what}</div> : null}
      {s.impact ? <div>影响：{s.impact}</div> : null}
    </div>
  )
}

/** 单条高影响项的完整呈现（标签 + 前后值 + 三问） */
function HighItemLines({ p }: { p: AiProposalRow }) {
  return (
    <div className="ai-suggest-high-item">
      <div>
        <Typography.Text strong style={{ fontSize: 13 }}>
          {changeLabel(p)}
        </Typography.Text>
      </div>
      {p.policy_diff && p.policy_diff.length > 0 ? (
        <div className="ai-policy-diff">
          {p.policy_diff.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      ) : null}
      <SummaryLines p={p} />
    </div>
  )
}

/** 分组键：同一资源合并；POLICY/CONTENT_GAP 无资源实体，各自成组 */
const groupKey = (p: AiProposalRow) =>
  p.entity_id ?? `__${p.proposal_type}:${p.proposal_id}`

const groupTitle = (p: AiProposalRow) =>
  p.entity_title ?? (p.proposal_type === 'POLICY' ? '屏幕时间规则' : '内容方向')

interface Group {
  key: string
  title: string
  items: AiProposalRow[]
}

function groupBy(list: AiProposalRow[]): Group[] {
  const map = new Map<string, Group>()
  for (const p of list) {
    const key = groupKey(p)
    if (!map.has(key)) map.set(key, { key, title: groupTitle(p), items: [] })
    map.get(key)!.items.push(p)
  }
  return [...map.values()]
}

export interface AiSuggestCardProps {
  proposals: AiProposalRow[]
  busy: boolean
  onApplyOne: (id: string) => void
  /** LOW 批量（不含高影响） */
  onApplyBatch: (ids: string[]) => void
  /** 高影响清单式一次确认（单资源多项或全部） */
  onApplyMany: (ids: string[]) => void
  onReject: (id: string) => void
}

export function AiSuggestCard({
  proposals,
  busy,
  onApplyOne,
  onApplyBatch,
  onApplyMany,
  onReject,
}: AiSuggestCardProps) {
  const pending = proposals.filter((p) => p.status === 'PENDING')
  const lows = pending.filter(
    (p) => p.impact_level === 'LOW' && p.proposal_type !== 'CONTENT_GAP',
  )
  const highGroups = groupBy(pending.filter((p) => p.impact_level === 'HIGH'))
  const infos = pending.filter((p) => p.proposal_type === 'CONTENT_GAP')
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [confirmOpen, setConfirmOpen] = useState(false)
  const highIds = highGroups.flatMap((g) => g.items.map((p) => p.proposal_id))

  if (!pending.length) return null
  const isChecked = (p: AiProposalRow) => checked[p.proposal_id] !== false
  const selectedIds = lows.filter(isChecked).map((p) => p.proposal_id)
  const lowGroups = groupBy(lows)
  const breakdown = lows
    .map((p) => changeLabel(p).replace(/（.*）$/, ''))
    .reduce<Record<string, number>>((acc, k) => {
      acc[k] = (acc[k] ?? 0) + 1
      return acc
    }, {})

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {lows.length > 0 && (
        <Card
          size="small"
          title={`可以完善 ${lowGroups.length} 个内容${lows.length !== lowGroups.length ? `（${lows.length} 条建议）` : ''}`}
          extra={
            <Button
              size="small"
              type="primary"
              loading={busy}
              disabled={!selectedIds.length}
              onClick={() => onApplyBatch(selectedIds)}
            >
              应用安全修改
            </Button>
          }
          aria-label="低影响建议批量卡"
        >
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
            {Object.entries(breakdown)
              .map(([k, n]) => `${n} 个${k}`)
              .join('；')}
            。一次应用即可，无需逐条确认。
          </Typography.Paragraph>
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {lowGroups.map((g) => (
              <div key={g.key} className="ai-suggest-entity-group">
                <Typography.Text strong style={{ fontSize: 12 }}>
                  {g.title}
                </Typography.Text>
                {g.items.map((p) => (
                  <div key={p.proposal_id} className="ai-suggest-row">
                    <Checkbox
                      checked={isChecked(p)}
                      onChange={(e) =>
                        setChecked((prev) => ({ ...prev, [p.proposal_id]: e.target.checked }))
                      }
                    >
                      {changeLabel(p)}
                    </Checkbox>
                    <Button
                      size="small"
                      type="text"
                      disabled={busy}
                      onClick={() => onReject(p.proposal_id)}
                      aria-label={`忽略 ${p.entity_title ?? ''}`}
                    >
                      忽略
                    </Button>
                  </div>
                ))}
              </div>
            ))}
          </Space>
        </Card>
      )}

      {highGroups.length >= 2 ? (
        <Button
          block
          type="primary"
          loading={busy}
          onClick={() => setConfirmOpen(true)}
          aria-label="一键确认全部高影响建议"
        >
          一键确认全部（{highIds.length} 条高影响建议）
        </Button>
      ) : null}

      {highGroups.map((g) => (
        <Card
          key={g.key}
          size="small"
          title={`调整建议：${g.title}${g.items.length > 1 ? `（${g.items.length} 项）` : ''}`}
          extra={
            <Space>
              <Button
                size="small"
                type="primary"
                loading={busy}
                onClick={() =>
                  g.items.length === 1
                    ? onApplyOne(g.items[0].proposal_id)
                    : onApplyMany(g.items.map((p) => p.proposal_id))
                }
              >
                应用调整
              </Button>
              <Button
                size="small"
                disabled={busy}
                onClick={() => g.items.forEach((p) => onReject(p.proposal_id))}
              >
                保持现在这样
              </Button>
            </Space>
          }
          aria-label="高影响建议单决策卡"
        >
          <Tag color="orange">需要你确认一次</Tag>
          {g.items.map((p) => (
            <HighItemLines key={p.proposal_id} p={p} />
          ))}
        </Card>
      ))}

      {infos.map((p) => (
        <Card key={p.proposal_id} size="small" title="内容方向建议" aria-label="方向性建议卡">
          <SummaryLines p={p} />
          <Button
            size="small"
            type="text"
            disabled={busy}
            onClick={() => onReject(p.proposal_id)}
          >
            忽略
          </Button>
        </Card>
      ))}

      <Modal
        open={confirmOpen}
        title={`一键确认 ${highIds.length} 条高影响建议`}
        okText={`确认应用 ${highIds.length} 条`}
        cancelText="再看看"
        confirmLoading={busy}
        onOk={() => {
          setConfirmOpen(false)
          onApplyMany(highIds)
        }}
        onCancel={() => setConfirmOpen(false)}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          以下调整将立即生效（规则类变更即刻生效并可能结束进行中的播放）。请确认你已了解每条变化：
        </Typography.Paragraph>
        <div className="ai-confirm-list">
          {highGroups.map((g) => (
            <div key={g.key} className="ai-suggest-entity-group">
              <Typography.Text strong style={{ fontSize: 12 }}>
                {g.title}
              </Typography.Text>
              {g.items.map((p) => (
                <div key={p.proposal_id} style={{ paddingLeft: 8 }}>
                  <Typography.Text style={{ fontSize: 12 }}>· {changeLabel(p)}</Typography.Text>
                  {p.policy_diff?.map((line, i) => (
                    <div key={i} className="ai-policy-diff">
                      {line}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      </Modal>
    </Space>
  )
}
