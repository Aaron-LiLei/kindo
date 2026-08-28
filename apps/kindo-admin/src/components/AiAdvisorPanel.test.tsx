import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AiJobRow, AiProposalRow } from '../types/admin'

vi.mock('../api/admin', () => {
  const doneJob: AiJobRow = {
    job_id: 'job-1',
    job_type: 'USAGE_SUMMARY',
    state: 'done',
    progress: 1,
    result_summary: {
      headlines: ['娱乐视频每天基本用满'],
      summary_text: ['音频内容使用较少'],
      counts: {},
    },
    error_summary: null,
    created_at: '2026-08-26T00:00:00Z',
    started_at: null,
    finished_at: null,
  }
  const policyProposal: AiProposalRow = {
    proposal_id: 'p1',
    proposal_type: 'POLICY',
    impact_level: 'HIGH',
    status: 'PENDING',
    profile: 'family_advisor',
    job_id: 'job-1',
    summary: '…',
    summary_parts: {
      why: '娱乐视频长期用满而音频少',
      what: '动画时间 40 → 35 分钟',
      impact: '总屏幕时间不变，动画略减',
    },
    changes: { rules_patch: { budgets: { video_by_class: { ENTERTAINMENT: 35 } } } },
    policy_diff: ['动画（娱乐）时间：40 → 35 分钟'],
    entity_id: null,
    entity_title: null,
    created_at: null,
    applied_at: null,
  }
  return {
    adminApi: {
      aiJobs: vi.fn().mockResolvedValue({ items: [doneJob] }),
      aiJob: vi.fn().mockResolvedValue(doneJob),
      aiJobCreate: vi.fn().mockResolvedValue({ job_id: 'job-2', state: 'queued' }),
      aiProposals: vi.fn().mockResolvedValue({ items: [policyProposal] }),
      aiProposalApply: vi.fn().mockResolvedValue({
        proposal_id: 'p1', status: 'applied', policy_version: 8, revoked_playbacks: 0,
      }),
      aiProposalReject: vi.fn().mockResolvedValue({ proposal_id: 'p1', status: 'REJECTED' }),
      aiProposalsBatchApply: vi.fn(),
    },
  }
})

import { AiAdvisorPanel } from './AiAdvisorPanel'

describe('AiAdvisorPanel 三变体（交互 §8.2；AIA-003/004/005）', () => {
  it('summary 变体：呈现摘要全文，无确认操作', async () => {
    render(<AiAdvisorPanel variant="summary" />)
    expect(await screen.findByText(/娱乐视频每天基本用满/)).toBeInTheDocument()
    expect(screen.getByText(/音频内容使用较少/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '应用调整' })).toBeNull()
  })

  it('policy 变体：HIGH 单决策卡展示服务端事实核对的变更前后值，应用调整走单条 apply', async () => {
    const onChanged = vi.fn()
    render(<AiAdvisorPanel variant="policy" onChanged={onChanged} />)
    expect(await screen.findByText('需要你确认一次')).toBeInTheDocument()
    expect(screen.getByText('动画（娱乐）时间：40 → 35 分钟')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '应用调整' }))
    const { adminApi } = await import('../api/admin')
    await waitFor(() => expect(adminApi.aiProposalApply).toHaveBeenCalledWith('p1'))
    expect(onChanged).toHaveBeenCalled() // 规则页刷新（version 前进）
  })

  it('policy 变体：保持现在这样 = 拒绝', async () => {
    render(<AiAdvisorPanel variant="policy" />)
    await screen.findByText('需要你确认一次')
    await userEvent.click(screen.getByRole('button', { name: '保持现在这样' }))
    const { adminApi } = await import('../api/admin')
    await waitFor(() => expect(adminApi.aiProposalReject).toHaveBeenCalledWith('p1'))
  })
})
