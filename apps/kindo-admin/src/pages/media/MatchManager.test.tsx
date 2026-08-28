import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { App as AntApp } from 'antd'
import { describe, expect, it, vi } from 'vitest'
import { MatchManager } from './MatchManager'

// AntApp.useApp() 的 message 在无 Provider 时是残缺桩（success 非函数）→ 包真 Provider
const renderUi = () => render(<AntApp><MatchManager /></AntApp>)

// useApi 打桩：按 path 分流（overview / 全局决策时间线）。
// vi.mock 工厂被提升到文件顶部——数据构造必须放进工厂内部（hoisting TDZ）。
vi.mock('../../hooks/useApi', () => {
  const overview = {
    counts: { confirmed: 2, auto: 1, no_match: 1 },
    pending: [
      {
        entity_id: 'e1',
        entity_type: 'series',
        title: '小鼠波波',
        match_status: 'pending',
        candidates: [
          { ref_id: 't1', title: '小鼠波波 Maisy', first_air_date: '2007-01-01', confidence: 'likely' },
          { ref_id: 't2', title: '波波和朋友们', confidence: 'fuzzy' },
        ],
      },
      {
        entity_id: 'e2',
        entity_type: 'movie',
        title: '另一部电影',
        match_status: 'pending',
        candidates: [{ ref_id: 't3', title: '另一部电影（2020）', confidence: 'exact' }],
      },
    ],
    no_candidates: [
      { entity_id: 'e9', entity_type: 'series', title: '自然拼读儿歌合集' },
    ],
  }
  const recentDecisions = [
    {
      entity_id: 'e0',
      entity_title: '汪汪队立大功',
      entity_type: 'series',
      provider: 'tmdb',
      candidate: { ref_id: 't0', title: '汪汪队立大功 PAW Patrol' },
      confidence: 'exact',
      decision: 'parent_confirm',
      decided_by: 'parent',
      created_at: '2026-08-26T10:00:00Z',
    },
  ]
  return {
    useApi: (path: string | null) => {
      if (!path) return { data: undefined, error: null, loading: true, reload: vi.fn() }
      if (path.includes('/match/decisions/recent')) {
        return { data: { decisions: recentDecisions }, error: null, loading: false, reload: vi.fn() }
      }
      if (path.includes('/match/overview')) {
        return { data: overview, error: null, loading: false, reload: vi.fn() }
      }
      return { data: undefined, error: null, loading: false, reload: vi.fn() }
    },
  }
})

const confirmMock = vi.fn().mockResolvedValue({ entity_id: 'e1', match_status: 'confirmed' })
vi.mock('../../api/admin', () => ({
  adminApi: {
    matchConfirm: (...args: unknown[]) => confirmMock(...args),
    matchSearch: vi.fn().mockResolvedValue({ candidates: [] }),
  },
}))

describe('MatchManager（批量确认与决策时间线）', () => {
  it('渲染待确认列表与候选、无候选分区', () => {
    renderUi()
    expect(screen.getByText('小鼠波波')).toBeInTheDocument()
    // 实体标题（精确匹配，不与候选按钮"另一部电影（2020）"混淆）
    expect(screen.getByText('另一部电影')).toBeInTheDocument()
    expect(screen.getByText(/自然拼读儿歌合集/)).toBeInTheDocument()
  })

  it('勾选待确认项后出现批量按钮，批量确认对每项采用第一候选', async () => {
    renderUi()
    // 全选 → 批量确认按钮启用并显示数量
    fireEvent.click(screen.getByText(/全选/))
    const batchBtn = screen.getByText(/确认所选（2）/)
    expect(batchBtn).toBeInTheDocument()
    // Popconfirm 浮层异步渲染；antd 对两字按钮自动插空格（"确 认"），与
    // 触发按钮"确认所选（2）"区分需精确匹配
    fireEvent.click(batchBtn)
    const okBtn = await screen.findByRole('button', { name: '确 认' })
    fireEvent.click(okBtn)
    await waitFor(() => {
      // 两个实体各以其第一候选确认（Maisy / 另一部电影（2020））
      expect(confirmMock).toHaveBeenCalledWith('e1', expect.objectContaining({ ref_id: 't1' }))
      expect(confirmMock).toHaveBeenCalledWith('e2', expect.objectContaining({ ref_id: 't3' }))
    })
  })

  it('决策时间线展示最近的家长确认记录（ADM-012）', async () => {
    renderUi()
    fireEvent.click(screen.getByText(/决策记录/))
    expect(await screen.findByText('汪汪队立大功')).toBeInTheDocument()
    expect(screen.getByText(/PAW Patrol/)).toBeInTheDocument()
    expect(screen.getByText('家长确认')).toBeInTheDocument()
  })
})
