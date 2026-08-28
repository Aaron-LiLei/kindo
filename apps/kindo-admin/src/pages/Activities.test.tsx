import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActivitiesPage } from './Activities'
import type { TransitionActivityRow } from '../types/admin'

const rows: TransitionActivityRow[] = [
  {
    id: 'a1',
    title: '小小海洋学家',
    summary: '找一个圆的东西当贝壳',
    topics_json: ['海洋'],
    age_min: 3,
    age_max: 6,
    source: 'builtin',
    status: 'preset',
  },
  {
    id: 'a2',
    title: 'AI 生成的活动',
    summary: '画出刚才那集里的小海龟',
    topics_json: [],
    source: 'generated',
    status: 'draft',
  },
  {
    id: 'a3',
    title: '家长自建',
    summary: '',
    topics_json: ['数字'],
    source: 'parent',
    status: 'published',
  },
]

vi.mock('../hooks/useApi', () => ({
  useApi: () => ({
    data: { items: rows },
    error: null,
    loading: false,
    reload: vi.fn(),
  }),
}))

describe('ActivitiesPage', () => {
  it('列出内置 / AI 草稿 / 自建并带状态徽章（ADM-014）', () => {
    render(<ActivitiesPage />)
    expect(screen.getByText('小小海洋学家')).toBeInTheDocument()
    expect(screen.getByText('AI 生成的活动')).toBeInTheDocument()
    expect(screen.getByText('家长自建')).toBeInTheDocument()
    expect(screen.getByText('草稿')).toBeInTheDocument()
    expect(screen.getByText('已发布')).toBeInTheDocument()
    expect(screen.getByText('内置')).toBeInTheDocument()
    // 草稿行有“发布”入口
    expect(screen.getAllByText('发布').length).toBeGreaterThan(0)
  })
})
