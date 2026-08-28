import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AiSuggestCard } from './AiSuggestCard'
import type { AiProposalRow } from '../types/admin'

const low = (id: string, changes: Record<string, unknown>, title = '汪汪队',
  entity = 'e1'): AiProposalRow => ({
  proposal_id: id,
  proposal_type: 'METADATA',
  impact_level: 'LOW',
  status: 'PENDING',
  profile: 'library_curator',
  job_id: 'j1',
  summary: '为什么：缺主题；将修改：补充主题；影响：更容易找到',
  summary_parts: { why: '缺少主题', what: '补充海洋主题', impact: '更容易被找到' },
  changes,
  policy_diff: null,
  entity_id: entity,
  entity_title: title,
  created_at: null,
  applied_at: null,
})

const high: AiProposalRow = {
  proposal_id: 'h1',
  proposal_type: 'METADATA',
  impact_level: 'HIGH',
  status: 'PENDING',
  profile: 'library_curator',
  job_id: 'j1',
  summary: '…',
  summary_parts: { why: '分类可疑', what: '调整为学习', impact: '计入学习预算' },
  changes: { fields: { content_class: 'LEARNING' } },
  policy_diff: null,
  entity_id: 'e2',
  entity_title: '海底小纵队',
  created_at: null,
  applied_at: null,
}

describe('AiSuggestCard 建议呈现原则（交互 §8.2.1）', () => {
  it('LOW 合并为一次操作：显示批量卡与"应用安全修改"，不逐项审批', async () => {
    const onApplyBatch = vi.fn()
    render(
      <AiSuggestCard
        proposals={[low('a', { topics_add: ['海洋'] }), low('b', { fields: { overview: 'x' } }, '小猪佩奇', 'e2')]}
        busy={false}
        onApplyOne={vi.fn()}
        onApplyMany={vi.fn()}
        onApplyBatch={onApplyBatch}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByText('可以完善 2 个内容')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '应用安全修改' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '应用安全修改' }))
    expect(onApplyBatch).toHaveBeenCalledWith(['a', 'b'])
  })

  it('LOW 取消勾选后仅应用选中项', async () => {
    const onApplyBatch = vi.fn()
    render(
      <AiSuggestCard
        proposals={[low('a', { topics_add: ['海洋'] }), low('b', { topics_add: ['工程车'] }, '小猪佩奇', 'e2')]}
        busy={false}
        onApplyOne={vi.fn()}
        onApplyMany={vi.fn()}
        onApplyBatch={onApplyBatch}
        onReject={vi.fn()}
      />,
    )
    await userEvent.click(screen.getAllByRole('checkbox')[1])
    await userEvent.click(screen.getByRole('button', { name: '应用安全修改' }))
    expect(onApplyBatch).toHaveBeenCalledWith(['a'])
  })

  it('HIGH 单决策卡：应用调整 / 保持现在这样 各确认一次', async () => {
    const onApplyOne = vi.fn()
    const onReject = vi.fn()
    render(
      <AiSuggestCard
        proposals={[high]}
        busy={false}
        onApplyOne={onApplyOne}
        onApplyMany={vi.fn()}
        onApplyBatch={vi.fn()}
        onReject={onReject}
      />,
    )
    expect(screen.getByText('需要你确认一次')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '应用调整' }))
    expect(onApplyOne).toHaveBeenCalledWith('h1')
    await userEvent.click(screen.getByRole('button', { name: '保持现在这样' }))
    expect(onReject).toHaveBeenCalledWith('h1')
  })

  it('建议解释三问必须呈现，且不出现内部字段名', () => {
    render(
      <AiSuggestCard
        proposals={[high]}
        busy={false}
        onApplyOne={vi.fn()}
        onApplyMany={vi.fn()}
        onApplyBatch={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByText(/为什么：缺少主题|为什么：分类可疑/)).toBeTruthy()
    expect(screen.queryByText(/content_class/i)).toBeNull()
  })
})

describe('AiSuggestCard 按资源分组与一键确认（v0.3.3 呈现定版）', () => {
  const highSameEntity = (id: string, what: string): AiProposalRow => ({
    ...high,
    proposal_id: id,
    entity_id: 'e2',
    entity_title: '海底小纵队',
    summary_parts: { why: '资料支持', what, impact: '分类更准确' },
    changes: { fields: { content_class: 'LEARNING' } },
    policy_diff: null,
  })

  it('同一资源的多条高影响建议合并为一张卡，应用调整一次决策', async () => {
    const onApplyMany = vi.fn()
    const onApplyOne = vi.fn()
    render(
      <AiSuggestCard
        proposals={[highSameEntity('h1', '调整为学习'), highSameEntity('h2', '适龄修正')]}
        busy={false}
        onApplyOne={onApplyOne}
        onApplyMany={onApplyMany}
        onApplyBatch={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByText('调整建议：海底小纵队（2 项）')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '应用调整' }))
    expect(onApplyMany).toHaveBeenCalledWith(['h1', 'h2'])
    expect(onApplyOne).not.toHaveBeenCalled()
  })

  it('多条高影响建议提供一键确认弹窗：清单完整呈现后一次应用', async () => {
    const onApplyMany = vi.fn()
    const withDiff = { ...high, policy_diff: ['动画（娱乐）时间：40 → 35 分钟'] }
    const second = { ...withDiff, proposal_id: 'h9', entity_id: 'e3', entity_title: '汪汪队' }
    render(
      <AiSuggestCard
        proposals={[withDiff, second]}
        busy={false}
        onApplyOne={vi.fn()}
        onApplyMany={onApplyMany}
        onApplyBatch={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '一键确认全部高影响建议' }))
    // 弹窗内呈现每条（实体 + 变化 + 前后值）后才确认
    expect(screen.getByText('海底小纵队')).toBeInTheDocument()
    expect(screen.getByText('汪汪队')).toBeInTheDocument()
    expect(screen.getAllByText(/动画（娱乐）时间：40 → 35/).length).toBeGreaterThanOrEqual(2)
    await userEvent.click(screen.getByRole('button', { name: /确认应用 2 条/ }))
    expect(onApplyMany).toHaveBeenCalledWith(['h1', 'h9'])
  })
})
