import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AiJobRow } from '../types/admin'

// vi.mock 工厂会被提升到文件顶部：数据构造必须放进工厂内部（hoisting TDZ）。
vi.mock('../api/admin', () => {
  const doneJob: AiJobRow = {
    job_id: 'job-1',
    job_type: 'USAGE_SUMMARY',
    state: 'done',
    progress: 1,
    result_summary: {
      headlines: ['最近一周海洋主题接触最多', '当前屏幕时间规则运行正常'],
      summary_text: ['娱乐视频 213 分钟，音频 40 分钟', '成长接力发起 6 次，接受 4 次'],
      counts: {},
    },
    error_summary: null,
    created_at: '2026-08-26T00:00:00Z',
    started_at: null,
    finished_at: null,
  }
  return {
    adminApi: {
      aiJobs: vi.fn().mockResolvedValue({ items: [doneJob] }),
      aiJob: vi.fn().mockResolvedValue(doneJob),
      aiJobCreate: vi.fn().mockResolvedValue({ job_id: 'job-2', state: 'queued' }),
    },
  }
})

import { KindoAiCard } from './KindoAiCard'

describe('KindoAiCard 概览摘要卡（交互 §8.2 / AIA-003）', () => {
  it('呈现最近一次摘要的 headline（只放值得关注的信息）', async () => {
    render(<KindoAiCard />)
    expect(await screen.findByText('最近一周海洋主题接触最多')).toBeInTheDocument()
    expect(screen.getByText('当前屏幕时间规则运行正常')).toBeInTheDocument()
  })

  it('查看详细情况展开摘要全文，再点收起', async () => {
    render(<KindoAiCard />)
    await screen.findByText('最近一周海洋主题接触最多')
    await userEvent.click(screen.getByRole('button', { name: '查看详细情况' }))
    expect(screen.getByText(/娱乐视频 213 分钟/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '收起详细情况' }))
    expect(screen.queryByText(/娱乐视频 213 分钟/)).toBeNull()
  })
})

describe('KindoAiCard 首次触发（S2 评审 M-2）', () => {
  it('无历史任务时点"生成摘要"直接创建任务，而不是只刷新', async () => {
    const { adminApi } = await import('../api/admin')
    const mockJobs = adminApi.aiJobs as ReturnType<typeof vi.fn>
    mockJobs.mockResolvedValueOnce({ items: [] })
    render(<KindoAiCard />)
    const btn = await screen.findByRole('button', { name: '生成使用摘要' })
    await userEvent.click(btn)
    await waitFor(() => expect(adminApi.aiJobCreate).toHaveBeenCalledWith('USAGE_SUMMARY'))
  })
})
