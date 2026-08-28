import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AiJobRow, AiProposalRow } from '../../types/admin'

// vi.mock 工厂会被提升到文件顶部：数据构造必须放进工厂内部（hoisting TDZ）。
vi.mock('../../api/admin', () => {
  const doneJob: AiJobRow = {
    job_id: 'job-1',
    job_type: 'CATALOG_AUDIT',
    state: 'done',
    progress: 1,
    result_summary: {
      headlines: ['《汪汪队》疑似重复归组', '《海底小纵队》缺少适龄范围'],
      counts: { audited: 2, created: 1 },
    },
    error_summary: null,
    created_at: '2026-08-26T00:00:00Z',
    started_at: null,
    finished_at: null,
  }
  const proposal: AiProposalRow = {
    proposal_id: 'p1',
    proposal_type: 'METADATA',
    impact_level: 'LOW',
    status: 'PENDING',
    profile: 'library_curator',
    job_id: 'job-1',
    summary: '…',
    summary_parts: { why: '缺少主题', what: '补充海洋主题', impact: '更容易找到' },
    changes: { topics_add: ['海洋'] },
    policy_diff: null,
    entity_id: 'e1',
    entity_title: '汪汪队',
    created_at: null,
    applied_at: null,
  }
  return {
    adminApi: {
      aiJobs: vi.fn().mockResolvedValue({ items: [doneJob] }),
      aiJob: vi.fn().mockResolvedValue(doneJob),
      aiJobCreate: vi.fn().mockResolvedValue({ job_id: 'job-2', state: 'queued' }),
      aiProposals: vi.fn().mockResolvedValue({ items: [proposal] }),
      aiProposalApply: vi.fn().mockResolvedValue({ proposal_id: 'p1', status: 'applied' }),
      aiProposalReject: vi.fn().mockResolvedValue({ proposal_id: 'p1', status: 'REJECTED' }),
      aiProposalsBatchApply: vi
        .fn()
        .mockResolvedValue({ results: [{ proposal_id: 'p1', status: 'applied' }], note: '' }),
    },
  }
})

import { AiCurateDrawer } from './AiCurateDrawer'

describe('AiCurateDrawer（AIA-001/002 媒体库 AI 整理）', () => {
  it('打开即呈现最近任务的无副作用发现（不要求确认）与待处理建议卡', async () => {
    render(<AiCurateDrawer open onClose={vi.fn()} />)
    expect(await screen.findByText('整理发现')).toBeInTheDocument()
    expect(screen.getByText('《汪汪队》疑似重复归组')).toBeInTheDocument()
    expect(screen.getByText('可以完善 1 个内容')).toBeInTheDocument()
  })

  it('完成后呈现“本次整理依据”（检查范围/锁定跳过/不读观看历史）', async () => {
    render(<AiCurateDrawer open onClose={vi.fn()} />)
    expect(await screen.findByText('本次整理依据')).toBeInTheDocument()
    expect(screen.getByText(/共检查 2 个内容/)).toBeInTheDocument()
    expect(screen.getByText(/全程未读取观看历史与文件路径/)).toBeInTheDocument()
  })

  it('运行中显示过程阶段与进度计数（不只一个百分比）', async () => {
    const { adminApi } = await import('../../api/admin')
    ;(adminApi.aiJobs as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [
        {
          job_id: 'job-run',
          job_type: 'CATALOG_AUDIT',
          state: 'running',
          progress: 0.5,
          result_summary: {
            stage_note: '正在分析第 1/2 批内容',
            processed: 50,
            total: 100,
            counts: { audited: 100, created: 12, created_high: 2 },
          },
          error_summary: null,
          created_at: null,
          started_at: null,
          finished_at: null,
        },
      ],
    })
    render(<AiCurateDrawer open onClose={vi.fn()} />)
    expect(await screen.findByText('正在分析第 1/2 批内容')).toBeInTheDocument()
    expect(screen.getByText(/已检查 50\/100 个内容/)).toBeInTheDocument()
    expect(screen.getByText(/已生成 14 条建议（2 条需单独确认）/)).toBeInTheDocument()
  })

  it('运行中已生成的建议逐条流入呈现（只读，整理完成后才可应用）', async () => {
    const { adminApi } = await import('../../api/admin')
    ;(adminApi.aiJobs as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [
        {
          job_id: 'job-run2',
          job_type: 'CATALOG_AUDIT',
          state: 'running',
          progress: 0.4,
          result_summary: {
            stage_note: '正在分析第 2/5 批内容',
            processed: 100,
            total: 250,
            counts: { audited: 250, created: 1 },
          },
          error_summary: null,
          created_at: null,
          started_at: null,
          finished_at: null,
        },
      ],
    })
    render(<AiCurateDrawer open onClose={vi.fn()} />)
    // 轮询周期后建议卡出现（流入），且提示"整理完成后即可应用"
    expect(await screen.findByText('可以完善 1 个内容', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByText('以下建议已生成，整理完成后即可应用：')).toBeInTheDocument()
    // antd v6 loading 按钮不设 disabled 属性（内部拦截点击），以 loading 态为准
    const apply = screen.getByRole('button', { name: /应用安全修改/ })
    expect(apply.className).toContain('ant-btn-loading')
  })

  it('批量应用安全修改调用 batch-apply 并回调刷新', async () => {
    const onChanged = vi.fn()
    render(<AiCurateDrawer open onClose={vi.fn()} onChanged={onChanged} />)
    await screen.findByText('可以完善 1 个内容')
    await userEvent.click(screen.getByRole('button', { name: '应用安全修改' }))
    const { adminApi } = await import('../../api/admin')
    await waitFor(() => expect(adminApi.aiProposalsBatchApply).toHaveBeenCalledWith(['p1']))
    expect(onChanged).toHaveBeenCalled()
  })

  it('忽略建议调用 reject', async () => {
    render(<AiCurateDrawer open onClose={vi.fn()} />)
    await screen.findByText('可以完善 1 个内容')
    await userEvent.click(screen.getByRole('button', { name: /忽略/ }))
    const { adminApi } = await import('../../api/admin')
    await waitFor(() => expect(adminApi.aiProposalReject).toHaveBeenCalledWith('p1'))
  })
})
